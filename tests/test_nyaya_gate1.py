"""Gate 1 (Track-C, GATE1-PRODUCT-WIRING-SPEC-2026-09-26.md): a third gate, pure NLI entailment check, run
only on an element the judge(s) above already established. `split_disjuncts`/`gate1_check` are pure functions
(no model call -- `gate1_check` takes an injected `score_fn`); `Gate1Judge` wraps a `Judge` double the same
way `test_nyaya_and_gate.py` wraps two for `AndGateJudge`, never a real NLI model.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from pravrudhi.application.nyaya_judges import (
    ElementJudgment,
    Gate1Judge,
    JudgeRequest,
    gate1_check,
    split_disjuncts,
)

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
