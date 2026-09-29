"""HouseJudge.enforce_served_model: when a deployment names the judge's model id AND opts in, every answer's `model` field
must equal it, or the call fails closed.

A deployment can keep a stable served name while the weights behind it change, so the pinned id alone cannot show which model
answered; this check refuses an answer that reports a different id. It is opt-in (`house_judge.enforce_served_model` /
NYAYA_HOUSE_JUDGE_ENFORCE_SERVED_MODEL) so an existing deployment does not change until it names the switch, and it never applies
to a model resolved from /models, to a fallback, or to the second judge."""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import pytest

from pravrudhi.application import nyaya_agent, nyaya_judges
from pravrudhi.application.nyaya_judges import HouseJudge

PRIMARY = "http://primary/v1"
FALLBACK = "http://fallback/v1"
_TOP = {" established": -0.1, " not": -3.0}
REPO = Path(__file__).resolve().parents[1]


class _Resp(io.BytesIO):
    def __enter__(self) -> _Resp:
        return self

    def __exit__(self, *a: object) -> None:
        self.close()


class _Net:
    def __init__(self, answered_as: str, *, primary_listed: str = "nyaya-judge-4b") -> None:
        self.answered_as = answered_as
        self.primary_listed = primary_listed
        self.completions: list[str] = []

    def __call__(self, req: Any, timeout: float = 0) -> _Resp:
        url = req.full_url
        base = PRIMARY if url.startswith(PRIMARY) else FALLBACK
        if url.endswith("/models"):
            listed = self.primary_listed if base == PRIMARY else "fallback-model"
            return _Resp(json.dumps({"data": [{"id": listed}]}).encode())
        self.completions.append(base)
        body = {"model": self.answered_as, "choices": [{"text": " established F1", "finish_reason": "length",
                                                        "logprobs": {"top_logprobs": [_TOP]}}]}
        return _Resp(json.dumps(body).encode())


def _judge(monkeypatch: pytest.MonkeyPatch, net: _Net, *, model: str | None = "nyaya-judge-4b",
           enforce: bool = True, fallbacks: list[str] | None = None) -> HouseJudge:
    monkeypatch.setattr("urllib.request.urlopen", net)
    return HouseJudge(tau=0.5, statute_chars=600, base_url=PRIMARY, model=model, max_tokens=30, top_logprobs=20,
                      timeout_s=5, fallback_urls=fallbacks or [], enforce_served_model=enforce)


def _req() -> nyaya_judges.JudgeRequest:
    return nyaya_judges.JudgeRequest("c", "an element", False, "statute", "narrative", (("F1", "a fact"),))


def test_an_answer_under_the_pinned_id_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    net = _Net("nyaya-judge-4b")
    assert _judge(monkeypatch, net).judge(_req()).status is not None
    assert net.completions == [PRIMARY]


def test_an_answer_under_another_id_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    net = _Net("other-model-id")
    with pytest.raises(RuntimeError, match="pins 'nyaya-judge-4b'") as ei:
        _judge(monkeypatch, net).judge(_req())
    assert isinstance(ei.value.__cause__, nyaya_judges.ServedModelMismatch)


def test_a_mismatch_never_falls_back_to_the_next_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    net = _Net("something-else")
    with pytest.raises(RuntimeError):
        _judge(monkeypatch, net, fallbacks=[FALLBACK]).judge(_req())
    assert net.completions == [PRIMARY]


def test_the_check_is_off_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    net = _Net("other-model-id")
    _judge(monkeypatch, net, enforce=False).judge(_req())
    assert net.completions == [PRIMARY]


def test_a_model_resolved_from_models_is_not_a_pin(monkeypatch: pytest.MonkeyPatch) -> None:
    net = _Net("whatever-the-server-calls-itself", primary_listed="listed-id")
    _judge(monkeypatch, net, model=None).judge(_req())
    assert net.completions == [PRIMARY]


def test_a_fallback_answer_is_not_checked_against_the_primarys_pin(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Down(_Net):
        def __call__(self, req: Any, timeout: float = 0) -> _Resp:
            if req.full_url.startswith(PRIMARY) and not req.full_url.endswith("/models"):
                import urllib.error

                raise urllib.error.HTTPError(req.full_url, 503, "down", {}, io.BytesIO(b"{}"))  # type: ignore[arg-type]
            return super().__call__(req, timeout)

    net = _Down("the-fallbacks-own-id")
    out = _judge(monkeypatch, net, fallbacks=[FALLBACK]).judge(_req())
    assert out.backend_used == 1


def test_env_switch_turns_it_on_for_the_house_judge_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NYAYA_HOUSE_JUDGE_BASE_URL", PRIMARY)
    monkeypatch.setenv("NYAYA_HOUSE_JUDGE_MODEL", "nyaya-judge-4b")
    monkeypatch.setenv("NYAYA_HOUSE_JUDGE_ENFORCE_SERVED_MODEL", "1")
    monkeypatch.setenv("NYAYA_SECOND_JUDGE_BASE_URL", "http://second/v1")
    monkeypatch.setenv("NYAYA_SECOND_JUDGE_TAU", "0.97")
    monkeypatch.setenv("NYAYA_SECOND_JUDGE_TIMEOUT_S", "60")
    cfg = nyaya_agent.load_agent_config(REPO)
    assert cfg.house_judge["enforce_served_model"] is True
    assert cfg.house_judge["model"] == "nyaya-judge-4b"
    assert "enforce_served_model" not in (cfg.second_judge or {})


@pytest.mark.parametrize("value", ["", "0", "false", "no", "off"])
def test_env_switch_stays_off_unless_truthy(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("NYAYA_HOUSE_JUDGE_ENFORCE_SERVED_MODEL", value)
    cfg = nyaya_agent.load_agent_config(REPO)
    assert "enforce_served_model" not in cfg.house_judge
