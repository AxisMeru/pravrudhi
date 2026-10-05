"""#154: the typed decoder honours `enforce_served_model` like HouseJudge. Constructed inputs only; the fake
ChatClient exists only in this file. No network, no real material."""

from __future__ import annotations

import pytest

import pravrudhi.application.nyaya_judges as nj
from pravrudhi.application import nyaya_agent
from pravrudhi.application.nyaya_judges import JudgeRequest, ServedModelMismatch
from pravrudhi.application.typed import decoder as decoder_mod
from pravrudhi.models.openai_compat import CompletionResult

REQ = JudgeRequest(
    contract_id="constructed_toy",
    element="an element",
    is_denial=False,
    statute="CONSTRUCTED statute",
    narrative="CONSTRUCTED narrative",
    facts=(("F1", "CONSTRUCTED fact one."),),
)
GOOD_TOP = {" established": -0.05, " not": -4.0}


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
    "base_url": "http://127.0.0.1:1/v1",
    "model": "pinned-model",
    "statute_chars": 600,
    "max_tokens": 30,
    "top_logprobs": 20,
    "timeout_s": 5,
    "label_mass_floor": 0.5,
    "enforce_served_model": True,
}


def test_typed_slot_refuses_a_backend_serving_an_unpinned_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(nj, "ChatClient", _FakeClient)
    monkeypatch.setattr(decoder_mod, "ChatClient", _FakeClient)
    with pytest.raises(RuntimeError, match="pins"):
        nyaya_agent._build_house_judge(_CFG, tau=0.74, typed=False, api_key_env="X_UNSET").judge(REQ)
    with pytest.raises(RuntimeError, match="pins"):
        nyaya_agent._build_house_judge(_CFG, tau=0.74, typed=True, api_key_env="X_UNSET").judge(REQ)


def test_enforcement_is_off_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(decoder_mod, "ChatClient", _FakeClient)
    cfg = {k: v for k, v in _CFG.items() if k != "enforce_served_model"}
    judge = nyaya_agent._build_house_judge(cfg, tau=0.74, typed=True, api_key_env="X_UNSET")
    assert judge.judge(REQ).status == "established"


def test_an_unpinned_model_is_never_refused_even_when_enforced(monkeypatch: pytest.MonkeyPatch) -> None:
    """No `model` named -> nothing to pin; the id is read from /models, so a match is by construction."""
    monkeypatch.setattr(decoder_mod, "ChatClient", _FakeClient)
    cfg = {k: v for k, v in _CFG.items() if k != "model"}
    judge = nyaya_agent._build_house_judge(cfg, tau=0.74, typed=True, api_key_env="X_UNSET")
    assert judge.judge(REQ).status == "established"


def test_served_model_mismatch_is_the_nyaya_judges_class() -> None:
    assert issubclass(ServedModelMismatch, RuntimeError)
