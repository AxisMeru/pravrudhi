"""Typed-layer defect reproductions, night of 2026-09-30 (Tag's claims, pending verification).

Every test here is a runnable reproduction of one numbered finding in TYPED-LAYER-FINDINGS-2026-09-30.md.
All inputs are CONSTRUCTED (hand-written logprob dictionaries, toy strings, fake transports / a fake
ChatClient that exist only inside this file). No network, no real material.

Each test passes on this branch (the proposed fix is applied); `git stash`-free check that it FAILS on the
default branch: run it against commit b9f0435's src (see the findings file, "how verified").
"""

from __future__ import annotations

import math
from typing import Any

import pytest

from pravrudhi.application import nyaya_agent
from pravrudhi.application.nyaya_judges import HouseJudge, JudgeOutputError, JudgeRequest, ServedModelMismatch
from pravrudhi.application.typed import decoder as decoder_mod
from pravrudhi.application.typed.decoder import VLLMDecoder, score_decision
from pravrudhi.application.typed.house_judge import TypedHouseJudge
from pravrudhi.application.typed.schema import Field, FieldKind, bool_field
from pravrudhi.models.openai_compat import CompletionResult

REQ = JudgeRequest(
    contract_id="constructed_toy", element="an element", is_denial=False, statute="CONSTRUCTED statute",
    narrative="CONSTRUCTED narrative", facts=(("F1", "CONSTRUCTED fact one."),),
)
GOOD_TOP = {" established": -0.05, " not": -4.0}
NAN = float("nan")
NAN_TOPS = [{" established": NAN, " not": -1.0}, {" established": -0.05, " not": NAN}]


def _typed(top: dict[str, float], text: str = "established F1") -> TypedHouseJudge:
    def complete(prompt: str, *, max_tokens: int, temperature: float, logprobs: int | None) -> CompletionResult:
        return CompletionResult(text=text, model="m", top_logprobs=[top], wall_s=0.0)

    return TypedHouseJudge(tau=0.74, statute_chars=600, decoder=VLLMDecoder(model="m", complete=complete))


# -- F2: enforce_served_model is dropped on the typed path -----------------------------------------------

class _FakeClient:
    """Test double for `ChatClient`: answers every completion as the model `SERVED`."""

    SERVED = "some-other-model"

    def __init__(self, *, base_url: str, model: str, api_key: str | None = None, timeout_s: int = 60) -> None:
        self.base_url, self.model = base_url, model

    def list_models(self) -> list[str]:
        return [self.SERVED]

    def complete(self, prompt: str, *, max_tokens: int, temperature: float, logprobs: int | None) -> CompletionResult:
        return CompletionResult(text="established F1", model=self.SERVED, top_logprobs=[GOOD_TOP], wall_s=0.0)


_CFG = {
    "base_url": "http://127.0.0.1:1/v1", "model": "pinned-model", "statute_chars": 600, "max_tokens": 30,
    "top_logprobs": 20, "timeout_s": 5, "label_mass_floor": 0.5, "enforce_served_model": True,
}


def test_f2_typed_slot_refuses_a_backend_serving_an_unpinned_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """HouseJudge (enforce_served_model: true) refuses a backend answering under a model other than the pinned
    one (ServedModelMismatch, wrapped in RuntimeError by the transport). The typed slot, built from the SAME
    config by `_build_house_judge`, must refuse identically -- on the default branch it scores the answer."""
    import pravrudhi.application.nyaya_judges as nj

    monkeypatch.setattr(nj, "ChatClient", _FakeClient)
    monkeypatch.setattr(decoder_mod, "ChatClient", _FakeClient)
    with pytest.raises(RuntimeError, match="pins"):
        nyaya_agent._build_house_judge(_CFG, tau=0.74, typed=False, api_key_env="X_UNSET").judge(REQ)
    with pytest.raises(RuntimeError, match="pins"):
        nyaya_agent._build_house_judge(_CFG, tau=0.74, typed=True, api_key_env="X_UNSET").judge(REQ)


def test_f2_enforcement_off_by_default_keeps_todays_behaviour(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(decoder_mod, "ChatClient", _FakeClient)
    cfg = {k: v for k, v in _CFG.items() if k != "enforce_served_model"}
    assert nyaya_agent._build_house_judge(cfg, tau=0.74, typed=True, api_key_env="X_UNSET").judge(REQ).status == "established"


def test_served_model_mismatch_is_the_nyaya_judges_class() -> None:
    assert issubclass(ServedModelMismatch, RuntimeError)


# -- F3: typed builder silently defaults keys HouseJudge.from_config requires -----------------------------

@pytest.mark.parametrize("missing", ["timeout_s", "max_tokens", "top_logprobs"])
def test_f3_typed_builder_requires_the_same_keys_as_house_judge_from_config(missing: str) -> None:
    """Repo rule: a missing input raises, it never defaults. HouseJudge.from_config uses bare subscripts for
    these keys; the typed branch of `_build_house_judge` used `.get(key, default)` (60 / 30 / 20)."""
    cfg = {k: v for k, v in _CFG.items() if k != missing}
    with pytest.raises(KeyError, match=missing):
        HouseJudge.from_config(cfg, tau=0.74, api_key_env="X_UNSET")
    with pytest.raises(KeyError, match=missing):
        nyaya_agent._build_house_judge(cfg, tau=0.74, typed=True, api_key_env="X_UNSET")


# -- F4: Field accepts option variants that make score_decision meaningless -----------------------------------

def test_f4_empty_variant_tuple_is_refused() -> None:
    """An option with no token variants can never be observed: it is always 'missing', so `score_decision`
    returns a bound for it (0.5/0.5 against one present option) instead of a score. Field must refuse it."""
    with pytest.raises(ValueError, match="non-empty token variants"):
        bool_field("s", true_tokens=(), false_tokens=(" not",))


def test_f4_a_token_listed_under_two_options_is_refused() -> None:
    """A shared token is counted for BOTH options, so the softmax splits mass the model never split."""
    with pytest.raises(ValueError, match="both"):
        Field(name="s", kind=FieldKind.ENUM, options={"a": (" x", " y"), "b": (" y", " z")})


def test_f4_blank_token_string_is_refused() -> None:
    with pytest.raises(ValueError, match="non-empty token variants"):
        Field(name="s", kind=FieldKind.ENUM, options={"a": ("",), "b": (" z",)})


def test_f4_the_shared_established_not_fields_still_construct() -> None:
    assert bool_field("s", true_tokens=(" established", "established"), false_tokens=(" not", "not")).options


# -- F5: a NaN label logprob yields `established` with p = NaN ----------------------------------------------

@pytest.mark.parametrize("top", NAN_TOPS, ids=["nan-est", "nan-not"])
def test_f5_typed_judge_refuses_a_nan_label_logprob(top: dict[str, float]) -> None:
    """NaN compares False against everything, so `p < tau` and the label-mass floor both 'pass' a NaN: the
    decision falls through to `established` with p=NaN (fail-open). `CompletionResult` accepts NaN (pydantic
    float), and `json.loads` accepts the literal NaN. The fixed typed judge raises JudgeOutputError."""
    with pytest.raises(JudgeOutputError):
        _typed(top).judge(REQ)


@pytest.mark.xfail(strict=True, reason=(
    "F5b, NOT fixed on this branch (nyaya_judges.py is outside tag/night-typed scope): HouseJudge itself "
    "returns established with p=NaN for a NaN label logprob. Proposed one-line fix in the findings file. "
    "strict=True: the moment someone fixes it, this xfail turns into a failure and should be deleted."))
@pytest.mark.parametrize("top", NAN_TOPS, ids=["nan-est", "nan-not"])
def test_f5b_house_judge_refuses_a_nan_label_logprob(top: dict[str, float]) -> None:
    def complete(prompt: str) -> CompletionResult:
        return CompletionResult(text="established F1", model="m", top_logprobs=[top], wall_s=0.0)

    with pytest.raises(JudgeOutputError):
        HouseJudge(tau=0.74, statute_chars=600, model="m", complete=complete).judge(REQ)


# -- F6: a NaN / out-of-range label_mass_floor silently disables the guard -------------------------------

@pytest.mark.parametrize("bad", [NAN, -0.1, 1.5, math.inf])
def test_f6_typed_judge_refuses_a_nonsensical_label_mass_floor(bad: float) -> None:
    """`mass < NaN` is always False, so a NaN floor (e.g. `NYAYA_SECOND_JUDGE_LABEL_MASS_FLOOR=nan`, which
    `float()` accepts) would disable the guard silently. Refused at construction."""
    with pytest.raises(ValueError, match="label_mass_floor"):
        decoder = VLLMDecoder(model="m", complete=lambda *a, **k: None)
        TypedHouseJudge(tau=0.74, statute_chars=600, decoder=decoder, label_mass_floor=bad)


def test_score_decision_is_unchanged_by_the_guard_work() -> None:
    """The generic primitive keeps its signature (scripts/t2_*.py call it directly)."""
    scores, missing = score_decision(
        CompletionResult(text="", model="m", top_logprobs=[GOOD_TOP], wall_s=0.0),
        bool_field("s", true_tokens=(" established",), false_tokens=(" not",)),
    )
    assert missing == frozenset() and abs(sum(scores.values()) - 1.0) < 1e-12
    _: Any = None
