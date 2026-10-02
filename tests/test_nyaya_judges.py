"""Element judges for the Nyaya agentic loop: the house judge (vLLM first-token logprob) and a frontier
judge (any `panel` vendor). Both return the same per-element shape and both go through the same quote check
downstream. The HTTP transport is replaced by a test double here; no model is called.

Every fact and statute string is hand-written toy text (the statute strings are public statute text).
"""

from __future__ import annotations

import json
import math
import urllib.error
from collections.abc import Callable
from typing import Any
from unittest import mock

import pytest

from pravrudhi.application import panel
from pravrudhi.application.nyaya_judges import (
    LABEL_MASS_FLOOR,
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


class TestLabelMassGuard:
    """Per Tag/Lead-2 (2026-09-28), production-safety: a label token appearing anywhere in the top-k
    is not itself evidence of a real decision -- a prose completion can still have it by coincidence,
    at negligible probability."""

    def test_top1_is_prose_raises_even_though_a_label_token_is_present(self) -> None:
        top = {"Based": -0.1, " established": -8.0, " on": -1.0, " the": -2.0}
        with pytest.raises(JudgeOutputError, match="not a label token"):
            p_established_from_top_logprobs(top)

    def test_label_mass_below_floor_raises_even_when_top1_is_a_label_token(self) -> None:
        # ' established' IS top-1 here, but its own probability (exp(-2.0) ~= 0.135) plus ' not'
        # (exp(-8.0) ~= 0.0003) is far below the floor -- most of the real distribution went elsewhere.
        top = {" established": -2.0, " not": -8.0, "prose_a": -2.1, "prose_b": -2.2, "prose_c": -2.3}
        with pytest.raises(JudgeOutputError, match="label mass"):
            p_established_from_top_logprobs(top)

    def test_label_mass_clearing_the_floor_passes(self) -> None:
        # ' not' missing (lower_bound case); exp(est) alone already clears the floor comfortably --
        # confirms the guard doesn't fire on a genuinely confident, mostly-label completion.
        top = {" established": -0.05, " F": -6.0}
        p, clamp = p_established_from_top_logprobs(top)
        mass = math.exp(-0.05)
        assert mass >= LABEL_MASS_FLOOR
        assert p == pytest.approx(1 / (1 + math.exp(-6.0 - (-0.05))))
        assert clamp == "lower_bound"


class TestFirstTokenScore:
    def test_softmax_of_established_vs_not(self) -> None:
        p, clamp = p_established_from_top_logprobs({" established": -0.1, " not": -2.5, " F": -6.0})
        assert p == pytest.approx(1 / (1 + math.exp(-2.4)))
        assert clamp == "none"

    def test_takes_the_max_over_spaced_and_bare_variants(self) -> None:
        p, clamp = p_established_from_top_logprobs(
            {" established": -3.0, "established": -1.0, " not": -1.0, "not": -4.0}
        )
        assert p == pytest.approx(0.5)
        assert clamp == "none"

    def test_neither_token_is_an_error_not_a_guess(self) -> None:
        with pytest.raises(JudgeOutputError):
            p_established_from_top_logprobs({" F": -0.1, " R": -2.0})

    def test_absent_not_token_returns_a_lower_bound_not_a_clamp_to_one(self) -> None:
        # G-28 (2026-09-28), Tag's finding: the OLD behaviour clamped this to exactly 1.0. The correct
        # value is a LOWER bound: neg's true logprob is <= min(top) (-6.0 here, since it's not itself in
        # top-k), so the bound uses neg=min(top)=-6.0 (its least-negative-possible value, i.e. the
        # worst case for the bound): p = 1/(1+exp(-6.0 - (-0.1))).
        top = {" established": -0.1, " F": -6.0}
        p, clamp = p_established_from_top_logprobs(top)
        assert clamp == "lower_bound"
        assert p == pytest.approx(1 / (1 + math.exp(-6.0 - (-0.1))))
        assert p < 1.0  # never the old hard clamp

    def test_absent_established_token_returns_an_upper_bound_not_a_clamp_to_zero(self) -> None:
        # Mirror case: est's true logprob is <= min(top) (-6.0), so the bound uses est=-6.0 (its
        # least-negative-possible value, the worst case for THIS bound, which is an upper bound):
        # p = 1/(1+exp(-0.1 - (-6.0))).
        top = {" not": -0.1, " F": -6.0}
        p, clamp = p_established_from_top_logprobs(top)
        assert clamp == "upper_bound"
        assert p == pytest.approx(1 / (1 + math.exp(-0.1 - (-6.0))))
        assert p > 0.0  # never the old hard clamp
        assert p <= 0.5  # provable: min(top) <= neg always in this branch (module docstring)

    def test_lower_bound_pinned_numeric_case(self) -> None:
        # A pinned, hand-checkable case: est=-0.5, min(top)=-8.0 (a token far below either label, e.g.
        # a stray punctuation token) -> p = 1/(1+exp(-8.0-(-0.5))) = 1/(1+exp(-7.5)) ~= 0.9994472...
        top = {" established": -0.5, " ,": -8.0}
        p, clamp = p_established_from_top_logprobs(top)
        assert clamp == "lower_bound"
        assert p == pytest.approx(0.9994472213, abs=1e-9)


class TestHouseJudgeConservativeDecisionRule:
    """Per R1's finding on #120 (G-28): hard-coding bound_undetermined=False in HouseJudge.judge
    failed 0 tests -- these three go through judge() itself (not the bare p_established_from_top_logprobs
    function), covering all three real decision branches."""

    def test_lower_bound_clearing_tau_is_established_not_undetermined(self) -> None:
        # ' not' missing; est=-0.05, min(top)=-3.0 -> bound = 1/(1+exp(-3.0-(-0.05))) ~= 0.9503, well
        # clear of tau=0.74. A valid lower bound >= tau proves the TRUE p is also >= tau (it can only
        # be higher) -- established, no ambiguity.
        fake = _FakeComplete(_completion(" established F1:5:22", {" established": -0.05, " F": -3.0}))
        j = HouseJudge(complete=fake, tau=0.74, statute_chars=600).judge(REQ)
        assert j.status == "established"
        assert j.clamp == "lower_bound"
        assert j.bound_undetermined is False
        assert j.p_established == pytest.approx(1 / (1 + math.exp(-3.0 - (-0.05))))

    def test_lower_bound_below_tau_is_not_established_and_flagged_bound_undetermined(self) -> None:
        # ' not' missing; est=-0.05, min(top)=-0.5 -> bound = 1/(1+exp(-0.5-(-0.05))) ~= 0.6108, BELOW
        # tau=0.74. The bound alone cannot rule establishment in OR out (the true p could be anywhere
        # from this bound up to 1.0) -- conservative default is not_established, but flagged distinctly
        # from an ordinary tau-miss.
        fake = _FakeComplete(_completion(" established F1", {" established": -0.05, " F": -0.5}))
        j = HouseJudge(complete=fake, tau=0.74, statute_chars=600).judge(REQ)
        assert j.status == "not_established"
        assert j.clamp == "lower_bound"
        assert j.bound_undetermined is True
        assert 0.5 < j.p_established < 0.74  # the interesting "lower bound < tau < 1.0" zone

    def test_upper_bound_is_a_genuine_not_established_never_undetermined(self) -> None:
        # ' established' missing; neg=-0.1, min(top)=-6.0 -> bound = 1/(1+exp(-0.1-(-6.0))) ~= 0.0027,
        # far below any tau >= 0.5. This branch can NEVER manufacture an unresolved case against a
        # realistic tau (module docstring: min(top) <= neg always here, so the bound itself is <= 0.5)
        # -- bound_undetermined must be False, not just "happens to be" in this fixture.
        fake = _FakeComplete(_completion(" not", {" not": -0.1, " F": -6.0}))
        j = HouseJudge(complete=fake, tau=0.74, statute_chars=600).judge(REQ)
        assert j.status == "not_established"
        assert j.clamp == "upper_bound"
        assert j.bound_undetermined is False
        assert j.p_established == pytest.approx(1 / (1 + math.exp(-0.1 - (-6.0))))


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

    def test_from_config_requires_label_mass_floor(self) -> None:
        # R1's review of #124: "refuse if it's missing", not a silent module-constant fallback.
        config = {
            "statute_chars": 600, "base_url": "http://h/v1", "model": "m",
            "max_tokens": 30, "top_logprobs": 20, "timeout_s": 60,
        }
        with pytest.raises(KeyError, match="label_mass_floor"):
            HouseJudge.from_config(config, tau=0.74)

    def test_from_config_threads_a_custom_label_mass_floor_into_the_actual_decision(self) -> None:
        # Not just stored -- proves the CONFIG value is what judge() actually uses: a completion whose
        # label mass (~0.322) clears a low floor (0.2) but not the module default (0.5).
        config = {
            "statute_chars": 600, "base_url": "http://h/v1", "model": "m", "max_tokens": 30,
            "top_logprobs": 20, "timeout_s": 60, "label_mass_floor": 0.2,
        }
        j = HouseJudge.from_config(config, tau=0.74)
        assert j.label_mass_floor == 0.2
        fake = _FakeComplete(_completion(" established F1", {" established": -1.3, " not": -3.0}))
        j._complete = fake  # type: ignore[attr-defined]  # override the built transport, post-construction
        result = j.judge(REQ)  # would raise JudgeOutputError under the 0.5 default; must not here
        assert result.status in ("established", "not_established")

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
            "label_mass_floor": 0.5,
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
            "label_mass_floor": 0.5,
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

    def test_fallback_api_key_env_from_config(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """2026-09-27: `fallback_api_key_env` gives every fallback its own key, distinct from `api_key_env`'s
        -- opt-in per call (`from_config`'s default is `None`, unchanged for a caller that never asks)."""
        config = {
            "statute_chars": 600,
            "base_url": "https://primary.example/v1",
            "base_urls_fallback": ["https://fallback.example/v1"],
            "max_tokens": 30,
            "top_logprobs": 20,
            "timeout_s": 60,
            "api_key": "primary_from_yaml",
        }
        # Not asked for (fallback_api_key_env omitted): unchanged "every backend shares api_key" default.
        j = HouseJudge.from_config(config, tau=0.5)
        assert j.api_keys == ["primary_from_yaml", "primary_from_yaml"]

        # Asked for, via env:
        monkeypatch.setenv("NYAYA_SECOND_JUDGE_FALLBACK_API_KEY", "fallback_from_env")
        j = HouseJudge.from_config(
            config, tau=0.5, fallback_api_key_env="NYAYA_SECOND_JUDGE_FALLBACK_API_KEY"
        )
        assert j.api_keys == ["primary_from_yaml", "fallback_from_env"]

        # Asked for, but env unset -- falls back to the config's own `fallback_api_key` key.
        monkeypatch.delenv("NYAYA_SECOND_JUDGE_FALLBACK_API_KEY", raising=False)
        config["fallback_api_key"] = "fallback_from_yaml"
        j = HouseJudge.from_config(
            config, tau=0.5, fallback_api_key_env="NYAYA_SECOND_JUDGE_FALLBACK_API_KEY"
        )
        assert j.api_keys == ["primary_from_yaml", "fallback_from_yaml"]

    def test_the_fallback_backend_also_receives_the_bearer_key(self) -> None:
        """2026-09-27 (Lead-2, `docs/decisions/2026-09-27-5090-second-judge-exposure-design.md` sec 2.1,
        pravrudhi): a fallback backend used to get NO Authorization header at all -- fine for a fallback
        that never checks one (the class's original "operator's own local vLLM" fallback shape), silently
        broken for a fallback (a RunPod serverless endpoint) that requires the SAME bearer key the primary
        does. The primary here fails with a connection error (a real `urllib.error.URLError`, the same
        exception class a genuinely unreachable host raises) so `_complete_with_fallback` moves to the
        fallback backend, which is where the header actually gets checked."""
        captured: dict[str, Any] = {}
        result = _completion(" established F1:0:5", {" established": -0.1, " not": -2.0})

        def fake_open(req: Any, timeout: float | None = None) -> _Resp:
            if req.full_url == "http://primary/v1/models":
                return _Resp({"data": [{"id": "primary-model"}]})
            if req.full_url == "http://primary/v1/completions":
                raise urllib.error.URLError("connection refused")
            if req.full_url == "http://fallback/v1/models":
                return _Resp({"data": [{"id": "fallback-model"}]})
            if req.full_url == "http://fallback/v1/completions":
                captured["auth_header"] = req.get_header("Authorization")
                return _Resp({
                    "model": "fallback-model",
                    "choices": [{
                        "text": result.text, "finish_reason": "stop",
                        "logprobs": {"top_logprobs": result.top_logprobs},
                    }],
                })
            raise AssertionError(f"unexpected URL in test: {req.full_url}")

        with mock.patch("urllib.request.urlopen", fake_open):
            j = HouseJudge(
                base_url="http://primary/v1",
                fallback_urls=["http://fallback/v1"],
                api_key="shared_bearer_key",
                tau=0.74,
                statute_chars=600,
            )
            out = j.judge(REQ)

        assert captured["auth_header"] == "Bearer shared_bearer_key"
        assert out.backend_used == 1  # confirms the fallback, not the primary, actually answered

    def _fallback_net(self, captured: dict[str, Any]) -> Callable[[Any, float | None], _Resp]:
        """A primary that always fails (connection refused) and a fallback that records its own
        Authorization header -- shared by the per-backend-key tests below."""
        result = _completion(" established F1:0:5", {" established": -0.1, " not": -2.0})

        def fake_open(req: Any, timeout: float | None = None) -> _Resp:
            captured.setdefault("auth_by_url", {})
            if req.full_url == "http://primary/v1/models":
                return _Resp({"data": [{"id": "primary-model"}]})
            if req.full_url == "http://primary/v1/completions":
                captured["auth_by_url"]["primary"] = req.get_header("Authorization")
                raise urllib.error.URLError("connection refused")
            if req.full_url == "http://fallback/v1/models":
                return _Resp({"data": [{"id": "fallback-model"}]})
            if req.full_url == "http://fallback/v1/completions":
                captured["auth_by_url"]["fallback"] = req.get_header("Authorization")
                return _Resp({
                    "model": "fallback-model",
                    "choices": [{
                        "text": result.text, "finish_reason": "stop",
                        "logprobs": {"top_logprobs": result.top_logprobs},
                    }],
                })
            raise AssertionError(f"unexpected URL in test: {req.full_url}")

        return fake_open

    def test_a_distinct_fallback_api_key_reaches_only_the_fallback(self) -> None:
        """2026-09-27 (Lead-2, follow-up on the "every backend" fix above): a primary and a fallback in
        different trust domains (a 5090 tunnel vs a RunPod-serverless endpoint) must never share one
        secret. `fallback_api_key` gives the fallback its OWN key, distinct from the primary's `api_key`."""
        captured: dict[str, Any] = {}
        with mock.patch("urllib.request.urlopen", self._fallback_net(captured)):
            j = HouseJudge(
                base_url="http://primary/v1",
                fallback_urls=["http://fallback/v1"],
                api_key="primary_secret",
                fallback_api_key="fallback_secret",
                tau=0.74,
                statute_chars=600,
            )
            j.judge(REQ)

        assert captured["auth_by_url"]["primary"] == "Bearer primary_secret"
        assert captured["auth_by_url"]["fallback"] == "Bearer fallback_secret"

    def test_an_explicit_api_keys_list_is_used_verbatim_per_backend(self) -> None:
        captured: dict[str, Any] = {}
        with mock.patch("urllib.request.urlopen", self._fallback_net(captured)):
            j = HouseJudge(
                base_url="http://primary/v1",
                fallback_urls=["http://fallback/v1"],
                api_keys=["primary_secret", "fallback_secret"],
                tau=0.74,
                statute_chars=600,
            )
            assert j.api_keys == ["primary_secret", "fallback_secret"]
            j.judge(REQ)

        assert captured["auth_by_url"]["primary"] == "Bearer primary_secret"
        assert captured["auth_by_url"]["fallback"] == "Bearer fallback_secret"

    def test_an_api_keys_list_of_the_wrong_length_is_refused(self) -> None:
        """Fail closed on a mismatch: never zip a too-short/too-long list against the backend URLs and
        guess which key belongs to which one."""
        with pytest.raises(ValueError, match="2 api_keys for 1 backends"):
            HouseJudge(
                base_url="http://primary/v1",
                fallback_urls=[],
                api_keys=["primary_secret", "extra_secret"],
                tau=0.74,
                statute_chars=600,
            )

    def test_api_keys_and_fallback_api_key_together_is_refused(self) -> None:
        """Two different ways to say the same thing, given at once -- refuse rather than silently pick
        one and ignore the other."""
        with pytest.raises(ValueError, match="both api_keys and fallback_api_key"):
            HouseJudge(
                base_url="http://primary/v1",
                fallback_urls=["http://fallback/v1"],
                api_keys=["a", "b"],
                fallback_api_key="c",
                tau=0.74,
                statute_chars=600,
            )

    def test_neither_per_backend_option_keeps_the_shared_key_default(self) -> None:
        """No behaviour change for a caller that only ever knew about `api_key` (the 2026-09-27 "every
        backend" fix, unchanged when neither of the two newer options is used)."""
        captured: dict[str, Any] = {}
        with mock.patch("urllib.request.urlopen", self._fallback_net(captured)):
            j = HouseJudge(
                base_url="http://primary/v1",
                fallback_urls=["http://fallback/v1"],
                api_key="shared_secret",
                tau=0.74,
                statute_chars=600,
            )
            assert j.api_keys == ["shared_secret", "shared_secret"]
            j.judge(REQ)

        assert captured["auth_by_url"]["primary"] == "Bearer shared_secret"
        assert captured["auth_by_url"]["fallback"] == "Bearer shared_secret"

    def test_isolated_fallback_with_no_key_configured_sends_no_authorization_header(self) -> None:
        """2026-09-27, Lead-2's follow-up decision on #115: an isolated fallback (a different trust domain
        from the primary) with NO fallback key configured gets NO `Authorization` header at all -- it must
        NEVER silently fall back to the primary's own key. (Correction, R1's review: a real deployment
        where that fallback itself checks auth would then see a bare 401, which `_config_fault_status`
        classifies as a CONFIGURATION fault, not a transient one -- `AndGateJudge`/`nyaya_agent` turn that
        into `JudgeMisconfigured`, a loud 503, NOT a fail-closed REFER. That is correct for a genuinely
        broken/missing key, but it means an unauthenticated fallback must never be reached by accident --
        `nyaya_agent.load_agent_config` now refuses to start with fallback URLs configured and no key
        anywhere, unless the deployment explicitly opts in (`NYAYA_SECOND_JUDGE_FALLBACK_NO_AUTH=1`); see
        `tests/test_nyaya_agent.py`'s `TestSecondJudgeFallbackAuthLoadTimeGuard` and
        `TestJudgeConfigurationFault.test_an_isolated_second_judge_fallback_with_no_key_is_a_config_fault_
        not_a_refer`. This test itself is unaffected -- it stays at the HTTP-header level, checking only
        that the key isolation itself works, not what happens to the request afterward.)"""
        captured: dict[str, Any] = {}
        with mock.patch("urllib.request.urlopen", self._fallback_net(captured)):
            j = HouseJudge(
                base_url="http://primary/v1",
                fallback_urls=["http://fallback/v1"],
                api_key="primary_secret",
                isolate_fallback_key=True,  # fallback_api_key omitted: no key configured for it
                tau=0.74,
                statute_chars=600,
            )
            assert j.api_keys == ["primary_secret", None]
            j.judge(REQ)

        assert captured["auth_by_url"]["primary"] == "Bearer primary_secret"
        assert captured["auth_by_url"]["fallback"] is None

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


class TestLazyModelResolution:
    """Lead-2, 2026-09-25: found validating the config-C switch-on smoke test against a real, briefly-
    unreachable second judge -- `HouseJudge(base_url=..., model=None)` used to call `/v1/models` EAGERLY at
    construction, so an unreachable/cold judge crashed agent construction itself with an unhandled error,
    before any request was attempted (and, for the second judge, before AndGateJudge's own try/except around
    the per-request call ever ran). Resolution must be lazy: on first actual use, not at construction.
    Port 1 (never bound, always refused) stands in for "unreachable" -- no mock needed, this is a real,
    fast, local connection refusal."""

    UNREACHABLE = "http://127.0.0.1:1/v1"

    def test_construction_with_an_unreachable_server_and_no_model_does_not_raise(self) -> None:
        HouseJudge(base_url=self.UNREACHABLE, model=None, tau=0.74, statute_chars=600)  # must not raise

    def test_the_model_property_is_what_actually_resolves_lazily(self) -> None:
        j = HouseJudge(base_url=self.UNREACHABLE, model=None, tau=0.74, statute_chars=600)
        with pytest.raises((ConnectionError, OSError)):
            _ = j.model  # first access -- this is where the /v1/models call now happens

    def test_judge_call_against_an_unreachable_server_and_no_model_raises_at_call_time_not_earlier(self) -> None:
        j = HouseJudge(base_url=self.UNREACHABLE, model=None, tau=0.74, statute_chars=600)
        with pytest.raises(RuntimeError):
            j.judge(REQ)  # the error surfaces here, not at construction (already proven not to raise, above)

    def test_a_reachable_server_with_no_model_still_resolves_and_caches(self) -> None:
        """The primary's own behaviour for a REACHABLE judge is unchanged: `.model` resolves correctly (still
        lazily, on first access) and is cached, matching the pre-existing (eager) resolution's cached value
        for every subsequent access -- this only moves WHEN resolution happens, never what it resolves to."""

        class _FakeListModelsClient:
            def __init__(self, models: list[str]) -> None:
                self._models = models
                self.list_models_calls = 0

            def list_models(self) -> list[str]:
                self.list_models_calls += 1
                return self._models

        j = HouseJudge(base_url="http://fake/v1", model=None, tau=0.74, statute_chars=600)
        fake_client = _FakeListModelsClient(["resolved-model-id"])
        j.clients[0] = fake_client  # type: ignore[assignment]  -- a test double, real ChatClient never used
        assert j.model == "resolved-model-id"
        assert j.model == "resolved-model-id"  # second access: cached, not a second list_models() call
        assert fake_client.list_models_calls == 1

    def test_a_given_model_id_skips_list_models_entirely_construction_and_access(self) -> None:
        """`model="some-id"` (never None): resolution is a no-op either way -- unchanged from before this
        fix, and the point of pinning a fixed model id in the runbook for the second judge specifically."""

        class _RaisingClient:
            def list_models(self) -> list[str]:
                raise AssertionError("list_models() must never be called when a model id was given")

        j = HouseJudge(base_url="http://fake/v1", model="pinned-model", tau=0.74, statute_chars=600)
        j.clients[0] = _RaisingClient()  # type: ignore[assignment]
        assert j.model == "pinned-model"  # does not raise -- list_models() is never reached


class TestSecondJudgeAgainstAnUnreachableRealServer:
    """End-to-end, through the real AndGateJudge + NyayaAgent path, not a ScriptedJudge double: the second
    judge is a REAL HouseJudge pointed at an unreachable server. Confirms the whole chain -- lazy resolution
    (this file) plus the second_judge_unavailable -> REFER_TO_LAWYER fix (nyaya_agent.py, #10) -- composes
    correctly, not just each half in isolation."""

    def test_and_gate_construction_does_not_raise_even_though_second_is_unreachable(self) -> None:
        from pravrudhi.application.nyaya_judges import AndGateJudge

        primary = HouseJudge(
            complete=_FakeComplete(_completion(" established F1:5:22", {" established": -0.05, " not": -3.0})),
            tau=0.74, statute_chars=600,
        )
        second = HouseJudge(base_url="http://127.0.0.1:1/v1", model=None, tau=0.97, statute_chars=600)
        AndGateJudge(primary, second, tau_primary=0.74, tau_second=0.97)  # must not raise

    def test_and_gate_judge_call_fails_closed_not_established_when_second_is_unreachable(self) -> None:
        from pravrudhi.application.nyaya_judges import AndGateJudge

        primary = HouseJudge(
            complete=_FakeComplete(_completion(" established F1:5:22", {" established": -0.05, " not": -3.0})),
            tau=0.74, statute_chars=600,
        )
        second = HouseJudge(base_url="http://127.0.0.1:1/v1", model=None, tau=0.97, statute_chars=600)
        out = AndGateJudge(primary, second, tau_primary=0.74, tau_second=0.97).judge(REQ)
        assert out.status == "not_established"
        assert out.vetoed_by == "second"
        assert out.second_skip_reason is not None and "second_unavailable" in out.second_skip_reason


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


@pytest.mark.parametrize(
    "top",
    [
        {" established": math.nan, " not": -1.0},
        {" established": -0.1, " not": math.nan},
        {" established": math.nan},
        {" not": math.nan},
        {" established": math.inf, " not": -1.0},
        {" established": -0.1, " not": math.inf},
        {"established": math.nan, " established": -0.1},
        {" established": -0.1, "established": math.nan},
        {" not": -0.1, "not": math.nan},
        {" established": -0.1, " not": -3.0, "Based": math.nan},
        {" established": -0.1, " not": -3.0, "Based": math.inf},
    ],
)
def test_non_finite_label_logprob_is_a_judge_output_error_not_a_probability(top: dict[str, float]) -> None:
    """#156: NaN made `label_mass < floor` False and returned p=nan; +inf returned established p=1.0."""
    with pytest.raises(JudgeOutputError):
        p_established_from_top_logprobs(top)


@pytest.mark.parametrize(
    "top",
    [
        {"established": math.nan, " established": -0.1},
        {" established": -0.1, " not": -3.0, "Based": math.nan},
        {" established": math.nan, " not": -1.0},
    ],
)
def test_house_judge_never_establishes_on_a_nan_logprob(top: dict[str, float]) -> None:
    """R2 on #166: a NaN on a duplicate label variant gave established with p=nan through judge()."""
    fake = _FakeComplete(_completion(" established F1:5:22", top))
    with pytest.raises(JudgeOutputError):
        HouseJudge(complete=fake, tau=0.74, statute_chars=600).judge(REQ)
