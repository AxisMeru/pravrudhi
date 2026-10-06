"""B4 (research/clef-decoder): score the judge's fact id as an ID_REF enum over the request's fact ids instead of
parsing it from greedy text. No model/network: the scorer is an injected callable. Claim tier: unit-tested with
constructed inputs only; the vLLM echo parsing is checked against a canned response in the OpenAI echo shape, NOT
against a live server."""

from __future__ import annotations

import math

import pytest

from pravrudhi.application.nyaya_judges import ElementJudgment, JudgeRequest
from pravrudhi.application.typed.id_ref import (
    IdRefDecodeError,
    continuation_logprob_from_echo,
    rescore_fact_id,
    score_id_ref,
)
from pravrudhi.application.typed.schema import Field, FieldKind

FIELD = Field("fact", FieldKind.ID_REF, candidates=("F1", "F2", "F10"))


def _scorer(table: dict[str, float]):
    def scorer(prompt: str, continuation: str) -> float:
        return table[continuation]

    return scorer


class TestScoreIdRef:
    def test_softmax_over_candidates_with_terminator_and_mass(self) -> None:
        lp = {" F1:": math.log(0.6), " F2:": math.log(0.3), " F10:": math.log(0.05)}
        res = score_id_ref(FIELD, prefix="p", scorer=_scorer(lp), mass_floor=0.5)
        assert res.best == "F1"
        assert res.probs["F1"] == pytest.approx(0.6 / 0.95)
        assert res.mass == pytest.approx(0.95)
        assert res.margin == pytest.approx((0.6 - 0.3) / 0.95)

    def test_f1_vs_f10_are_distinguished_by_the_terminator(self) -> None:
        seen: list[str] = []

        def scorer(prompt: str, continuation: str) -> float:
            seen.append(continuation)
            return -1.0 - len(seen)  # distinct values, so no tie

        score_id_ref(FIELD, prefix="p", scorer=scorer, mass_floor=0.0)
        assert seen == [" F1:", " F2:", " F10:"]

    def test_low_mass_means_the_model_wanted_something_else_so_raise(self) -> None:
        lp = {" F1:": math.log(0.01), " F2:": math.log(0.01), " F10:": math.log(0.01)}
        with pytest.raises(IdRefDecodeError, match="mass"):
            score_id_ref(FIELD, prefix="p", scorer=_scorer(lp), mass_floor=0.5)

    @pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
    def test_non_finite_logprob_raises(self, bad: float) -> None:
        lp = {" F1:": bad, " F2:": -1.0, " F10:": -1.0}
        with pytest.raises(IdRefDecodeError):
            score_id_ref(FIELD, prefix="p", scorer=_scorer(lp), mass_floor=0.0)

    def test_positive_logprob_is_impossible_so_raises(self) -> None:
        with pytest.raises(IdRefDecodeError):
            score_id_ref(FIELD, prefix="p", scorer=lambda p, c: 0.1, mass_floor=0.0)

    def test_requires_an_id_ref_field_and_a_valid_floor(self) -> None:
        with pytest.raises(ValueError):
            score_id_ref(Field("x", FieldKind.FREE_TEXT), prefix="p", scorer=lambda p, c: -1.0, mass_floor=0.5)
        for floor in (-0.1, 1.5, math.nan):
            with pytest.raises(ValueError):
                score_id_ref(FIELD, prefix="p", scorer=lambda p, c: -1.0, mass_floor=floor)

    def test_exact_tie_raises_rather_than_picking_one(self) -> None:
        with pytest.raises(IdRefDecodeError, match="tie"):
            score_id_ref(FIELD, prefix="p", scorer=lambda p, c: -1.0, mass_floor=0.0)


def _req(facts) -> JudgeRequest:
    return JudgeRequest("c", "el", False, "statute", "narr", tuple(facts))


def _est(fact_id=None) -> ElementJudgment:
    return ElementJudgment("established", 0.99, fact_id, None, None, raw=" established F2: x")


class TestRescoreFactId:
    def test_enum_choice_replaces_the_parsed_id_and_names_the_whole_fact(self) -> None:
        req = _req([("F1", "one"), ("F2", "two"), ("F_narrative", "narr")])
        lp = {" F1:": math.log(0.8), " F2:": math.log(0.1)}
        out, choice = rescore_fact_id(_est("F2"), req, prompt="P", scorer=_scorer(lp), mass_floor=0.5)
        assert out.fact_id == "F1" and out.quote == "one" and out.quote_source == "whole_fact"
        assert choice is not None and choice.best == "F1"

    def test_narrative_is_never_a_candidate(self) -> None:
        seen: list[str] = []

        def scorer(prompt: str, continuation: str) -> float:
            seen.append(continuation)
            return -1.0 if continuation == " F1:" else -2.0

        rescore_fact_id(_est(), _req([("F1", "a"), ("F_narrative", "n")]), prompt="P", scorer=scorer, mass_floor=0.0)
        assert seen == [" F1:"]

    def test_scores_after_the_decision_word(self) -> None:
        prompts: list[str] = []

        def scorer(prompt: str, continuation: str) -> float:
            prompts.append(prompt)
            return -1.0

        rescore_fact_id(_est(), _req([("F1", "a")]), prompt="Answer:", scorer=scorer, mass_floor=0.0)
        assert prompts == ["Answer: established"]

    def test_not_established_is_returned_untouched_and_never_scored(self) -> None:
        j = ElementJudgment("not_established", 0.01)
        out, choice = rescore_fact_id(j, _req([("F1", "a")]), prompt="P", scorer=lambda p, c: 1 / 0, mass_floor=0.5)
        assert out is j and choice is None

    @pytest.mark.parametrize("facts", [[], [("F_narrative", "n")]])
    def test_established_with_no_candidate_facts_raises_never_keeps_the_parsed_id(self, facts) -> None:
        j = ElementJudgment("established", 0.99, "F99", "made up", "whole_fact", raw=" established F99: made up")
        with pytest.raises(IdRefDecodeError, match="no candidate fact ids"):
            rescore_fact_id(j, _req(facts), prompt="P", scorer=lambda p, c: 1 / 0, mass_floor=0.5)

    def test_decode_errors_propagate_never_fall_back_to_the_parsed_id(self) -> None:
        with pytest.raises(IdRefDecodeError):
            rescore_fact_id(_est("F1"), _req([("F1", "a")]), prompt="P", scorer=lambda p, c: math.nan, mass_floor=0.5)


class TestEchoParsing:
    def _resp(self, tokens, lps, offsets):
        return {"choices": [{"logprobs": {"tokens": tokens, "token_logprobs": lps, "text_offset": offsets}}]}

    def test_sums_only_tokens_that_start_at_or_after_the_prefix_end(self) -> None:
        # prefix "A established" (13 chars), continuation " F1:" -> tokens " F","1",":"
        r = self._resp(["A", " established", " F", "1", ":"], [None, -0.5, -0.2, -0.3, -0.1], [0, 1, 13, 15, 16])
        assert continuation_logprob_from_echo(r, prefix_len=13, end_len=17) == pytest.approx(-0.6)

    def test_tokens_the_server_generated_after_the_continuation_are_excluded(self) -> None:
        r = self._resp(["A", " F", "1", ":", "0"], [None, -0.2, -0.3, -0.1, -9.0], [0, 1, 3, 4, 5])
        assert continuation_logprob_from_echo(r, prefix_len=1, end_len=5) == pytest.approx(-0.6)

    def test_a_token_straddling_the_boundary_raises(self) -> None:
        r = self._resp(["A", " establishedF", "1"], [None, -0.5, -0.3], [0, 1, 14])
        with pytest.raises(IdRefDecodeError, match="straddle"):
            continuation_logprob_from_echo(r, prefix_len=13, end_len=15)

    @pytest.mark.parametrize("resp", [{}, {"choices": []}, {"choices": [{"logprobs": None}]},
                                      {"choices": [{"logprobs": {"tokens": [], "token_logprobs": [], "text_offset": []}}]}])
    def test_malformed_or_empty_raises(self, resp) -> None:
        with pytest.raises(IdRefDecodeError):
            continuation_logprob_from_echo(resp, prefix_len=3, end_len=9)

    def test_null_logprob_inside_the_continuation_raises(self) -> None:
        r = self._resp(["A", " F"], [None, None], [0, 1])
        with pytest.raises(IdRefDecodeError):
            continuation_logprob_from_echo(r, prefix_len=1, end_len=3)

    def test_a_positive_continuation_token_logprob_raises(self) -> None:
        r = self._resp(["A", " F", "1"], [None, 0.3, -0.5], [0, 1, 3])
        with pytest.raises(IdRefDecodeError, match="<= 0"):
            continuation_logprob_from_echo(r, prefix_len=1, end_len=4)

    @pytest.mark.parametrize("bad", [math.nan, math.inf, "x", True])
    def test_a_non_finite_or_non_numeric_token_logprob_raises(self, bad) -> None:
        r = self._resp(["A", " F"], [None, bad], [0, 1])
        with pytest.raises(IdRefDecodeError):
            continuation_logprob_from_echo(r, prefix_len=1, end_len=3)

    @pytest.mark.parametrize("off", [None, "1", 1.5])
    def test_a_bad_offset_raises_decode_error_not_type_error(self, off) -> None:
        r = self._resp(["A", " F"], [None, -0.2], [0, off])
        with pytest.raises(IdRefDecodeError):
            continuation_logprob_from_echo(r, prefix_len=1, end_len=3)
