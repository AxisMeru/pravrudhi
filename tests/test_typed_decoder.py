"""The TypedDecoder protocol, its vLLM/SGLang adapters, and the decision-field scoring primitive (T1,
docs/decisions/TYPED-LAYER-PLAN-2026-09-24.md design principle 1: an enum/bool field is decided by scoring
each option's log-probability, never by sampling). The HTTP transport is replaced by a test double
throughout; no model is called.
"""

from __future__ import annotations

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

    def test_only_established_token_present_scores_true_at_1(self) -> None:
        scores = score_decision(_result({" established": -0.1}), STATUS)
        assert scores == {"true": pytest.approx(1.0), "false": pytest.approx(0.0)}

    def test_only_not_token_present_scores_true_at_0(self) -> None:
        scores = score_decision(_result({" not": -0.1}), STATUS)
        assert scores == {"true": pytest.approx(0.0), "false": pytest.approx(1.0)}

    def test_matches_the_original_two_way_softmax_formula_across_many_logprob_pairs(self) -> None:
        from pravrudhi.application.nyaya_judges import p_established_from_top_logprobs

        for est, neg in [(-0.5, -3.0), (-2.0, -0.1), (-1.0, -1.0), (-10.0, -0.001), (-0.001, -10.0), (-5.5, -5.5)]:
            top = {" established": est, " not": neg}
            original = p_established_from_top_logprobs(top)
            generalized = score_decision(_result(top), STATUS)["true"]
            assert abs(original - generalized) <= 1e-12, (est, neg, original, generalized)

    def test_prefers_the_leading_space_variant_and_the_bare_variant_equally(self) -> None:
        # Either surface form of "established" is read, exactly like the original's max() over both.
        a = score_decision(_result({"established": -0.2, " not": -1.0}), STATUS)["true"]
        b = score_decision(_result({" established": -0.2, " not": -1.0}), STATUS)["true"]
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
        scores = score_decision(_result({"proof": -0.2, "denial": -1.5, "abstain": -3.0}), self.THREE_WAY)
        assert sum(scores.values()) == pytest.approx(1.0)
        assert scores["proof"] > scores["denial"] > scores["abstain"]

    def test_an_absent_option_scores_zero_not_an_error_if_another_option_is_present(self) -> None:
        scores = score_decision(_result({"proof": -0.2, "denial": -1.5}), self.THREE_WAY)
        assert scores["abstain"] == 0.0


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
