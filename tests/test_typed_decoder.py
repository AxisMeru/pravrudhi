"""The TypedDecoder protocol, its vLLM/SGLang adapters, and the decision-field scoring primitive (T1,
docs/decisions/TYPED-LAYER-PLAN-2026-09-24.md design principle 1: an enum/bool field is decided by scoring
each option's log-probability, never by sampling). The HTTP transport is replaced by a test double
throughout; no model is called.
"""

from __future__ import annotations

import math

import pytest

from pravrudhi.application.typed.decoder import DecodeError, SGLangDecoder, VLLMDecoder, score_decision
from pravrudhi.application.typed.schema import Field, FieldKind, bool_field
from pravrudhi.models.openai_compat import CompletionResult

STATUS = bool_field("status", true_tokens=(" established", "established"), false_tokens=(" not", "not"))


def _result(top: dict[str, float], text: str = "established F1") -> CompletionResult:
    return CompletionResult(text=text, model="m", top_logprobs=[top], wall_s=0.1)


class TestScoreDecisionMatchesHouseJudgeAlgebra:
    """`score_decision` generalizes nyaya_judges.p_established_from_top_logprobs to N options; specialized to
    the exact 2-option established/not_established case it must reproduce the same softmax, since T1's pass
    bar is 0 decision flips and max|Δp| <= 1e-6 against the current HouseJudge."""

    def test_only_established_token_present_returns_a_lower_bound_not_a_clamp_to_one(self) -> None:
        # G-28 (2026-09-28): the OLD behaviour clamped this to exactly 1.0. Correct value is a LOWER
        # bound: "false"'s true logprob is <= min(top) (-2.0 here), so bounded there (its
        # least-negative-possible, worst-case value): scores["true"] = 1/(1+exp(-2.0-(-0.1))).
        top = {" established": -0.1, " F": -2.0}
        scores, missing = score_decision(_result(top), STATUS)
        assert missing == frozenset({"false"})
        assert scores["true"] == pytest.approx(1 / (1 + math.exp(-2.0 - (-0.1))))
        assert scores["true"] < 1.0  # never the old hard clamp

    def test_only_not_token_present_returns_an_upper_bound_not_a_clamp_to_zero(self) -> None:
        # Mirror case: ' established' missing, true's true logprob is <= min(top) (-2.0), bound there:
        # scores["true"] = 1/(1+exp(-0.1-(-2.0))).
        top = {" not": -0.1, " F": -2.0}
        scores, missing = score_decision(_result(top), STATUS)
        assert missing == frozenset({"true"})
        assert scores["true"] == pytest.approx(1 / (1 + math.exp(-0.1 - (-2.0))))
        assert scores["true"] > 0.0  # never the old hard clamp

    def test_matches_the_original_two_way_softmax_formula_across_many_logprob_pairs(self) -> None:
        from pravrudhi.application.nyaya_judges import p_established_from_top_logprobs

        # Pairs chosen to clear nyaya_judges' own label-mass guard (2026-09-28): exp(est)+exp(neg) >=
        # 0.5 and the higher of the two is top-1 -- these are realistic, confident-decision logprobs,
        # not the guard's own edge cases (covered separately in test_nyaya_judges.py).
        for est, neg in [(-0.5, -3.0), (-2.0, -0.1), (-0.4, -0.4), (-0.1, -3.0), (-3.0, -0.1), (-0.05, -0.6)]:
            top = {" established": est, " not": neg}
            original, clamp = p_established_from_top_logprobs(top)
            assert clamp == "none"  # both tokens present in every case here
            scores, missing = score_decision(_result(top), STATUS)
            assert missing == frozenset()
            assert abs(original - scores["true"]) <= 1e-12, (est, neg, original, scores["true"])

    def test_prefers_the_leading_space_variant_and_the_bare_variant_equally(self) -> None:
        # Either surface form of "established" is read, exactly like the original's max() over both.
        a = score_decision(_result({"established": -0.2, " not": -1.0}), STATUS)[0]["true"]
        b = score_decision(_result({" established": -0.2, " not": -1.0}), STATUS)[0]["true"]
        assert a == pytest.approx(b)

    def test_neither_option_token_present_raises_rather_than_guessing(self) -> None:
        with pytest.raises(DecodeError, match="status"):
            score_decision(_result({"maybe": -0.1}), STATUS)

    def test_no_top_logprobs_at_all_raises(self) -> None:
        res = CompletionResult(text="x", model="m", top_logprobs=[], wall_s=0.1)
        with pytest.raises(DecodeError, match="no logprobs"):
            score_decision(res, STATUS)

    def test_refuses_a_non_decision_field(self) -> None:
        with pytest.raises(ValueError, match="enum/bool"):
            score_decision(_result({" established": -0.1}), Field(name="n", kind=FieldKind.INT))


class TestScoreDecisionGeneralizesBeyondTwoOptions:
    THREE_WAY = Field(
        name="outcome",
        kind=FieldKind.ENUM,
        options={"proof": ("proof",), "denial": ("denial",), "abstain": ("abstain",)},
    )

    def test_three_options_sum_to_one(self) -> None:
        scores, missing = score_decision(_result({"proof": -0.2, "denial": -1.5, "abstain": -3.0}), self.THREE_WAY)
        assert missing == frozenset()
        assert sum(scores.values()) == pytest.approx(1.0)
        assert scores["proof"] > scores["denial"] > scores["abstain"]

    def test_an_absent_option_gets_a_bound_not_a_clamp_to_zero(self) -> None:
        # G-28 (2026-09-28): the OLD behaviour scored a missing option exactly 0.0. "abstain"'s true
        # logprob is <= min(top) (-1.5, "denial"'s own value here) -- bounded there, tying it with
        # "denial" exactly, not zeroed out.
        scores, missing = score_decision(_result({"proof": -0.2, "denial": -1.5}), self.THREE_WAY)
        assert missing == frozenset({"abstain"})
        assert scores["abstain"] == pytest.approx(scores["denial"])
        assert scores["abstain"] > 0.0  # never the old hard clamp


class TestVLLMDecoder:
    def test_complete_delegates_to_the_injected_transport_with_the_given_params(self) -> None:
        calls = []

        def fake_complete(prompt: str, *, max_tokens: int, temperature: float, logprobs: int | None) -> CompletionResult:
            calls.append((prompt, max_tokens, temperature, logprobs))
            return _result({" established": -0.1})

        decoder = VLLMDecoder(model="injected", complete=fake_complete)
        res = decoder.complete("Statute: ...", max_tokens=30, temperature=0.0, logprobs=20)
        assert calls == [("Statute: ...", 30, 0.0, 20)]
        assert res.top_logprobs == [{" established": -0.1}]

    def test_name_identifies_the_backend(self) -> None:
        decoder = VLLMDecoder(model="m", complete=lambda *a, **k: _result({}))
        assert decoder.name == "vllm"

    def test_needs_a_base_url_or_an_injected_transport(self) -> None:
        with pytest.raises(ValueError, match="base_url"):
            VLLMDecoder()


class TestSGLangDecoder:
    def test_complete_delegates_to_the_injected_transport(self) -> None:
        calls = []

        def fake_complete(prompt: str, *, max_tokens: int, temperature: float, logprobs: int | None) -> CompletionResult:
            calls.append(prompt)
            return _result({" established": -0.1})

        decoder = SGLangDecoder(model="injected", complete=fake_complete)
        decoder.complete("p", max_tokens=10, temperature=0.0, logprobs=5)
        assert calls == ["p"]

    def test_name_identifies_the_backend(self) -> None:
        decoder = SGLangDecoder(model="m", complete=lambda *a, **k: _result({}))
        assert decoder.name == "sglang"

    def test_needs_a_base_url_or_an_injected_transport(self) -> None:
        with pytest.raises(ValueError, match="base_url"):
            SGLangDecoder()
