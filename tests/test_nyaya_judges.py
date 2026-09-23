"""Element judges for the Nyaya agentic loop: the house judge (vLLM first-token logprob) and a frontier
judge (any `panel` vendor). Both return the same per-element shape and both go through the same quote check
downstream. The HTTP transport is replaced by a test double here; no model is called.

Every fact and statute string is hand-written toy text (the statute strings are public statute text).
"""

from __future__ import annotations

import json
import math
from typing import Any
from unittest import mock

import pytest

from pravrudhi.application import panel
from pravrudhi.application.nyaya_judges import (
    FrontierJudge,
    HouseJudge,
    JudgeOutputError,
    JudgeRequest,
    build_house_prompt,
    p_established_from_top_logprobs,
    parse_house_span,
)
from pravrudhi.models.openai_compat import ChatClient, CompletionResult

STATUTE = (
    "Whoever, being the husband or the relative of the husband of a woman, subjects such woman to cruelty shall be punished."
)
REQ = JudgeRequest(
    contract_id="bns85",
    element="the accused is the husband, or a relative of the husband, of the woman",
    is_denial=False,
    statute=STATUTE,
    narrative="TOY: Arun and Bela married in 2019; from 2021 Arun beat Bela whenever she refused to ask her father for money.",
    facts=(("F1", "TOY: Arun married Bela in 2019."), ("F2", "TOY: Arun beat Bela whenever she refused to ask for money.")),
)


class TestHousePrompt:
    def test_mirrors_the_training_template_exactly(self) -> None:
        assert build_house_prompt(REQ, statute_chars=600) == (
            f"Statute: {STATUTE}\n"
            f"Scenario: {REQ.narrative}\n"
            f"Element to judge: {REQ.element}\n"
            "Available facts:\n"
            "[F1] TOY: Arun married Bela in 2019.\n"
            "[F2] TOY: Arun beat Bela whenever she refused to ask for money.\n"
            "Answer:"
        )

    def test_statute_is_truncated_like_the_training_prompt(self) -> None:
        prompt = build_house_prompt(REQ, statute_chars=20)
        assert prompt.startswith(f"Statute: {STATUTE[:20]}\nScenario:")


class TestFirstTokenScore:
    def test_softmax_of_established_vs_not(self) -> None:
        p = p_established_from_top_logprobs({" established": -0.1, " not": -2.5, " F": -6.0})
        assert p == pytest.approx(1 / (1 + math.exp(-2.4)))

    def test_takes_the_max_over_spaced_and_bare_variants(self) -> None:
        p = p_established_from_top_logprobs({" established": -3.0, "established": -1.0, " not": -1.0, "not": -4.0})
        assert p == pytest.approx(0.5)

    def test_absent_not_token_is_minus_infinity(self) -> None:
        assert p_established_from_top_logprobs({" established": -0.01}) == 1.0

    def test_neither_token_is_an_error_not_a_guess(self) -> None:
        with pytest.raises(JudgeOutputError):
            p_established_from_top_logprobs({" F": -0.1, " R": -2.0})


class TestParseHouseSpan:
    def test_reads_fact_and_offsets(self) -> None:
        assert parse_house_span(" established F2:5:30") == ("F2", 5, 30)

    def test_underscored_fact_ids_parse(self) -> None:
        assert parse_house_span("established F_el0:0:333\n") == ("F_el0", 0, 333)

    @pytest.mark.parametrize("text", [" not_established", " established", " established F2", "garbage"])
    def test_no_span_is_none(self, text: str) -> None:
        assert parse_house_span(text) is None


def _completion(text: str, top: dict[str, float]) -> CompletionResult:
    return CompletionResult(text=text, model="judge", top_logprobs=[top], wall_s=0.01, finish_reason="stop")


class _FakeComplete:
    def __init__(self, result: CompletionResult) -> None:
        self.result = result
        self.prompts: list[str] = []

    def __call__(self, prompt: str) -> CompletionResult:
        self.prompts.append(prompt)
        return self.result


class TestHouseJudge:
    def test_established_above_tau_carries_the_span_and_its_slice(self) -> None:
        fake = _FakeComplete(_completion(" established F1:5:22", {" established": -0.05, " not": -3.0}))
        j = HouseJudge(complete=fake, tau=0.74, statute_chars=600).judge(REQ)
        assert j.status == "established"
        assert j.p_established > 0.74
        assert (j.fact_id, j.start, j.end) == ("F1", 5, 22)
        assert j.quote == "Arun married Bela"
        assert fake.prompts == [build_house_prompt(REQ, statute_chars=600)]

    def test_below_tau_is_not_established_even_when_argmax_says_established(self) -> None:
        # p = sigmoid(0.5) ~ 0.62: the model's own argmax is " established", tau is not met.
        fake = _FakeComplete(_completion(" established F1:5:22", {" established": -0.5, " not": -1.0}))
        j = HouseJudge(complete=fake, tau=0.74, statute_chars=600).judge(REQ)
        assert j.status == "not_established"
        assert 0.5 < j.p_established < 0.74
        assert j.fact_id is None and j.quote is None

    def test_out_of_bounds_span_is_reported_as_given_with_no_quote(self) -> None:
        """The model named F1:0:63 of a 32-character fact (seen live on the 5090 server). The judge reports the
        offsets it was given and NO quote -- it never clips them into range; the quote check then rejects."""
        fake = _FakeComplete(_completion(" established F1:0:63", {" established": -0.05, " not": -3.0}))
        j = HouseJudge(complete=fake, tau=0.74, statute_chars=600).judge(REQ)
        assert j.status == "established"
        assert (j.fact_id, j.start, j.end, j.quote) == ("F1", 0, 63, None)

    def test_established_without_a_span_keeps_status_and_no_span(self) -> None:
        fake = _FakeComplete(_completion(" established", {" established": -0.01, " not": -5.0}))
        j = HouseJudge(complete=fake, tau=0.74, statute_chars=600).judge(REQ)
        assert j.status == "established"
        assert j.fact_id is None

    def test_no_logprobs_is_a_judge_output_error(self) -> None:
        fake = _FakeComplete(CompletionResult(text=" not", model="m", top_logprobs=[], wall_s=0.0, finish_reason="stop"))
        with pytest.raises(JudgeOutputError):
            HouseJudge(complete=fake, tau=0.74, statute_chars=600).judge(REQ)


class _Resp:
    status = 200

    def __init__(self, body: dict[str, Any]) -> None:
        self.body = body

    def __enter__(self) -> _Resp:
        return self

    def __exit__(self, *a: object) -> bool:
        return False

    def read(self) -> bytes:
        return json.dumps(self.body).encode()


class TestClientCompletions:
    def test_complete_sends_raw_prompt_with_logprobs_and_reads_first_token_top(self) -> None:
        captured: dict[str, Any] = {}
        body = {
            "model": "judge",
            "choices": [
                {
                    "text": " established F1:0:5",
                    "finish_reason": "length",
                    "logprobs": {"tokens": [" established"], "top_logprobs": [{" established": -0.1, " not": -2.0}]},
                }
            ],
        }

        def fake_open(req: Any, timeout: float | None = None) -> _Resp:
            captured["url"] = req.full_url
            captured["body"] = json.loads(req.data)
            return _Resp(body)

        with mock.patch("urllib.request.urlopen", fake_open):
            res = ChatClient("http://h/v1", model="judge").complete("P", max_tokens=30, logprobs=20)
        assert captured["url"] == "http://h/v1/completions"
        assert captured["body"] == {"model": "judge", "prompt": "P", "max_tokens": 30, "temperature": 0.0, "logprobs": 20}
        assert res.text == " established F1:0:5"
        assert res.top_logprobs == [{" established": -0.1, " not": -2.0}]

    def test_list_models_reads_ids(self) -> None:
        def fake_open(req: Any, timeout: float | None = None) -> _Resp:
            assert req.full_url == "http://h/v1/models"
            return _Resp({"object": "list", "data": [{"id": "a"}, {"id": "b"}]})

        with mock.patch("urllib.request.urlopen", fake_open):
            assert ChatClient("http://h/v1").list_models() == ["a", "b"]


def _answer(text: str) -> panel.Answer:
    return panel.Answer("v", "cli", "m", "", text, 0.1, None, None)


class TestFrontierJudge:
    VENDOR = panel.VENDORS["claude-cli"]

    def test_parses_json_and_reports_a_hard_probability(self) -> None:
        reply = json.dumps({"status": "established", "fact_id": "F1", "quote": "Arun married Bela", "start": 5, "end": 22})
        prompts: list[str] = []

        def ask(v: panel.Vendor, p: str) -> panel.Answer:
            prompts.append(p)
            return _answer(f"Here you go:\n```json\n{reply}\n```")

        j = FrontierJudge(self.VENDOR, ask_fn=ask).judge(REQ)
        assert (j.status, j.p_established, j.fact_id, j.quote, j.start, j.end) == (
            "established",
            1.0,
            "F1",
            "Arun married Bela",
            5,
            22,
        )
        assert REQ.element in prompts[0] and "[F2]" in prompts[0] and STATUTE in prompts[0]

    def test_not_established_has_zero_probability_and_no_span(self) -> None:
        j = FrontierJudge(self.VENDOR, ask_fn=lambda v, p: _answer('{"status": "not_established"}')).judge(REQ)
        assert (j.status, j.p_established, j.fact_id) == ("not_established", 0.0, None)

    def test_quote_is_passed_through_verbatim_never_recomputed(self) -> None:
        reply = json.dumps({"status": "established", "fact_id": "F1", "quote": "Arun wed Bela", "start": 5, "end": 22})
        j = FrontierJudge(self.VENDOR, ask_fn=lambda v, p: _answer(reply)).judge(REQ)
        assert j.quote == "Arun wed Bela"

    @pytest.mark.parametrize("text", ["no json here", '{"status": "maybe"}', "[1, 2]", '{"status": "established", "start": "x"}'])
    def test_unusable_reply_is_a_judge_output_error(self, text: str) -> None:
        with pytest.raises(JudgeOutputError):
            FrontierJudge(self.VENDOR, ask_fn=lambda v, p: _answer(text)).judge(REQ)
