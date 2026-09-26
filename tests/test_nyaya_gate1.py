"""Gate 1 (Track-C, GATE1-PRODUCT-WIRING-SPEC-2026-09-26.md): a third gate, pure NLI entailment check, run
only on an element the judge(s) above already established. `split_disjuncts`/`gate1_check` are pure functions
(no model call -- `gate1_check` takes an injected `score_fn`); `Gate1Judge` wraps a `Judge` double the same
way `test_nyaya_and_gate.py` wraps two for `AndGateJudge`, never a real NLI model.
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

from pravrudhi.application.nyaya_judges import (
    ElementJudgment,
    Gate1Judge,
    JudgeRequest,
    gate1_check,
    split_disjuncts,
)

#: No hard-coded absolute path (this repo is public) -- set PRABHASA_NYAYA_SCORE_BIN to a real, built
#: prabhasa-nyaya score binary to run the registry-wide tests below (same convention as
#: test_nyaya_application_registry.py / test_api_nyaya_registry.py).
_SCORE_BIN = os.environ.get("PRABHASA_NYAYA_SCORE_BIN")
requires_score_bin = pytest.mark.skipif(
    not _SCORE_BIN or not Path(_SCORE_BIN).exists(),
    reason="set PRABHASA_NYAYA_SCORE_BIN to a built prabhasa-nyaya score binary to run these",
)

#: Every contract id the pinned binary knows, per KNOWN_CONTRACT_IDS (nyaya_lean_registry.py) -- listed
#: directly rather than imported to keep this test file independent of that module's own drift test.
_ALL_REGISTRY_CONTRACT_IDS = (
    "ipc405_misappropriation", "ipc405_use_or_disposal", "ipc405_wilfully_suffers",
    "ipc415_property", "ipc415_damaging_act", "ipc416",
    "ipc182_misdirected_act", "ipc182_abuse_of_power",
    "bns69", "bns47", "bns85",
    "bns46_instigation", "bns46_conspiracy", "bns46_intentional_aid",
    "bns316_misappropriation", "bns316_use_or_disposal", "bns316_wilfully_suffers",
    "bns318_property", "bns318_damaging_act",
    "bns217_misdirected_act", "bns217_abuse_of_power",
    "bns80", "bns108",
    "bnss187_extended_serious", "bnss187_extended_other",
    "ni138",
)


def _registry_element_descriptions() -> list[tuple[str, str, str]]:
    """(contract_id, element_id, description) for EVERY element of every registered contract, straight off
    the binary's own `--describe-contract` output -- never `p2b_registry.CONTRACTS`, which is missing
    several of the newer contracts (a Python-side data gap, not this test's concern).

    HARD FAILS on a stale `PRABHASA_NYAYA_SCORE_BIN` that doesn't know a given contract yet (a real gap R1
    found, 2026-09-26: an older binary answers `--describe-contract` for an unknown id with exit code 0 and
    a single `"UNKNOWN_CONTRACT_ID\\t<cid>"` line on stdout, not a subprocess failure -- the pre-fix version
    of this function silently swallowed that as if it were one real, harmless element description, masking
    every real element of that contract from the whole registry scan below. `bnss187_extended_other` was
    missed exactly this way against an older local binary; this guard makes that failure mode loud instead
    of silent, so it can never happen again unnoticed."""
    out = []
    for cid in _ALL_REGISTRY_CONTRACT_IDS:
        proc = subprocess.run(
            [str(_SCORE_BIN), "--describe-contract", cid], capture_output=True, text=True, timeout=30, check=True,
        )
        lines = [line for line in proc.stdout.split("\n") if line]
        if lines and lines[0].startswith("UNKNOWN_CONTRACT_ID"):
            raise AssertionError(
                f"PRABHASA_NYAYA_SCORE_BIN={_SCORE_BIN} does not know contract {cid!r} "
                f"(binary said: {lines[0]!r}) -- this binary is too old for a complete registry scan; "
                f"point PRABHASA_NYAYA_SCORE_BIN at the currently-pinned binary instead of skipping silently"
            )
        for i, desc in enumerate(lines):
            out.append((cid, f"el{i}", desc))
    return out


#: The pre-fix regex pair, inlined verbatim for the non-regression comparison below -- this is what
#: `split_disjuncts` computed before the paren-awareness fix (GATE1-PAREN-SPLIT-BUG-2026-09-26.md), kept
#: here ONLY as a fixed comparison baseline, never imported from the module (which no longer has it).
_PRE_FIX_SUFFIX3 = re.compile(r"^(?P<pre>.*?),\s*or\s+(?P<b>[^,]+),\s*(?P<suf>.*)$")
_PRE_FIX_SIMPLE2 = re.compile(r"^(?P<pre>.*?),?\s+or\s+(?P<suf>.*)$")


def _pre_fix_split(desc: str) -> list[str]:
    m = _PRE_FIX_SUFFIX3.match(desc)
    if m:
        return [f"{m['pre']}, {m['suf']}", f"{m['b']}, {m['suf']}"]
    m = _PRE_FIX_SIMPLE2.match(desc)
    if m:
        return [m["pre"], m["suf"]]
    return [desc]

REQ = JudgeRequest(
    contract_id="bns85",
    element="the accused is the husband, or a relative of the husband, of the woman",
    is_denial=False,
    statute="Whoever, being the husband ... shall be punished.",
    narrative="TOY: Arun married Bela in 2019.",
    facts=(("F1", "TOY: Arun married Bela in 2019."), ("F2", "TOY: Arun beat Bela.")),
)

#: A request whose element description has no disjunction ("or") at all -- `split_disjuncts` returns it
#: unchanged as a single item, so a test doesn't also have to account for REQ's own disjunctive element text.
REQ_SIMPLE = JudgeRequest(
    contract_id="bns85", element="the accused married the woman", is_denial=False,
    statute=REQ.statute, narrative=REQ.narrative, facts=REQ.facts,
)


def _judgment(status: str, p: float = 0.97, *, fact_id: str | None = "F1",
              quote: str | None = "TOY: Arun married Bela in 2019.") -> ElementJudgment:
    return ElementJudgment(status, p, fact_id, quote, "whole_fact" if fact_id else None,
                           raw=f"established {fact_id}" if status == "established" else "not")


@dataclass
class _StubJudge:
    """A `Judge` double that returns a fixed judgment (or raises) and records calls -- same shape
    `test_nyaya_and_gate.py`'s own `_StubJudge` uses."""

    name: str
    result: ElementJudgment | None = None
    error: BaseException | None = None

    def __post_init__(self) -> None:
        self.calls = 0

    def judge(self, request: JudgeRequest) -> ElementJudgment:
        self.calls += 1
        if self.error is not None:
            raise self.error
        assert self.result is not None
        return self.result


@dataclass
class _StubModel:
    """A `Gate1NLIModel` double: `score_fn` maps (fact_text, hypothesis_text) -> a fixed score, or raises."""

    model_id: str = "stub-model"
    scores: dict[str, float] | None = None
    error: BaseException | None = None
    calls: list[tuple[str, str]] | None = None

    def __post_init__(self) -> None:
        if self.calls is None:
            self.calls = []

    def score_one(self, fact_text: str, hypothesis_text: str) -> float:
        assert self.calls is not None
        self.calls.append((fact_text, hypothesis_text))
        if self.error is not None:
            raise self.error
        assert self.scores is not None
        return self.scores[hypothesis_text]


class TestSplitDisjuncts:
    """The two regex patterns frozen on heldout_v1 (`run_configc_gate1_v2_eval.py` sha `7ed02c49`), copied
    verbatim into `nyaya_judges.py` -- these tests pin the exact same shapes, not new ones."""

    def test_three_part_shared_suffix(self) -> None:
        assert split_disjuncts("presented within 3 months, or within the period of validity, whichever is earlier") == [
            "presented within 3 months, whichever is earlier",
            "within the period of validity, whichever is earlier",
        ]

    def test_simple_two_part(self) -> None:
        assert split_disjuncts("insufficiency of funds, or account closed") == [
            "insufficiency of funds", "account closed",
        ]

    def test_no_disjunction_is_a_single_item_list(self) -> None:
        assert split_disjuncts("the drawer failed to pay within 15 days of receipt") == [
            "the drawer failed to pay within 15 days of receipt"
        ]

    def test_documented_subject_embedded_gap_produces_a_broken_but_non_flipping_split(self) -> None:
        """The known limitation, shipped documented rather than fixed (spec §2): a SUBJECT-EMBEDDED
        disjunction ("the consequence [intended, or known to be likely,] is X") still matches the 3-part
        shape by punctuation alone and produces two grammatically broken halves -- this pins exactly what
        those broken halves look like, so a future change to the regex is forced to notice it changed this
        case, not silently drift."""
        broken = split_disjuncts("the consequence intended, or known to be likely, is death")
        assert broken == ["the consequence intended, is death", "known to be likely, is death"]
        # Neither half reads as a complete, correct restatement of the original clause -- this is the
        # documented non-rescue, not a fix.
        assert broken != ["the consequence is intended to be death", "the consequence is known to be likely death"]


class TestParenAwareSplit:
    """GATE1-PAREN-SPLIT-BUG-2026-09-26.md (corrected by its successor, R1 finding 2026-09-26): the pre-fix
    regex matched "or" inside a parenthetical (a statutory citation, not a real disjunction) as a top-level
    split point, producing paren-unbalanced garbage on **5/30** disjunctive-matched elements across the
    26-contract registry -- the original hand scan found 4 (run against a stale local binary that didn't
    even know the 5th contract, `bnss187_extended_other`, existed); R1 caught the 5th running this test
    against the currently-pinned binary. These pin the exact corrected output for all 5, found and
    root-caused by hand."""

    def test_bns85_el1_citation_parenthetical_is_not_a_disjunction(self) -> None:
        # "(s.86(a) or (b))" is a citation, not "subjects to cruelty OR subjects to something else" --
        # there is no real top-level disjunction here at all.
        assert split_disjuncts("subjects the woman to cruelty (s.86(a) or (b))") == [
            "subjects the woman to cruelty (s.86(a) or (b))"
        ]

    def test_ipc416_el0_citation_parenthetical_is_not_a_disjunction(self) -> None:
        assert split_disjuncts(
            "cheats (s.415: either the property-inducement limb or the damaging-act limb)"
        ) == ["cheats (s.415: either the property-inducement limb or the damaging-act limb)"]

    def test_bns69_el0_finds_the_real_top_level_or_past_the_parenthetical(self) -> None:
        # The "or"s INSIDE the parenthetical (a list of ways deceit can happen) are not the split point;
        # the real top-level disjunction is the "or" AFTER the parenthetical closes.
        assert split_disjuncts(
            "induces the woman by deceitful means (inducement for, or false promise of, employment or "
            "promotion, or marrying by suppressing identity), or by a promise to marry made without any "
            "intention of fulfilling it"
        ) == [
            "induces the woman by deceitful means (inducement for, or false promise of, employment or "
            "promotion, or marrying by suppressing identity)",
            "by a promise to marry made without any intention of fulfilling it",
        ]

    def test_doubly_nested_parens_never_split(self) -> None:
        """R1 review question: does depth-tracking handle MORE than one level of nesting? None of the 4
        real registry elements need more than 1 level, so this is a synthetic stress case, not a registry
        pin -- an "or" two levels deep must still never be treated as a top-level disjunction."""
        assert split_disjuncts(
            "the accused acted unlawfully (see s.5 (exceptions (a) or (b) apply)) and caused harm"
        ) == ["the accused acted unlawfully (see s.5 (exceptions (a) or (b) apply)) and caused harm"]

    def test_real_top_level_or_after_a_doubly_nested_paren_closes(self) -> None:
        """Depth must return to 0 (not get stuck) once ALL nested parens close, so a genuine top-level "or"
        immediately after a doubly-nested citation still splits correctly."""
        assert split_disjuncts(
            "the accused acted unlawfully (see s.5 (exceptions (a) or (b) apply)), or failed to prevent it"
        ) == [
            "the accused acted unlawfully (see s.5 (exceptions (a) or (b) apply))",
            "failed to prevent it",
        ]

    def test_two_separate_parenthesized_clauses_each_with_their_own_or(self) -> None:
        """Depth must drop back to 0 between two SEPARATE parenthesized clauses, not stay "inside" from the
        first one -- the "or" joining the two clauses is a real top-level disjunction; the "or"s inside
        each clause's own parens are not."""
        assert split_disjuncts("a happens (x or y), or b happens (p or q)") == [
            "a happens (x or y)", "b happens (p or q)",
        ]

    def test_bns46_instigation_el0_finds_the_real_top_level_or_past_the_parenthetical(self) -> None:
        assert split_disjuncts(
            "instigates any person to do the thing (urges, incites or provokes it), including, per s.45 "
            "Explanation 1, wilfully misrepresenting or wilfully concealing a material fact one is bound "
            "to disclose so as to cause, procure, or attempt to cause or procure it"
        ) == [
            "instigates any person to do the thing (urges, incites or provokes it), including, per s.45 "
            "Explanation 1, wilfully misrepresenting",
            "wilfully concealing a material fact one is bound to disclose so as to cause, procure, or "
            "attempt to cause or procure it",
        ]

    def test_bnss187_extended_other_el2_citation_parenthetical_is_not_a_disjunction(self) -> None:
        """The 5th affected element, found by R1 (2026-09-26) running the non-regression test against the
        currently-pinned binary -- the original hand scan missed it because the local binary used at the
        time didn't know `bnss187_extended_other` at all (see `_registry_element_descriptions`'s own
        UNKNOWN_CONTRACT_ID guard, added for exactly this). Same shape as the other 4: an "or" that's part
        of a parenthetical qualifier, not a real disjunction."""
        assert split_disjuncts(
            "the investigation relates to any other offence (not within the death, life, or "
            "ten-years-or-more category)"
        ) == [
            "the investigation relates to any other offence (not within the death, life, or "
            "ten-years-or-more category)"
        ]

    @requires_score_bin
    def test_non_regression_all_other_registry_elements_unchanged(self) -> None:
        """Every disjunctive-matched element in the 26-contract registry OTHER than the 5 fixed above must
        produce a BYTE-IDENTICAL split to the pre-fix regex -- the fix changes behavior only where the
        pre-fix regex was matching "or" inside parentheses. Requires a binary that actually knows every
        contract in `_ALL_REGISTRY_CONTRACT_IDS` (see `_registry_element_descriptions`'s hard-fail guard) --
        an older/incomplete binary makes this test ERROR, not silently pass or skip."""
        fixed_keys = {
            ("bns85", "el1"), ("ipc416", "el0"), ("bns69", "el0"), ("bns46_instigation", "el0"),
            ("bnss187_extended_other", "el2"),
        }
        n_compared = 0
        for cid, element_id, desc in _registry_element_descriptions():
            n_compared += 1
            old = _pre_fix_split(desc)
            new = split_disjuncts(desc)
            if (cid, element_id) in fixed_keys:
                assert old != new, f"{cid}/{element_id} was expected to change but didn't"
            else:
                assert old == new, f"{cid}/{element_id} changed unexpectedly: {old!r} -> {new!r}"
        assert n_compared == 74  # exact: all 74 elements across all 26 registry contracts (current pinned binary), never a sample

    @requires_score_bin
    def test_paren_balance_holds_for_every_registry_element(self) -> None:
        """Every disjunct of every element in the registry has balanced parentheses -- the mechanical
        signal GATE1-PAREN-SPLIT-BUG-2026-09-26.md used to find the original 4 broken elements; this test
        makes that check permanent so a future contract with the same shape is caught automatically."""
        n_compared = 0
        for cid, element_id, desc in _registry_element_descriptions():
            n_compared += 1
            for disjunct in split_disjuncts(desc):
                assert disjunct.count("(") == disjunct.count(")"), (
                    f"{cid}/{element_id} disjunct has unbalanced parens: {disjunct!r}"
                )
        assert n_compared == 74  # exact: all 74 elements across all 26 registry contracts (current pinned binary), never a sample


class TestGate1Check:
    """Pure disjunct-max scoring (Track-C's own `gate1_score`, frozen, reused unchanged)."""

    def test_takes_the_max_over_disjuncts(self) -> None:
        scores = {"insufficiency of funds": 0.01, "account closed": 0.5}
        result = gate1_check(
            "fact text", "insufficiency of funds, or account closed",
            score_fn=lambda _f, h: scores[h], threshold=0.04, model="stub",
        )
        assert result.score == 0.5
        assert result.disjuncts == ["insufficiency of funds", "account closed"]
        assert result.passed is True

    @pytest.mark.parametrize("score,expected", [(0.04074102267622948, True), (0.04074102267622947, False)])
    def test_passed_is_score_ge_threshold_at_the_exact_boundary(self, score: float, expected: bool) -> None:
        result = gate1_check(
            "fact", "element", score_fn=lambda _f, _h: score, threshold=0.04074102267622948, model="stub",
        )
        assert result.passed is expected

    def test_no_disjunction_scores_the_element_exactly_once(self) -> None:
        calls = []

        def score_fn(fact: str, hyp: str) -> float:
            calls.append(hyp)
            return 0.5

        gate1_check("fact", "no disjunction here", score_fn=score_fn, threshold=0.04, model="stub")
        assert calls == ["no disjunction here"]

    def test_known_gap_shape_never_flips_a_correct_not_established_to_passed(self) -> None:
        """The documented non-rescue (spec §2): both broken halves of a subject-embedded disjunction score
        low (correctly not entailed, for the wrong underlying reason -- neither half is even a coherent
        clause), so the max still correctly stays below threshold. This is what "degrades gracefully" means
        in practice: it never accidentally flips to a false established."""
        element = "the consequence intended, or known to be likely, is death"
        result = gate1_check(
            "TOY: the accused struck the victim once in anger.", element,
            score_fn=lambda _f, _h: 0.001,  # both broken halves genuinely unsupported by this fact
            threshold=0.04074102267622948, model="stub",
        )
        assert result.passed is False


class TestGate1Judge:
    """Composition: wraps ANY `Judge` (a bare judge or `AndGateJudge`) the same way `AndGateJudge` itself
    wraps two judges -- never re-derives the inner judge's own decision."""

    def test_never_asks_gate1_when_inner_says_not_established(self) -> None:
        inner = _StubJudge("inner", _judgment("not_established", 0.3))
        model = _StubModel(error=AssertionError("must not be called"))
        gate = Gate1Judge(inner, model, threshold=0.04)
        out = gate.judge(REQ)
        assert out.status == "not_established"
        assert out.gate1_score is None and out.gate1_disjuncts is None
        assert model.calls == []

    def test_established_and_gate1_passes_stays_established(self) -> None:
        inner = _StubJudge("inner", _judgment("established", 0.97))
        model = _StubModel(scores={REQ_SIMPLE.element: 0.5})
        gate = Gate1Judge(inner, model, threshold=0.04)
        out = gate.judge(REQ_SIMPLE)
        assert out.status == "established"
        assert out.gate1_score == 0.5
        assert out.gate1_disjuncts == [REQ_SIMPLE.element]
        assert out.vetoed_by is None
        assert model.calls == [(REQ_SIMPLE.facts[0][1], REQ_SIMPLE.element)]

    def test_established_but_gate1_fails_vetoes(self) -> None:
        inner = _StubJudge("inner", _judgment("established", 0.97))
        model = _StubModel(scores={REQ_SIMPLE.element: 0.001})
        gate = Gate1Judge(inner, model, threshold=0.04)
        out = gate.judge(REQ_SIMPLE)
        assert out.status == "not_established"
        assert out.vetoed_by == "gate1"
        assert out.gate1_score == 0.001
        assert out.gate1_skip_reason is None  # a real score, not a model failure

    def test_gate1_model_error_fails_closed(self) -> None:
        inner = _StubJudge("inner", _judgment("established", 0.97))
        model = _StubModel(error=RuntimeError("model not loaded"))
        gate = Gate1Judge(inner, model, threshold=0.04)
        out = gate.judge(REQ)
        assert out.status == "not_established"
        assert out.vetoed_by == "gate1"
        assert out.gate1_skip_reason is not None
        assert out.gate1_skip_reason.startswith("gate1_unavailable")
        assert out.gate1_score is None  # no score to report -- the model itself never answered

    def test_missing_fact_id_passes_through_unchanged(self) -> None:
        """An established judgment naming a fact id not in `request.facts` is not Gate 1's problem to solve --
        the quote check downstream rejects it regardless of what Gate 1 would have said."""
        inner = _StubJudge("inner", _judgment("established", 0.97, fact_id="F99", quote="nonexistent"))
        model = _StubModel(error=AssertionError("must not be called"))
        gate = Gate1Judge(inner, model, threshold=0.04)
        out = gate.judge(REQ)
        assert out.status == "established"  # Gate 1 did not touch it
        assert out.gate1_score is None
        assert model.calls == []
