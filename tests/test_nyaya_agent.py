"""The Nyaya agentic loop: ingest facts, select contracts, judge each element, quote-check, assemble the REG
wire deterministically, Lean-check it, decide PROOF / DENIAL / ABSTAIN / REFER_TO_LAWYER, audit every step.

The judge is a test double (scripted per element) and lives only in this file. The Lean side is the REAL
pinned `score` binary where it is built (`requires_score_bin`), and a scripted registry double elsewhere, so
the decision logic is covered on a host without the sibling checkout too. All facts are hand-written toy text.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from pravrudhi.application import nyaya_lean_registry as reg
from pravrudhi.application.nyaya_agent import (
    AgentConfig,
    BinaryRegistry,
    BinaryShaMismatch,
    NyayaAgent,
    assemble_assertions,
    expected_outcome,
    ingest_facts,
    load_agent_config,
    outcome_from_lean,
    select_contracts,
)
from pravrudhi.application.nyaya_judges import AndGateJudge, ElementJudgment, JudgeOutputError, JudgeRequest

REPO = Path(__file__).resolve().parent.parent
#: The pinned binary lives outside this repo; point PRABHASA_NYAYA_SCORE_BIN at it (no host path is committed).
SCORE_BIN = Path(os.environ.get("PRABHASA_NYAYA_SCORE_BIN", "prabhasa-nyaya-score-not-configured"))
PINNED = "29f6eaed3ef5c548d6c8a1cdf884c9a73895937132779ea4d4cffb8e66cb80ae"
requires_score_bin = pytest.mark.skipif(
    not SCORE_BIN.exists(), reason=f"the pinned prabhasa-nyaya score binary is not built on this host ({SCORE_BIN})"
)
_FIXTURES = Path(__file__).parent / "fixtures" / "nyaya_score"

# -- toy fact pattern (hand-written; not from any evaluation set) -------------------------------------------
TOY_FACTS = [
    "TOY: Kiran was engaged to Lata and told her he would marry her in the spring.",
    "TOY: Kiran had already decided never to marry Lata when he made that promise.",
    "TOY: Relying on the promise, Lata had sexual intercourse with Kiran.",
]
BNS69_EL = [
    "induces the woman by deceitful means (inducement for, or false promise of, employment or promotion, or marrying "
    "by suppressing identity), or by a promise to marry made without any intention of fulfilling it",
    "sexual intercourse with the woman actually occurs",
]
BNS69_DENY = "the sexual intercourse amounts to the offence of rape"


def _est(fid: str, text: str, quote: str, p: float = 0.97) -> ElementJudgment:
    assert quote in text  # a toy-fixture typo must fail here, not look like a rejected quote
    return ElementJudgment("established", p, fid, quote, "model")


def _bad(fid: str, quote: str, p: float = 0.97) -> ElementJudgment:
    return ElementJudgment("established", p, fid, quote, "model")


def _not(p: float = 0.03) -> ElementJudgment:
    return ElementJudgment("not_established", p)


@dataclass
class ScriptedJudge:
    """Test double: a queue of judgments (or exceptions) per element name, recording every request."""

    script: dict[str, list[ElementJudgment | Exception]]
    name: str = "scripted"
    requests: list[JudgeRequest] = field(default_factory=list)

    def judge(self, request: JudgeRequest) -> ElementJudgment:
        self.requests.append(request)
        queue = self.script[request.element]
        item = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(item, Exception):
            raise item
        return item


@dataclass
class ScriptedRegistry:
    """Test double for the Lean side, answering the way `score` does (verified against the real binary in
    `TestRealBinary` below)."""

    contracts: dict[str, reg.DescribedContract]
    sources: dict[str, list[str]]
    sha256: str = "scripted"
    checks: list[tuple[str, dict[str, bool]]] = field(default_factory=list)

    def list_contracts(self) -> dict[str, list[str]]:
        return dict(self.sources)

    def describe(self, contract_id: str) -> reg.DescribedContract:
        return self.contracts[contract_id]

    def source_text(self, contract_id: str) -> str:
        return f"RETRIEVED statute text for {contract_id}"

    def check(self, assertions: Mapping[str, bool], contract_id: str) -> dict[str, Any]:
        self.checks.append((contract_id, dict(assertions)))
        c = self.contracts[contract_id]
        met = {k for k, v in assertions.items() if v}
        denied = [e for e in c.denials if e in met]
        omitted = [e for e in c.elements if e not in met]
        unlicensed = [e for e in met if e not in c.elements and e not in c.denials]
        flagged = denied or omitted or unlicensed
        return {
            "verdict": "flagged" if flagged else "grounded",
            "denied_claims": denied,
            "unlicensed_claims": unlicensed,
            "omitted_claims": omitted,
        }


def _registry() -> ScriptedRegistry:
    return ScriptedRegistry(
        contracts={
            "bns69": reg.DescribedContract("bns69", list(BNS69_EL), [BNS69_DENY]),
            "bns85": reg.DescribedContract("bns85", ["husband element", "cruelty element"], []),
        },
        sources={
            "bns69": ["Bharatiya Nyaya Sanhita §69"],
            "bns85": ["Bharatiya Nyaya Sanhita §85"],
            "ipc999_unknown": ["Nowhere §999"],
        },
    )


def _config(tmp_path: Path, **over: Any) -> AgentConfig:
    base: dict[str, Any] = {
        "tau": 0.74,
        "refer_band": (0.5, 0.74),
        "max_retries": 2,
        "audit_dir": tmp_path / "audit",
        "judge_statute_text": {"bns69": "TRAINING statute text for bns69"},
    }
    base.update(over)
    return AgentConfig(**base)


def _proof_script(facts: list[str]) -> dict[str, list[ElementJudgment | Exception]]:
    return {
        BNS69_EL[0]: [_est("F2", facts[1], "never to marry Lata")],
        BNS69_EL[1]: [_est("F3", facts[2], "Lata had sexual intercourse with Kiran")],
        BNS69_DENY: [_not()],
    }


def _run(
    tmp_path: Path, script: dict[str, list[ElementJudgment | Exception]], **cfg: Any
) -> tuple[Any, ScriptedJudge, ScriptedRegistry]:
    judge = ScriptedJudge(script)
    registry = _registry()
    agent = NyayaAgent(judge, registry, _config(tmp_path, **cfg))
    return agent.run(TOY_FACTS, narrative="TOY narrative.", contract_ids=["bns69"]), judge, registry


class TestIngest:
    def test_numbers_facts_and_hashes_each(self) -> None:
        facts = ingest_facts(["  a fact ", "another"])
        assert [f.id for f in facts] == ["F1", "F2"]
        assert facts[0].text == "a fact"
        assert facts[0].sha256 == hashlib.sha256(b"a fact").hexdigest()

    def test_empty_fact_is_refused(self) -> None:
        with pytest.raises(ValueError):
            ingest_facts(["ok", "   "])

    def test_no_facts_is_refused(self) -> None:
        with pytest.raises(ValueError):
            ingest_facts([])


class TestSelectContracts:
    LISTED = reg.parse_list_contracts((_FIXTURES / "29f6eaed_list_contracts.txt").read_text())

    def test_default_is_every_listed_contract_the_checker_knows_in_binary_order(self) -> None:
        chosen = select_contracts(self.LISTED)
        # Nothing is excluded since 0.5.28, so every listed contract is chosen, in the binary's own order.
        assert chosen == list(self.LISTED)
        assert len(chosen) == 26
        assert {"bnss187_extended_serious", "bnss187_extended_other", "ni138"} <= set(chosen)

    def test_named_ids_keep_the_binary_order_not_the_callers(self) -> None:
        assert select_contracts(self.LISTED, contract_ids=["bns85", "ipc416"]) == ["ipc416", "bns85"]

    def test_unknown_named_id_is_refused(self) -> None:
        with pytest.raises(reg.UnknownContractError):
            select_contracts(self.LISTED, contract_ids=["ipc999"])

    def test_bnss187_is_selectable_since_its_conduct_fix(self) -> None:
        # Excluded in 0.5.26 for a non-standard conduct entity; fixed in prabhasa-nyaya 8d9f0af.
        assert select_contracts(self.LISTED, contract_ids=["bnss187_extended_serious"]) == ["bnss187_extended_serious"]

    def test_an_id_the_binary_does_not_list_is_refused_not_dropped(self) -> None:
        with pytest.raises(reg.UnknownContractError):
            select_contracts(self.LISTED, contract_ids=["bns999_not_a_contract"])

    def test_known_contract_id_is_selected(self) -> None:
        # bns316 is now KNOWN (previously was in binary but not known)
        assert select_contracts(self.LISTED, contract_ids=["bns316_misappropriation"]) == ["bns316_misappropriation"]

    def test_by_section_matches_the_source_column(self) -> None:
        assert select_contracts(self.LISTED, sections=["Bharatiya Nyaya Sanhita §86"]) == ["bns85"]
        assert select_contracts(self.LISTED, sections=["Indian Penal Code §405"]) == [
            "ipc405_misappropriation",
            "ipc405_use_or_disposal",
            "ipc405_wilfully_suffers",
        ]

    def test_section_matching_nothing_is_refused(self) -> None:
        with pytest.raises(reg.UnknownContractError):
            select_contracts(self.LISTED, sections=["Indian Penal Code §1"])


class TestAssembly:
    C = reg.DescribedContract("bns69", list(BNS69_EL), [BNS69_DENY])

    def test_denial_is_asserted_only_when_established(self) -> None:
        a = assemble_assertions(self.C, {BNS69_EL[0]: True, BNS69_EL[1]: True, BNS69_DENY: False})
        assert a == {BNS69_EL[0]: True, BNS69_EL[1]: True}
        a = assemble_assertions(self.C, {BNS69_EL[0]: True, BNS69_EL[1]: False, BNS69_DENY: True})
        assert a == {BNS69_EL[0]: True, BNS69_EL[1]: False, BNS69_DENY: True}

    def test_assembly_is_deterministic_in_contract_order(self) -> None:
        a = assemble_assertions(self.C, {BNS69_DENY: False, BNS69_EL[1]: True, BNS69_EL[0]: True})
        assert list(a) == BNS69_EL

    @pytest.mark.parametrize(
        ("established", "outcome"),
        [
            ({BNS69_EL[0]: True, BNS69_EL[1]: True, BNS69_DENY: False}, "PROOF"),
            ({BNS69_EL[0]: True, BNS69_EL[1]: False, BNS69_DENY: False}, "ABSTAIN"),
            ({BNS69_EL[0]: True, BNS69_EL[1]: True, BNS69_DENY: True}, "DENIAL"),
            ({BNS69_EL[0]: False, BNS69_EL[1]: False, BNS69_DENY: True}, "DENIAL"),
        ],
    )
    def test_expected_outcome_mirrors_the_element_first_harness(self, established: dict[str, bool], outcome: str) -> None:
        assert expected_outcome(self.C, assemble_assertions(self.C, established)) == outcome

    def test_outcome_from_lean(self) -> None:
        assert (
            outcome_from_lean({"verdict": "grounded", "denied_claims": [], "unlicensed_claims": [], "omitted_claims": []})
            == "PROOF"
        )
        assert (
            outcome_from_lean({"verdict": "flagged", "denied_claims": ["d"], "unlicensed_claims": [], "omitted_claims": ["o"]})
            == "DENIAL"
        )
        assert (
            outcome_from_lean({"verdict": "flagged", "denied_claims": [], "unlicensed_claims": [], "omitted_claims": ["o"]})
            == "ABSTAIN"
        )
        with pytest.raises(RuntimeError):
            outcome_from_lean({"verdict": "flagged", "denied_claims": [], "unlicensed_claims": ["u"], "omitted_claims": []})


class TestLoop:
    def test_proof_when_every_element_is_established_with_a_verbatim_quote(self, tmp_path: Path) -> None:
        run, judge, registry = _run(tmp_path, _proof_script(TOY_FACTS))
        (c,) = run.contracts
        assert c.outcome == "PROOF"
        assert c.lean is not None and c.lean["verdict"] == "grounded"
        assert registry.checks == [("bns69", {BNS69_EL[0]: True, BNS69_EL[1]: True})]
        assert [e.attempts for e in c.elements] == [1, 1, 1]

    def test_offsets_are_computed_by_the_system(self, tmp_path: Path) -> None:
        run, _, _ = _run(tmp_path, _proof_script(TOY_FACTS))
        el0 = run.contracts[0].elements[0]
        assert (el0.fact_id, el0.quote) == ("F2", "never to marry Lata")
        assert TOY_FACTS[1][el0.start : el0.end] == "never to marry Lata"  # type: ignore[misc]
        assert (el0.offsets_source, el0.occurrences) == ("system", 1)

    def test_multiple_occurrences_take_the_first_and_are_counted(self, tmp_path: Path) -> None:
        script = _proof_script(TOY_FACTS)
        script[BNS69_EL[0]] = [_est("F2", TOY_FACTS[1], "a")]
        run, _, _ = _run(tmp_path, script)
        el0 = run.contracts[0].elements[0]
        assert el0.start == TOY_FACTS[1].find("a")
        assert el0.occurrences == TOY_FACTS[1].count("a")
        assert el0.occurrences > 1

    def test_every_attempt_uses_the_same_training_statute_never_the_binary_text(self, tmp_path: Path) -> None:
        script = _proof_script(TOY_FACTS)
        script[BNS69_EL[0]] = [_bad("F2", "never to wed Lata"), script[BNS69_EL[0]][0]]
        run, judge, _ = _run(tmp_path, script)
        el0 = [r for r in judge.requests if r.element == BNS69_EL[0]]
        assert [r.statute for r in el0] == ["TRAINING statute text for bns69"] * 2
        assert not any("RETRIEVED" in r.statute for r in judge.requests)
        assert run.contracts[0].elements[0].attempts == 2
        assert run.contracts[0].outcome == "PROOF"

    def test_a_retry_only_re_asks_for_the_quote_status_and_p_stand(self, tmp_path: Path) -> None:
        """Attempt 1 decides status and p_established; a retry supplies only a quote. A retry that now says
        not_established (or a different p) cannot flip the element -- it simply gave no quote."""
        script = _proof_script(TOY_FACTS)
        script[BNS69_EL[1]] = [_bad("F3", "Lata slept with Kiran", p=0.97), _not(p=0.6)]
        run, _, _ = _run(tmp_path, script)
        el1 = run.contracts[0].elements[1]
        assert el1.p_established == 0.97  # never the retry's 0.6 -> never in the refer band
        assert el1.claimed is True
        assert el1.status == "not_established"  # no verbatim quote in 3 attempts
        assert el1.attempts == 3
        assert run.contracts[0].outcome == "ABSTAIN"

    def test_a_retry_that_supplies_a_verbatim_quote_keeps_attempt_ones_p(self, tmp_path: Path) -> None:
        script = _proof_script(TOY_FACTS)
        good = _est("F3", TOY_FACTS[2], "Lata had sexual intercourse with Kiran", p=0.55)
        script[BNS69_EL[1]] = [_bad("F3", "Lata slept with Kiran", p=0.97), good]
        run, _, _ = _run(tmp_path, script)
        el1 = run.contracts[0].elements[1]
        assert (el1.status, el1.p_established, el1.quote) == ("established", 0.97, good.quote)
        assert run.contracts[0].outcome == "PROOF"

    def test_facts_reach_the_judge_as_numbered_pairs(self, tmp_path: Path) -> None:
        _, judge, _ = _run(tmp_path, _proof_script(TOY_FACTS))
        assert judge.requests[0].facts == tuple((f"F{i + 1}", t) for i, t in enumerate(TOY_FACTS))
        assert judge.requests[0].narrative == "TOY narrative."

    def test_non_verbatim_quote_after_retries_is_not_established_never_repaired(self, tmp_path: Path) -> None:
        script = _proof_script(TOY_FACTS)
        script[BNS69_EL[1]] = [_bad("F3", "Lata slept with Kiran", p=0.99)]
        run, judge, registry = _run(tmp_path, script)
        c = run.contracts[0]
        el1 = c.elements[1]
        assert el1.status == "not_established"
        assert el1.quote_check == "quote_not_found"
        assert el1.start is None and el1.end is None
        assert el1.attempts == 3  # first + max_retries
        assert c.outcome == "ABSTAIN"
        assert registry.checks[0][1] == {BNS69_EL[0]: True, BNS69_EL[1]: False}

    def test_retry_budget_is_config(self, tmp_path: Path) -> None:
        script = _proof_script(TOY_FACTS)
        script[BNS69_EL[1]] = [_bad("F9", "x")]
        run, judge, _ = _run(tmp_path, script, max_retries=0)
        assert run.contracts[0].elements[1].attempts == 1
        assert run.contracts[0].elements[1].quote_check == "unknown_fact"

    def test_not_established_is_never_retried(self, tmp_path: Path) -> None:
        script = _proof_script(TOY_FACTS)
        script[BNS69_EL[1]] = [_not()]
        run, judge, _ = _run(tmp_path, script)
        assert sum(r.element == BNS69_EL[1] for r in judge.requests) == 1
        assert run.contracts[0].outcome == "ABSTAIN"
        assert run.contracts[0].reason == "missing_element"

    def test_denial_established_is_a_denial(self, tmp_path: Path) -> None:
        script = _proof_script(TOY_FACTS)
        script[BNS69_DENY] = [_est("F3", TOY_FACTS[2], "sexual intercourse")]
        run, _, registry = _run(tmp_path, script)
        assert run.contracts[0].outcome == "DENIAL"
        assert registry.checks[0][1][BNS69_DENY] is True

    def test_denial_claimed_but_unquotable_refers_rather_than_proves(self, tmp_path: Path) -> None:
        """A defeater the judge says is present but cannot quote must not quietly become 'no defeater' and
        let the contract prove: that would turn a failed quote into a positive finding."""
        script = _proof_script(TOY_FACTS)
        script[BNS69_DENY] = [_bad("F3", "invented words", p=0.95)]
        run, _, _ = _run(tmp_path, script)
        c = run.contracts[0]
        assert c.outcome == "REFER_TO_LAWYER"
        assert c.reason == "denial_unquotable"
        assert c.elements[2].status == "not_established"

    def test_uncertain_band_refers(self, tmp_path: Path) -> None:
        script = _proof_script(TOY_FACTS)
        script[BNS69_EL[1]] = [_not(p=0.6)]
        run, _, registry = _run(tmp_path, script)
        c = run.contracts[0]
        assert c.outcome == "REFER_TO_LAWYER"
        assert c.reason == "uncertain"
        assert c.uncertain == [BNS69_EL[1]]
        assert c.lean is not None and c.lean_outcome == "ABSTAIN"  # still checked and recorded

    def test_band_is_half_open(self, tmp_path: Path) -> None:
        script = _proof_script(TOY_FACTS)
        script[BNS69_EL[1]] = [_est("F3", TOY_FACTS[2], "Lata had sexual intercourse with Kiran", p=0.74)]
        run, _, _ = _run(tmp_path, script)
        assert run.contracts[0].outcome == "PROOF"

    def test_judge_failure_after_retries_abstains_without_a_lean_call(self, tmp_path: Path) -> None:
        script = _proof_script(TOY_FACTS)
        script[BNS69_EL[0]] = [JudgeOutputError("unreadable")]
        run, judge, registry = _run(tmp_path, script)
        c = run.contracts[0]
        assert c.outcome == "ABSTAIN"
        assert c.reason == "judge_error"
        assert c.elements[0].error is not None and "unreadable" in c.elements[0].error
        assert c.elements[0].attempts == 3
        assert registry.checks == []

    def test_transport_error_is_retried_then_recorded(self, tmp_path: Path) -> None:
        script = _proof_script(TOY_FACTS)
        script[BNS69_EL[0]] = [RuntimeError("connection refused"), script[BNS69_EL[0]][0]]
        run, _, _ = _run(tmp_path, script)
        assert run.contracts[0].outcome == "PROOF"
        assert run.contracts[0].elements[0].attempts == 2

    def test_contract_without_a_training_statute_abstains_without_judging(self, tmp_path: Path) -> None:
        """The binary's text is never fed to the judge, so a contract with no training text is not judged."""
        judge = ScriptedJudge({})
        run = NyayaAgent(judge, _registry(), _config(tmp_path)).run(TOY_FACTS, contract_ids=["bns85"])
        c = run.contracts[0]
        assert (c.outcome, c.reason) == ("ABSTAIN", "no_training_statute_text")
        assert judge.requests == []


def _logit(p: float) -> float:
    """Duplicates `nyaya_agent._logit`'s definition (clamp then log-odds) so a test can compute an exact
    boundary value without reaching into the module's private helper."""
    p = min(max(p, 1e-9), 1.0 - 1e-9)
    return math.log(p / (1.0 - p))


def _second(status: str, p: float) -> ElementJudgment:
    """A second-judge reply as a bare `Judge` double would give it -- fact_id/quote are never read from the
    second (AndGateJudge always keeps the primary's span), so they are dummy values here."""
    return ElementJudgment(status, p, fact_id="Fx", quote="unused")


class TestSecondJudgeReferBand:
    """Config C's own REFER band (module doc, "the second judge gets its own band"): a logit-distance test on
    the second judge's `p_established_second` against ITS tau, `second_judge.refer_logit_delta` (env
    `NYAYA_SECOND_JUDGE_REFER_LOGIT_DELTA`). Wraps two `ScriptedJudge` doubles in a REAL `AndGateJudge` (never
    hand-builds an `ElementJudgment` with second-judge fields) so the gate's own skip/fail-closed logic is
    exercised exactly as production wires it, never re-implemented here."""

    TAU2 = 0.97

    def _run(
        self, tmp_path: Path, primary_script: dict[str, list[Any]], second_script: dict[str, list[Any]],
        *, delta: float | None = 0.2, **cfg: Any,
    ) -> Any:
        primary = ScriptedJudge(primary_script)
        second = ScriptedJudge(second_script)
        gate = AndGateJudge(primary, second, tau_primary=0.74, tau_second=self.TAU2)
        second_judge_cfg = None if delta is None else {"tau": self.TAU2, "refer_logit_delta": delta}
        config = _config(tmp_path, second_judge=second_judge_cfg, **cfg)
        agent = NyayaAgent(gate, _registry(), config)
        return agent.run(TOY_FACTS, narrative="TOY narrative.", contract_ids=["bns69"]).contracts[0]

    def test_off_by_default_is_byte_identical(self, tmp_path: Path) -> None:
        """No `second_judge` config at all (delta=None): the second is still consulted (the gate is real) and
        its p is still recorded, but the band never fires -- outcome is exactly what it would be without any
        of this feature."""
        script = _proof_script(TOY_FACTS)
        c = self._run(tmp_path, script, {BNS69_EL[0]: [_second("established", 0.975)],
                                         BNS69_EL[1]: [_second("established", 0.99)]}, delta=None)
        assert c.outcome == "PROOF"
        assert c.uncertain_second == []
        el0 = c.elements[0]
        assert el0.p_established_second == 0.975  # consulted and recorded...
        assert el0.second_refer_band_fired is False  # ...but the band is off, so it never fires

    @pytest.mark.parametrize("p2", [0.975, 0.965])  # above and below tau=0.97
    def test_within_delta_on_either_side_of_tau_refers(self, tmp_path: Path, p2: float) -> None:
        script = _proof_script(TOY_FACTS)
        c = self._run(tmp_path, script, {BNS69_EL[0]: [_second("established", p2)],
                                         BNS69_EL[1]: [_second("established", 0.999)]}, delta=0.2)
        assert abs(_logit(p2) - _logit(self.TAU2)) < 0.2  # the test's own premise
        assert c.outcome == "REFER_TO_LAWYER"
        assert c.reason == "uncertain_second_judge"
        assert c.uncertain_second == [BNS69_EL[0]]
        assert c.uncertain == []  # the primary's own band never fired

    def test_refer_band_still_fires_under_concurrency(self, tmp_path: Path) -> None:
        """The second-judge REFER band is computed from `ElementResult` fields `_judge_element` fills in
        regardless of which code path judged the element (nyaya_agent.py's concurrent branch calls the exact
        same `_judge_element`, just with a different `judge`/`audit` pair per task) -- so it must fire
        identically at `max_concurrency > 1`. `bns69` has 3 tasks (2 elements + 1 denial); `max_concurrency=4`
        exercises the concurrent branch (`workers = min(4, 3) = 3`) with every task judged at once."""
        p2 = 0.975
        script = _proof_script(TOY_FACTS)
        c = self._run(
            tmp_path, script,
            {BNS69_EL[0]: [_second("established", p2)], BNS69_EL[1]: [_second("established", 0.999)]},
            delta=0.2, max_concurrency=4,
        )
        assert c.outcome == "REFER_TO_LAWYER"
        assert c.reason == "uncertain_second_judge"
        assert c.uncertain_second == [BNS69_EL[0]]
        assert c.elements[0].second_refer_band_fired is True

    def test_boundary_distance_equal_delta_is_not_in_band(self, tmp_path: Path) -> None:
        """Strict `<`, never `<=` -- mirrors the primary band's own half-open convention."""
        p2 = 0.965
        delta = abs(_logit(p2) - _logit(self.TAU2))  # distance == delta exactly
        script = _proof_script(TOY_FACTS)
        c = self._run(tmp_path, script, {BNS69_EL[0]: [_second("established", p2)],
                                         BNS69_EL[1]: [_second("established", 0.999)]}, delta=delta)
        assert c.outcome == "PROOF"
        assert c.uncertain_second == []
        assert c.elements[0].second_refer_band_fired is False

    def test_beyond_delta_does_not_refer(self, tmp_path: Path) -> None:
        script = _proof_script(TOY_FACTS)
        c = self._run(tmp_path, script, {BNS69_EL[0]: [_second("established", 0.9999)],
                                         BNS69_EL[1]: [_second("established", 0.999)]}, delta=0.2)
        assert c.outcome == "PROOF"
        assert c.uncertain_second == []

    def test_primary_rejects_second_is_skipped_band_never_applies(self, tmp_path: Path) -> None:
        """An element the primary already rejects (second never asked) cannot be referred by the second-judge
        band, at any delta -- there is no second p to test. (EL0's own second is far outside the band here, so
        the contract's outcome turns only on EL1.)"""
        script = _proof_script(TOY_FACTS)
        script[BNS69_EL[1]] = [_not(p=0.3)]  # primary rejects outright, below its own tau 0.74
        c = self._run(tmp_path, script, {BNS69_EL[0]: [_second("established", 0.5)],
                                         BNS69_EL[1]: [_second("established", 0.99)]}, delta=0.2)
        el1 = c.elements[1]
        assert el1.p_established_second is None
        assert el1.second_skip_reason == "primary_not_established"
        assert el1.second_refer_band_fired is False
        assert c.uncertain_second == []
        assert c.outcome == "ABSTAIN" and c.reason == "missing_element"

    def test_second_unavailable_fails_closed_but_refers_the_contract(self, tmp_path: Path) -> None:
        """Lead-2, 2026-09-24: the second judge errors (unavailable) -- the ELEMENT still fails closed (not
        established, unchanged), and is never additionally referred by the logit-distance band -- there is no
        p to compare. But the CONTRACT now becomes REFER_TO_LAWYER instead of an ordinary DENIAL/ABSTAIN,
        because a fail-closed element is not the same as a genuinely-not-established one: the second opinion
        was never actually obtained. (EL0's own second is far outside the band here, so the contract's outcome
        turns only on EL1.)"""
        script = _proof_script(TOY_FACTS)
        c = self._run(tmp_path, script, {BNS69_EL[0]: [_second("established", 0.5)],
                                         BNS69_EL[1]: [ConnectionError("second judge unreachable")]}, delta=0.2)
        el1 = c.elements[1]
        assert el1.status == "not_established"
        assert el1.p_established_second is None
        assert el1.second_skip_reason is not None and "second_unavailable" in el1.second_skip_reason
        assert el1.second_refer_band_fired is False
        assert el1.second_unavailable is True
        assert c.uncertain_second == []
        assert c.unavailable_second == [BNS69_EL[1]]
        assert c.outcome == "REFER_TO_LAWYER" and c.reason == "second_judge_unavailable"

    def test_second_unavailable_refers_even_with_no_delta_configured(self, tmp_path: Path) -> None:
        """The unavailable->REFER rule does not depend on `second_judge.refer_logit_delta` being set at all --
        it fires purely off `second_skip_reason`, unconditionally, unlike the logit-distance band."""
        script = _proof_script(TOY_FACTS)
        c = self._run(tmp_path, script, {BNS69_EL[0]: [_second("established", 0.99)],
                                         BNS69_EL[1]: [ConnectionError("second judge unreachable")]}, delta=None)
        assert c.outcome == "REFER_TO_LAWYER" and c.reason == "second_judge_unavailable"
        assert c.unavailable_second == [BNS69_EL[1]]

    def test_second_unavailable_takes_priority_over_final_outcome_but_after_the_bands(self, tmp_path: Path) -> None:
        """When one element hits the logit-distance band AND another element's second judge is unavailable,
        the band's `uncertain_second_judge` reason wins (checked first) -- both lists are still populated, only
        the reported `reason` picks one, same composition rule as the primary/second band interaction above."""
        script = _proof_script(TOY_FACTS)
        c = self._run(tmp_path, script, {BNS69_EL[0]: [_second("established", 0.975)],  # inside delta=0.2 band
                                         BNS69_EL[1]: [ConnectionError("second judge unreachable")]}, delta=0.2)
        assert c.outcome == "REFER_TO_LAWYER"
        assert c.reason == "uncertain_second_judge"
        assert c.uncertain_second == [BNS69_EL[0]]
        assert c.unavailable_second == [BNS69_EL[1]]  # still recorded, just not the reported reason

    def test_records_p_second_tau_and_distance_on_the_element(self, tmp_path: Path) -> None:
        p2 = 0.975
        script = _proof_script(TOY_FACTS)
        c = self._run(tmp_path, script, {BNS69_EL[0]: [_second("established", p2)],
                                         BNS69_EL[1]: [_second("established", 0.999)]}, delta=0.2)
        el0 = c.elements[0]
        assert el0.p_established_second == p2
        assert el0.tau_second == self.TAU2
        assert el0.second_logit_distance == pytest.approx(abs(_logit(p2) - _logit(self.TAU2)))
        assert el0.second_refer_band_fired is True

    def test_composes_with_the_primarys_own_band_primary_takes_reason_priority(self, tmp_path: Path) -> None:
        """An element in BOTH bands at once (contrived, but the composition must be well-defined): the
        primary's `uncertain` reason wins, exactly as it would with no second judge configured at all -- the
        primary band's existing behaviour is unchanged by this feature being on."""
        script = _proof_script(TOY_FACTS)
        # attempt 1 is "established" (so the second is asked) with a valid quote, but its OWN p sits in the
        # primary's refer_band [0.5, 0.74) -- claimed/quote validity and the recorded p are independent axes.
        script[BNS69_EL[1]] = [_est("F3", TOY_FACTS[2], "Lata had sexual intercourse with Kiran", p=0.6)]
        c = self._run(tmp_path, script, {BNS69_EL[0]: [_second("established", 0.999)],
                                         BNS69_EL[1]: [_second("established", 0.975)]}, delta=0.2)
        assert c.outcome == "REFER_TO_LAWYER"
        assert c.reason == "uncertain"
        assert c.uncertain == [BNS69_EL[1]]
        assert c.uncertain_second == [BNS69_EL[1]]  # both recorded; only the reason picks one

    def test_composes_with_typed_layer_flag_independently(self, tmp_path: Path) -> None:
        """`typed_layer` governs judge CONSTRUCTION only (`NyayaAgent.house`); the second-judge band reads
        `second_judge.refer_logit_delta` off the config the same way whichever flag value is set."""
        for typed in (False, True):
            cfg = _config(tmp_path, typed_layer=typed, second_judge={"tau": 0.97, "refer_logit_delta": 0.2})
            assert cfg.second_refer_logit_delta() == 0.2

    def test_negative_delta_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError):
            _config(tmp_path, second_judge={"tau": 0.97, "refer_logit_delta": -0.1})

    def test_no_second_judge_config_means_delta_is_none(self, tmp_path: Path) -> None:
        assert _config(tmp_path).second_refer_logit_delta() is None

    def test_env_var_introduces_the_key_on_a_host_with_no_yaml_block(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("NYAYA_SECOND_JUDGE_REFER_LOGIT_DELTA", "0.2")
        sj = load_agent_config(REPO).second_judge
        assert sj is not None
        assert sj["refer_logit_delta"] == 0.2

    def test_env_var_absent_by_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("NYAYA_SECOND_JUDGE_REFER_LOGIT_DELTA", raising=False)
        assert load_agent_config(REPO).second_judge is None


class TestStatuteMismatch:
    def test_training_vs_official_text_is_flagged_per_contract(self, tmp_path: Path) -> None:
        run, _, _ = _run(tmp_path, _proof_script(TOY_FACTS))
        c = run.contracts[0]
        assert c.statute_text_mismatch is True  # config "TRAINING..." != binary "RETRIEVED..."
        lines = [json.loads(x) for x in run.audit_path.read_text().splitlines()]
        (st,) = [x for x in lines if x["step"] == "statute"]
        assert st["output"]["statute_text_mismatch"] is True
        assert st["output"]["judge_statute_sha256"] == hashlib.sha256(b"TRAINING statute text for bns69").hexdigest()
        assert st["output"]["official_statute_sha256"] == hashlib.sha256(b"RETRIEVED statute text for bns69").hexdigest()
        assert st["output"]["judge_statute_source"] == "config"

    def test_identical_texts_are_not_flagged(self, tmp_path: Path) -> None:
        judge = ScriptedJudge(_proof_script(TOY_FACTS))
        cfg = _config(tmp_path, judge_statute_text={"bns69": "RETRIEVED statute text for bns69"})
        run = NyayaAgent(judge, _registry(), cfg).run(TOY_FACTS, contract_ids=["bns69"])
        assert run.contracts[0].statute_text_mismatch is False


class TestAudit:
    def test_every_step_is_a_jsonl_line_with_hashed_inputs(self, tmp_path: Path) -> None:
        run, _, _ = _run(tmp_path, _proof_script(TOY_FACTS))
        lines = [json.loads(x) for x in run.audit_path.read_text().splitlines()]
        steps = [x["step"] for x in lines]
        assert steps[0] == "run_start" and steps[-1] == "run_end"
        for s in (
            "ingest",
            "select_contracts",
            "describe",
            "statute",
            "judge",
            "quote_check",
            "assemble",
            "lean_check",
            "outcome",
        ):
            assert s in steps
        assert [x["seq"] for x in lines] == list(range(len(lines)))
        assert all(x["run_id"] == run.run_id for x in lines)
        assert all(len(x["inputs_sha256"]) == 64 and isinstance(x["wall_ms"], float) for x in lines)
        ingest = next(x for x in lines if x["step"] == "ingest")
        assert ingest["output"]["facts"][0] == {"id": "F1", "sha256": hashlib.sha256(TOY_FACTS[0].encode()).hexdigest()}
        assert run.audit_path.parent == tmp_path / "audit"

    def test_judge_steps_record_attempt_statute_source_and_output(self, tmp_path: Path) -> None:
        run, _, _ = _run(tmp_path, _proof_script(TOY_FACTS))
        lines = [json.loads(x) for x in run.audit_path.read_text().splitlines()]
        judged = [x for x in lines if x["step"] == "judge"]
        assert len(judged) == 3
        assert judged[0]["output"]["judgment"]["status"] == "established"
        assert judged[0]["output"]["attempt"] == 1
        assert judged[0]["output"]["statute_source"] == "config"
        assert judged[0]["output"]["uses"] == "status_and_quote"

    def test_retry_judge_steps_are_marked_quote_only(self, tmp_path: Path) -> None:
        script = _proof_script(TOY_FACTS)
        script[BNS69_EL[0]] = [_bad("F2", "never to wed Lata"), script[BNS69_EL[0]][0]]
        run, _, _ = _run(tmp_path, script)
        lines = [json.loads(x) for x in run.audit_path.read_text().splitlines()]
        el0 = [x["output"] for x in lines if x["step"] == "judge" and x["output"]["element"] == BNS69_EL[0]]
        assert [x["uses"] for x in el0] == ["status_and_quote", "quote_only"]

    def test_quote_checks_record_system_offsets(self, tmp_path: Path) -> None:
        run, _, _ = _run(tmp_path, _proof_script(TOY_FACTS))
        lines = [json.loads(x) for x in run.audit_path.read_text().splitlines()]
        qc = [x["output"] for x in lines if x["step"] == "quote_check"]
        assert qc and all(x["offsets_source"] == "system" for x in qc)
        assert qc[0]["start"] == TOY_FACTS[1].find("never to marry Lata") and qc[0]["occurrences"] == 1


class TestConfig:
    def test_repo_config_loads(self) -> None:
        cfg = load_agent_config(REPO)
        assert cfg.tau == 0.74
        assert cfg.refer_band == (0.5, 0.74)
        assert cfg.max_retries == 2
        assert cfg.pinned_score_sha256 == PINNED
        assert set(cfg.judge_statute_text) <= reg.KNOWN_CONTRACT_IDS
        assert cfg.audit_dir == REPO / "research" / "nyaya" / "agent_runs"

    def test_bad_band_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError):
            _config(tmp_path, refer_band=(0.8, 0.5))

    def test_repo_config_has_no_second_judge_by_default(self) -> None:
        """The shipped config has no `second_judge:` block: config C is opt-in, never the default."""
        assert load_agent_config(REPO).second_judge is None

    def test_second_judge_env_vars_are_absent_by_default_too(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for var in ("NYAYA_SECOND_JUDGE_BASE_URL", "NYAYA_SECOND_JUDGE_MODEL", "NYAYA_SECOND_JUDGE_TAU",
                    "NYAYA_SECOND_JUDGE_TIMEOUT_S"):
            monkeypatch.delenv(var, raising=False)
        assert load_agent_config(REPO).second_judge is None

    def test_second_judge_env_vars_introduce_the_block(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("NYAYA_SECOND_JUDGE_BASE_URL", "http://127.0.0.1:8111/v1")
        monkeypatch.setenv("NYAYA_SECOND_JUDGE_MODEL", "nyaya-judge-32b")
        monkeypatch.setenv("NYAYA_SECOND_JUDGE_TAU", "0.97")
        monkeypatch.setenv("NYAYA_SECOND_JUDGE_TIMEOUT_S", "120")
        sj = load_agent_config(REPO).second_judge
        assert sj is not None
        assert sj["base_url"] == "http://127.0.0.1:8111/v1"
        assert sj["model"] == "nyaya-judge-32b"
        assert sj["tau"] == 0.97
        assert sj["timeout_s"] == 120

    def test_second_judge_env_vars_override_a_configured_block(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import yaml

        cfg_dir = tmp_path / "configs"
        cfg_dir.mkdir()
        body = yaml.safe_load((REPO / "configs" / "nyaya_agent.yaml").read_text())
        body["second_judge"] = {"base_url": "http://from-yaml/v1", "tau": 0.9, "statute_chars": 600,
                                 "max_tokens": 30, "top_logprobs": 20, "timeout_s": 60}
        (cfg_dir / "nyaya_agent.yaml").write_text(yaml.safe_dump(body))
        monkeypatch.setenv("NYAYA_SECOND_JUDGE_TAU", "0.97")
        sj = load_agent_config(tmp_path).second_judge
        assert sj is not None
        assert sj["base_url"] == "http://from-yaml/v1"  # untouched: no env var for it
        assert sj["tau"] == 0.97  # env override wins

    def test_env_only_second_judge_inherits_prompt_shape_from_house_judge(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Lead-2, 2026-09-25: a second_judge built purely from NYAYA_SECOND_JUDGE_* env vars (no yaml
        second_judge: block at all) used to crash the first time NyayaAgent.house() ran, with
        KeyError: 'statute_chars' -- HouseJudge.from_config requires statute_chars/top_logprobs/max_tokens
        and there was no way to supply them via env alone. This is what makes a purely env-driven switch-on
        possible (the RunPod endpoint is configured this way)."""
        monkeypatch.setenv("NYAYA_SECOND_JUDGE_BASE_URL", "http://127.0.0.1:8111/v1")
        monkeypatch.setenv("NYAYA_SECOND_JUDGE_TAU", "0.97")
        cfg = load_agent_config(REPO)
        sj = cfg.second_judge
        assert sj is not None
        house = cfg.house_judge
        assert sj["statute_chars"] == house["statute_chars"]
        assert sj["top_logprobs"] == house["top_logprobs"]
        assert sj["max_tokens"] == house["max_tokens"]

    def test_second_judge_explicit_statute_chars_override_wins_over_inherited(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("NYAYA_SECOND_JUDGE_BASE_URL", "http://127.0.0.1:8111/v1")
        monkeypatch.setenv("NYAYA_SECOND_JUDGE_STATUTE_CHARS", "500")
        monkeypatch.setenv("NYAYA_SECOND_JUDGE_TOP_LOGPROBS", "10")
        monkeypatch.setenv("NYAYA_SECOND_JUDGE_MAX_TOKENS", "40")
        cfg = load_agent_config(REPO)
        sj = cfg.second_judge
        assert sj is not None
        assert sj["statute_chars"] == 500 != cfg.house_judge["statute_chars"]
        assert sj["top_logprobs"] == 10 != cfg.house_judge["top_logprobs"]
        assert sj["max_tokens"] == 40 != cfg.house_judge["max_tokens"]

    def test_yaml_second_judges_own_statute_chars_is_not_overwritten_by_inheritance(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import yaml

        cfg_dir = tmp_path / "configs"
        cfg_dir.mkdir()
        body = yaml.safe_load((REPO / "configs" / "nyaya_agent.yaml").read_text())
        assert body["house_judge"]["statute_chars"] != 999  # the test's own premise: genuinely different
        body["second_judge"] = {"base_url": "http://from-yaml/v1", "tau": 0.9, "statute_chars": 999,
                                 "max_tokens": 30, "top_logprobs": 20, "timeout_s": 60}
        (cfg_dir / "nyaya_agent.yaml").write_text(yaml.safe_dump(body))
        sj = load_agent_config(tmp_path).second_judge
        assert sj is not None
        assert sj["statute_chars"] == 999  # untouched -- yaml's own value, not overwritten by house_judge's

    def test_env_only_second_judge_with_no_second_judge_at_all_stays_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The inheritance logic only ever runs when second_judge is not None -- no second_judge configured
        at all must still be byte-identical to before this change (None, no house_judge leakage)."""
        for var in ("NYAYA_SECOND_JUDGE_BASE_URL", "NYAYA_SECOND_JUDGE_MODEL", "NYAYA_SECOND_JUDGE_TAU",
                    "NYAYA_SECOND_JUDGE_TIMEOUT_S", "NYAYA_SECOND_JUDGE_STATUTE_CHARS",
                    "NYAYA_SECOND_JUDGE_TOP_LOGPROBS", "NYAYA_SECOND_JUDGE_MAX_TOKENS"):
            monkeypatch.delenv(var, raising=False)
        assert load_agent_config(REPO).second_judge is None


class TestHouseFactory:
    """`NyayaAgent.house` wires config C (AndGateJudge) only when `second_judge` is configured; absent is
    exactly today's plain HouseJudge -- no network call happens in either case (both configs name their
    model, so no `/models` round-trip)."""

    _HOUSE_JUDGE_CFG = {"base_url": "http://h/v1", "model": "m", "statute_chars": 600, "max_tokens": 30,
                         "top_logprobs": 20, "timeout_s": 5}

    def _score_bin(self, tmp_path: Path) -> Path:
        p = tmp_path / "score"
        p.write_bytes(b"fake binary, sha256 unpinned for this test")
        return p

    def test_without_second_judge_uses_plain_house_judge(self, tmp_path: Path) -> None:
        from pravrudhi.application.nyaya_judges import HouseJudge

        cfg = _config(tmp_path, house_judge=self._HOUSE_JUDGE_CFG, score_bin=self._score_bin(tmp_path),
                      pinned_score_sha256=None)
        agent = NyayaAgent.house(tmp_path, config=cfg)
        assert isinstance(agent.judge, HouseJudge)

    def test_with_second_judge_uses_and_gate(self, tmp_path: Path) -> None:
        from pravrudhi.application.nyaya_judges import AndGateJudge

        second_cfg = {**self._HOUSE_JUDGE_CFG, "base_url": "http://s/v1", "model": "m2", "tau": 0.97}
        cfg = _config(tmp_path, house_judge=self._HOUSE_JUDGE_CFG, second_judge=second_cfg,
                      score_bin=self._score_bin(tmp_path), pinned_score_sha256=None)
        agent = NyayaAgent.house(tmp_path, config=cfg)
        assert isinstance(agent.judge, AndGateJudge)
        assert agent.judge.tau_primary == 0.74 and agent.judge.tau_second == 0.97
        assert agent.judge.primary.model == "m" and agent.judge.second.model == "m2"


class TestRealBinary:
    @requires_score_bin
    def test_pinned_sha_is_enforced(self) -> None:
        with pytest.raises(BinaryShaMismatch):
            BinaryRegistry(SCORE_BIN, pinned_sha256="0" * 64)
        assert BinaryRegistry(SCORE_BIN, pinned_sha256=PINNED).sha256 == PINNED

    @requires_score_bin
    def test_scripted_registry_answers_like_the_real_binary(self) -> None:
        real = BinaryRegistry(SCORE_BIN, pinned_sha256=PINNED)
        fake = _registry()
        c = real.describe("bns69")
        assert (c.elements, c.denials) == (BNS69_EL, [BNS69_DENY])
        for assertions in (
            {BNS69_EL[0]: True, BNS69_EL[1]: True},
            {BNS69_EL[0]: True, BNS69_EL[1]: False},
            {BNS69_EL[0]: True, BNS69_EL[1]: True, BNS69_DENY: True},
        ):
            r, f = real.check(assertions, "bns69"), fake.check(assertions, "bns69")
            assert outcome_from_lean(r) == outcome_from_lean(f) == expected_outcome(c, assertions)

    @requires_score_bin
    def test_loop_end_to_end_on_the_real_binary(self, tmp_path: Path) -> None:
        real = BinaryRegistry(SCORE_BIN, pinned_sha256=PINNED)
        agent = NyayaAgent(ScriptedJudge(_proof_script(TOY_FACTS)), real, _config(tmp_path))
        run = agent.run(TOY_FACTS, contract_ids=["bns69"])
        assert run.contracts[0].outcome == "PROOF"
        assert real.source_text("bns69").startswith("Whoever, by deceitful means")

    @requires_score_bin
    def test_training_statute_texts_that_differ_from_the_official_text(self) -> None:
        """The M2 target list: which configured training texts are not the binary's official text."""
        real = BinaryRegistry(SCORE_BIN, pinned_sha256=PINNED)
        training = load_agent_config(REPO).judge_statute_text
        differ = sorted(cid for cid, text in training.items() if text != real.source_text(cid))
        # 2026-09-25: 11 of the 12 Wave-1 ids (bns316/318/217/80, bnss187) were added to close the
        # no_training_statute_text ABSTAIN gap Lead-2's production relay test found; each is trimmed to an
        # operative sentence same as the pre-existing entries, so (like several of those) differs from the
        # binary's own full --describe-source text. bns108 is the one Wave-1 id whose full official text IS
        # short enough to use verbatim, so it is NOT in this list. ni138 has no entry at all (not sourced,
        # not fabricated) and so does not appear in `training` in the first place.
        assert len(training) == 25
        assert differ == sorted(
            [
                "ipc415_property",
                "ipc415_damaging_act",
                "ipc182_misdirected_act",
                "ipc182_abuse_of_power",
                "bns69",
                "bns85",
                "bns46_instigation",
                "bns46_conspiracy",
                "bns46_intentional_aid",
                "bns316_misappropriation",
                "bns316_use_or_disposal",
                "bns316_wilfully_suffers",
                "bns318_property",
                "bns318_damaging_act",
                "bns217_misdirected_act",
                "bns217_abuse_of_power",
                "bns80",
                "bnss187_extended_serious",
                "bnss187_extended_other",
            ]
        )

    def test_every_known_contract_has_training_statute_text_except_the_named_exception(self) -> None:
        """The regression this exists to catch (Lead-2, 2026-09-25 production relay test): Track A's Wave-1
        registry expansion (14 -> 26 contracts) added BNS 316/318/217/80/108, BNSS 187 and NI Act 138 to the
        Lean side but nobody wired their statute text here, so every one of them silently ABSTAINed with
        no_training_statute_text -- in production, not caught by CI, because no test asserted COMPLETENESS
        (the existing `<=` check above only ever caught an extra/misspelled key, never a missing one).
        `ni138` is the one deliberate, named exception: NI Act 1881 was never one of
        india_code_fetch.py's five operator-authorized Acts, so its text is not sourced anywhere in this
        repo, and this test does not require it -- but a future Wave-2 contract added to
        KNOWN_CONTRACT_IDS with no corresponding entry here, and no equally-explicit exception added to
        this test, now fails loudly instead of shipping a silent ABSTAIN."""
        training = load_agent_config(REPO).judge_statute_text
        deliberately_unsourced = {"ni138"}
        missing = (reg.KNOWN_CONTRACT_IDS - deliberately_unsourced) - set(training)
        assert missing == set(), f"contracts with no training statute text at all: {sorted(missing)}"


class TestJudgeConfigurationFault:
    """Reviewer 1 (2026-09-24): a 4xx from the judge (bad key, unknown model) is a configuration fault. Recording it as
    an ordinary per-element failure turned a revoked key into a normal-looking "not established"; it must stop the
    request instead."""

    def _fault(self, status: int) -> RuntimeError:
        from pravrudhi.models.openai_compat import HTTPStatusError

        try:
            raise HTTPStatusError(status, f"judge answered {status}")
        except HTTPStatusError as inner:
            err = RuntimeError(f"judge backend 0 failed: {inner}")
            err.__cause__ = inner
            return err

    @pytest.mark.parametrize("status", [401, 403, 404, 400])
    def test_a_client_error_from_the_judge_stops_the_run(self, tmp_path: Path, status: int) -> None:
        from pravrudhi.application.nyaya_agent import JudgeMisconfigured

        judge = ScriptedJudge({el: [self._fault(status)] for el in BNS69_EL + [BNS69_DENY]})
        with pytest.raises(JudgeMisconfigured, match=str(status)):
            NyayaAgent(judge, _registry(), _config(tmp_path)).run(TOY_FACTS, contract_ids=["bns69"])
        assert len(judge.requests) == 1  # never retried

    def test_a_transient_failure_is_still_retried_and_recorded(self, tmp_path: Path) -> None:
        judge = ScriptedJudge({el: [self._fault(503)] for el in BNS69_EL + [BNS69_DENY]})
        run = NyayaAgent(judge, _registry(), _config(tmp_path)).run(TOY_FACTS, contract_ids=["bns69"])
        assert run.contracts[0].outcome != "PROOF"


class TestSecondBandReproducesSignedEndpointCounts:
    """Reproduces the signed served_C REFER-band counts on the production-candidate serverless endpoint's
    32B raw (T2-HARNESS-CONFIGC-ENDPOINT-32B-RESULTS-2026-09-24.md, sha
    6766dda20cee0a4fbb473840550ee40c612aab2c0d1fbd16f661c1b9866ca3ad): 6/10/15 of 109 decided items referred
    at delta=0.125/0.25/0.375. Uses the SHIPPED `nyaya_agent._logit` distance function -- the same one
    `_second_band_info` calls in production -- never a reimplementation of the math, so this proves the
    engine code (not just the measurement script that originally produced these numbers) reproduces them.

    Host-specific sealed artifacts, not committed: skips cleanly when the env vars aren't set, per this
    project's standing rule (no host path defaults, no silent skip-as-pass -- an unset env var here means
    "not configured", printed as the skip reason, not a false green)."""

    ENDPOINT_32B_SHA256 = "5fc22c0c1ccb509c6e51b1ccf7d6c10d8fb134b007aec2cc20f2faba73a51c78"
    TAU = 0.97

    _SCORES_ENV = "PRAVRUDHI_NYAYA_AGENT_TEST_ENDPOINT_32B_SCORES"
    _VERDICTS_ENV = "PRAVRUDHI_NYAYA_AGENT_TEST_ENDPOINT_SERVED_C_VERDICTS"
    _scores_path = os.environ.get(_SCORES_ENV)
    _verdicts_path = os.environ.get(_VERDICTS_ENV)
    requires_sealed_endpoint = pytest.mark.skipif(
        not (_scores_path and _verdicts_path and Path(_scores_path).exists() and Path(_verdicts_path).exists()),
        reason=(
            f"sealed endpoint served_C artifacts not configured on this host "
            f"(set {_SCORES_ENV} and {_VERDICTS_ENV})"
        ),
    )

    def _load(self) -> tuple[dict[tuple[str, str], float], list[dict[str, Any]]]:
        from pravrudhi.application.nyaya_agent import _logit  # noqa: F401 -- imported by callers below

        scores_path = Path(self._scores_path)  # type: ignore[arg-type]
        raw = scores_path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        assert digest == self.ENDPOINT_32B_SHA256, (
            f"{scores_path} sha256 {digest} != R2-signed {self.ENDPOINT_32B_SHA256} -- refusing to reproduce "
            "the signed counts against an unverified file"
        )
        served_32b: dict[tuple[str, str], float] = {}
        for r in (json.loads(line) for line in raw.decode().splitlines() if line.strip()):
            item_id, element_id = r["id"].rsplit("__", 1)  # element_id never itself contains "__"
            served_32b[(item_id, element_id)] = r["p_established"]
        verdicts_path = Path(self._verdicts_path)  # type: ignore[arg-type]
        verdicts = [json.loads(line) for line in verdicts_path.read_text().splitlines() if line.strip()]
        return served_32b, verdicts

    @requires_sealed_endpoint
    @pytest.mark.parametrize("delta,expected_referred,expected_decided", [(0.125, 6, 109), (0.25, 10, 109), (0.375, 15, 109)])
    def test_reproduces_signed_referred_count(
        self, delta: float, expected_referred: int, expected_decided: int
    ) -> None:
        from pravrudhi.application.nyaya_agent import _logit

        served_32b, verdicts = self._load()
        decided = [
            r for r in verdicts
            if (r["partition"] == "full_ir_gold" and r["checker_pass"] is True)
            or (r["partition"] == "negatives" and r["false_prove"])
        ]
        assert len(decided) == expected_decided  # the signed doc's own "109 decided" premise

        logit_tau = _logit(self.TAU)
        referred_items: set[str] = set()
        for r in decided:
            elem_ids = {eid for (iid, eid) in served_32b if iid == r["item_id"]}
            for eid in elem_ids:
                p32 = served_32b.get((r["item_id"], eid))
                if p32 is None:
                    continue
                if abs(_logit(p32) - logit_tau) < delta:  # the shipped band's own strict "<", never "<="
                    referred_items.add(r["item_id"])
                    break

        assert len(referred_items) == expected_referred

    @requires_sealed_endpoint
    def test_delta_grid_is_monotone_non_decreasing(self) -> None:
        """A wider delta can only catch the same items or more, never fewer -- sanity-checks the three
        parametrized counts above are internally consistent with each other, not just individually correct."""
        from pravrudhi.application.nyaya_agent import _logit

        served_32b, verdicts = self._load()
        decided = [
            r for r in verdicts
            if (r["partition"] == "full_ir_gold" and r["checker_pass"] is True)
            or (r["partition"] == "negatives" and r["false_prove"])
        ]
        logit_tau = _logit(self.TAU)

        def referred_count(delta: float) -> int:
            items: set[str] = set()
            for r in decided:
                elem_ids = {eid for (iid, eid) in served_32b if iid == r["item_id"]}
                for eid in elem_ids:
                    p32 = served_32b.get((r["item_id"], eid))
                    if p32 is not None and abs(_logit(p32) - logit_tau) < delta:
                        items.add(r["item_id"])
                        break
            return len(items)

        counts = [referred_count(d) for d in (0.125, 0.25, 0.375)]
        assert counts == sorted(counts)
        assert counts == [6, 10, 15]
