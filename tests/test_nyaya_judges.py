"""Element judges for the Nyaya agentic loop: the house judge (vLLM first-token logprob) and a frontier
judge (any `panel` vendor). Both return the same per-element shape and both go through the same quote check
downstream. The HTTP transport is replaced by a test double here; no model is called.

Every fact and statute string is hand-written toy text (the statute strings are public statute text).
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable
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
    parse_house_fact_id,
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

    def test_a_narrative_fact_is_never_shown_twice(self) -> None:
        """The training data (element_judgment_combined.jsonl) carries the narrative as a fact row too, id
        `F_narrative` -- but the judge was TRAINED on prompts that drop it from `Available facts:` (it is
        already the `Scenario:` line). A caller that hands `build_house_prompt` a `request.facts` including
        an `F_narrative` entry -- as any consumer built straight off that jsonl schema would, batch scoring
        included -- must not see it doubled: `Scenario:` still carries the narrative, `Available facts:`
        does not, and every other fact is unchanged and in the caller's order."""
        req_with_narrative = JudgeRequest(
            contract_id=REQ.contract_id,
            element=REQ.element,
            is_denial=REQ.is_denial,
            statute=REQ.statute,
            narrative=REQ.narrative,
            facts=(("F_narrative", REQ.narrative), *REQ.facts),
        )
        assert build_house_prompt(req_with_narrative, statute_chars=600) == build_house_prompt(REQ, statute_chars=600)


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


class TestParseHouseFactId:
    def test_reads_the_fact_id_and_ignores_the_offsets(self) -> None:
        assert parse_house_fact_id(" established F2:5:30") == "F2"

    def test_underscored_fact_ids_parse(self) -> None:
        assert parse_house_fact_id("established F_el0:0:333\n") == "F_el0"

    def test_a_fact_id_with_no_offsets_parses(self) -> None:
        assert parse_house_fact_id(" established F2") == "F2"

    @pytest.mark.parametrize("text", [" not_established", " established", "garbage"])
    def test_no_fact_is_none(self, text: str) -> None:
        assert parse_house_fact_id(text) is None

    def test_f_narrative_is_never_a_returned_fact_id(self) -> None:
        """`F_narrative` is never one of the facts the model is shown (`build_house_prompt` drops it, above)
        -- so a completion naming it is not a legitimate answer the model could have given by looking at
        `Available facts:`. Read as `None` (same as no fact id at all) rather than as a real fact id, so a
        caller whose `request.facts` still happens to carry an `F_narrative` entry (e.g. handed the raw
        jsonl row) can never have it accepted downstream as the evidence for an element."""
        assert parse_house_fact_id(" established F_narrative:0:80") is None
        assert parse_house_fact_id("established F_narrative") is None


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
    def test_established_above_tau_quotes_the_whole_named_fact(self) -> None:
        """The house model was trained to name a fact and offsets, never quote text. With model offsets no
        longer used, its verbatim claim is the fact it named, whole -- recorded as `quote_source`."""
        fake = _FakeComplete(_completion(" established F1:5:22", {" established": -0.05, " not": -3.0}))
        j = HouseJudge(complete=fake, tau=0.74, statute_chars=600).judge(REQ)
        assert j.status == "established"
        assert j.p_established > 0.74
        assert (j.fact_id, j.quote, j.quote_source) == ("F1", "TOY: Arun married Bela in 2019.", "whole_fact")
        assert fake.prompts == [build_house_prompt(REQ, statute_chars=600)]

    def test_bearer_key_added_to_authorization_header(self) -> None:
        """Bearer API key support for serverless endpoints."""
        captured: dict[str, Any] = {}
        result = _completion(" established F1:5:22", {" established": -0.05, " not": -3.0})

        def fake_open(req: Any, timeout: float | None = None) -> _Resp:
            captured["auth_header"] = req.get_header("Authorization")
            return _Resp({
                "model": "judge",
                "choices": [{
                    "text": result.text,
                    "finish_reason": "stop",
                    "logprobs": {"top_logprobs": result.top_logprobs}
                }]
            })

        with mock.patch("urllib.request.urlopen", fake_open):
            client = ChatClient("http://h/v1", model="judge", api_key="test_bearer_key_12345")
            client.complete("test prompt", max_tokens=30, logprobs=20)

        assert captured["auth_header"] == "Bearer test_bearer_key_12345"

    def test_fallback_list_from_config(self) -> None:
        """Load judge base_urls with fallback from config."""
        # Mock clients to avoid real network calls
        def make_fake_complete(idx: int) -> Callable[[str], CompletionResult]:
            def fake(prompt: str) -> CompletionResult:
                result = _completion(" established F1:0:5", {" established": -0.1, " not": -2.0})
                object.__setattr__(result, "backend_index", idx)
                return result
            return fake

        # Test config loading
        config = {
            "statute_chars": 600,
            "base_url": "https://api.runpod.io/v2/endpoint1/openai/v1",
            "model": "judge-model",
            "max_tokens": 30,
            "top_logprobs": 20,
            "timeout_s": 60,
            "base_urls_fallback": [
                "http://127.0.0.1:8110/v1",
            ],
            "api_key": "test_key"
        }

        # This should load with fallback URLs
        j = HouseJudge.from_config_with_fallback(config, tau=0.5)
        assert j.primary_base_url == config["base_url"]
        assert j.fallback_urls == config.get("base_urls_fallback", [])
        assert j.api_key == config["api_key"]
        assert len(j.clients) == 2  # primary + one fallback

    def test_api_key_from_environment_variable(self) -> None:
        """Bearer token from NYAYA_HOUSE_JUDGE_API_KEY env var."""
        import os
        config = {
            "statute_chars": 600,
            "base_url": "https://api.runpod.io/v2/endpoint1/openai/v1",
            "model": "judge-model",
            "max_tokens": 30,
            "top_logprobs": 20,
            "timeout_s": 60,
        }

        # Test: env var takes precedence over config
        os.environ["NYAYA_HOUSE_JUDGE_API_KEY"] = "env_bearer_key"
        try:
            j = HouseJudge.from_config(config, tau=0.5)
            assert j.api_key == "env_bearer_key"

            # Test with config api_key (env var still wins)
            config["api_key"] = "config_bearer_key"
            j = HouseJudge.from_config(config, tau=0.5)
            assert j.api_key == "env_bearer_key"

            # Test env var with from_config_with_fallback
            j = HouseJudge.from_config_with_fallback(config, tau=0.5)
            assert j.api_key == "env_bearer_key"
        finally:
            del os.environ["NYAYA_HOUSE_JUDGE_API_KEY"]

        # Test: config api_key used when env var not set
        j = HouseJudge.from_config(config, tau=0.5)
        assert j.api_key == "config_bearer_key"

    def test_unknown_fact_id_is_reported_with_no_quote(self) -> None:
        fake = _FakeComplete(_completion(" established F_el0:0:40", {" established": -0.05, " not": -3.0}))
        j = HouseJudge(complete=fake, tau=0.74, statute_chars=600).judge(REQ)
        assert (j.status, j.fact_id, j.quote) == ("established", "F_el0", None)

    def test_below_tau_is_not_established_even_when_argmax_says_established(self) -> None:
        # p = sigmoid(0.5) ~ 0.62: the model's own argmax is " established", tau is not met.
        fake = _FakeComplete(_completion(" established F1:5:22", {" established": -0.5, " not": -1.0}))
        j = HouseJudge(complete=fake, tau=0.74, statute_chars=600).judge(REQ)
        assert j.status == "not_established"
        assert 0.5 < j.p_established < 0.74
        assert j.fact_id is None and j.quote is None

    def test_overshooting_model_offsets_no_longer_matter(self) -> None:
        """Seen live: `F1:0:103` for an 87-character fact. The offsets are ignored; the fact id carries the claim."""
        fake = _FakeComplete(_completion(" established F1:0:63", {" established": -0.05, " not": -3.0}))
        j = HouseJudge(complete=fake, tau=0.74, statute_chars=600).judge(REQ)
        assert (j.status, j.fact_id, j.quote) == ("established", "F1", "TOY: Arun married Bela in 2019.")

    def test_established_without_a_fact_keeps_status_and_no_quote(self) -> None:
        fake = _FakeComplete(_completion(" established", {" established": -0.01, " not": -5.0}))
        j = HouseJudge(complete=fake, tau=0.74, statute_chars=600).judge(REQ)
        assert j.status == "established"
        assert j.fact_id is None and j.quote is None

    def test_f_narrative_cannot_be_returned_as_evidence_even_if_present_in_facts(self) -> None:
        """Defense in depth: even if `request.facts` carries an `F_narrative` entry (a caller built off the
        jsonl training/eval schema, or a hallucination the model produced despite never seeing it in
        `Available facts:`), the judge must not accept it as the evidence fact -- `fact_id`/`quote` collapse
        to None exactly as for any other name absent from `Available facts:`, so `nyaya_quote.locate_quote`
        sees `fact_id=None` (`reason="no_quote"`) rather than a lookup that happens to succeed against a
        fact_map that (for other reasons -- e.g. the quote check) still carries the narrative."""
        req_with_narrative = JudgeRequest(
            contract_id=REQ.contract_id,
            element=REQ.element,
            is_denial=REQ.is_denial,
            statute=REQ.statute,
            narrative=REQ.narrative,
            facts=(("F_narrative", REQ.narrative), *REQ.facts),
        )
        fake = _FakeComplete(_completion(" established F_narrative:0:80", {" established": -0.05, " not": -3.0}))
        j = HouseJudge(complete=fake, tau=0.74, statute_chars=600).judge(req_with_narrative)
        assert j.status == "established"
        assert (j.fact_id, j.quote, j.quote_source) == (None, None, None)

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

    def test_complete_passes_stop_through_when_given(self) -> None:
        captured: dict[str, Any] = {}
        body = {"model": "judge", "choices": [{"text": " B", "finish_reason": "stop"}]}

        def fake_open(req: Any, timeout: float | None = None) -> _Resp:
            captured["body"] = json.loads(req.data)
            return _Resp(body)

        with mock.patch("urllib.request.urlopen", fake_open):
            ChatClient("http://h/v1", model="judge").complete("P", max_tokens=30, stop=["\n\n", "\nQuestion:"])
        assert captured["body"]["stop"] == ["\n\n", "\nQuestion:"]

    def test_complete_omits_stop_entirely_when_not_given(self) -> None:
        # P1's real finding, 2026-09-24: a missing `stop` must be an ABSENT field, not a null/empty
        # one silently sent -- some OpenAI-compatible servers treat an empty list differently from a
        # missing key, so "no stop requested" must not be indistinguishable from "stop on nothing".
        captured: dict[str, Any] = {}
        body = {"model": "judge", "choices": [{"text": " B", "finish_reason": "stop"}]}

        def fake_open(req: Any, timeout: float | None = None) -> _Resp:
            captured["body"] = json.loads(req.data)
            return _Resp(body)

        with mock.patch("urllib.request.urlopen", fake_open):
            ChatClient("http://h/v1", model="judge").complete("P", max_tokens=30)
        assert "stop" not in captured["body"]

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

    def test_parses_status_fact_and_quote_and_reports_a_hard_probability(self) -> None:
        reply = json.dumps({"status": "established", "fact_id": "F1", "quote": "Arun married Bela"})
        prompts: list[str] = []

        def ask(v: panel.Vendor, p: str) -> panel.Answer:
            prompts.append(p)
            return _answer(f"Here you go:\n```json\n{reply}\n```")

        j = FrontierJudge(self.VENDOR, ask_fn=ask).judge(REQ)
        assert (j.status, j.p_established, j.fact_id, j.quote, j.quote_source) == (
            "established",
            1.0,
            "F1",
            "Arun married Bela",
            "model",
        )
        assert REQ.element in prompts[0] and "[F2]" in prompts[0] and STATUTE in prompts[0]

    def test_prompt_never_asks_for_offsets(self) -> None:
        prompts: list[str] = []

        def ask(v: panel.Vendor, p: str) -> panel.Answer:
            prompts.append(p)
            return _answer('{"status": "not_established"}')

        FrontierJudge(self.VENDOR, ask_fn=ask).judge(REQ)
        assert "offset" not in prompts[0] and '"start"' not in prompts[0]

    def test_offsets_a_model_volunteers_are_ignored(self) -> None:
        reply = json.dumps({"status": "established", "fact_id": "F1", "quote": "Arun married Bela", "start": "x", "end": 999})
        j = FrontierJudge(self.VENDOR, ask_fn=lambda v, p: _answer(reply)).judge(REQ)
        assert (j.fact_id, j.quote) == ("F1", "Arun married Bela")

    def test_not_established_has_zero_probability_and_no_quote(self) -> None:
        j = FrontierJudge(self.VENDOR, ask_fn=lambda v, p: _answer('{"status": "not_established"}')).judge(REQ)
        assert (j.status, j.p_established, j.fact_id, j.quote) == ("not_established", 0.0, None, None)

    def test_quote_is_passed_through_verbatim_never_recomputed(self) -> None:
        reply = json.dumps({"status": "established", "fact_id": "F1", "quote": "Arun wed Bela"})
        j = FrontierJudge(self.VENDOR, ask_fn=lambda v, p: _answer(reply)).judge(REQ)
        assert j.quote == "Arun wed Bela"

    @pytest.mark.parametrize("text", ["no json here", '{"status": "maybe"}', "[1, 2]", '{"status": "established", "quote": 5}'])
    def test_unusable_reply_is_a_judge_output_error(self, text: str) -> None:
        with pytest.raises(JudgeOutputError):
            FrontierJudge(self.VENDOR, ask_fn=lambda v, p: _answer(text)).judge(REQ)
