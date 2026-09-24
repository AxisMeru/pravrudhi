"""The Nyaya agentic loop: ingest facts, select contracts, judge each element, quote-check, assemble the REG
wire deterministically, Lean-check it, decide PROOF / DENIAL / ABSTAIN / REFER_TO_LAWYER, audit every step.

The judge is a test double (scripted per element) and lives only in this file. The Lean side is the REAL
pinned `score` binary where it is built (`requires_score_bin`), and a scripted registry double elsewhere, so
the decision logic is covered on a host without the sibling checkout too. All facts are hand-written toy text.
"""

from __future__ import annotations

import hashlib
import json
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
from pravrudhi.application.nyaya_judges import ElementJudgment, JudgeOutputError, JudgeRequest

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
        assert len(training) == 14
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
            ]
        )


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
