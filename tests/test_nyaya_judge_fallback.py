"""HouseJudge primary -> fallback rules (reviewer 1's rejection of fd8befe, 2026-09-24).

- Only a transport failure (connection error, timeout), a 5xx or a 429 moves to the next backend. A 401/403/404/400
  is a configuration fault and must surface: a revoked key must never silently downgrade every call to the fallback.
- Each backend answers under its own model id (the RunPod endpoint serves `nyaya-judge-4b`, the 5090 serves the HF
  repo id), resolved from that backend's own /models when not configured.
- The bearer key goes to the primary only.
"""

from __future__ import annotations

import io
import json
import urllib.error
from typing import Any

import pytest

from pravrudhi.application import nyaya_judges
from pravrudhi.application.nyaya_judges import HouseJudge

PRIMARY = "http://primary/v1"
FALLBACK = "http://fallback/v1"
_TOP = {" established": -0.1, " not": -3.0}


class _Resp(io.BytesIO):
    def __enter__(self) -> _Resp:
        return self

    def __exit__(self, *a: object) -> None:
        self.close()


def _completion() -> bytes:
    return json.dumps({"model": "m", "choices": [{"text": " established F1", "finish_reason": "length",
                       "logprobs": {"top_logprobs": [_TOP]}}]}).encode()


class _Net:
    """A scripted network: per base URL, a behaviour for /completions and a /models list."""

    def __init__(self, primary: str, fallback: str = "ok") -> None:
        self.behaviour = {PRIMARY: primary, FALLBACK: fallback}
        self.models = {PRIMARY: ["nyaya-judge-4b"], FALLBACK: ["AxisMeru/prabhasa-nyaya-element-judge-4b-v0"]}
        self.calls: list[dict[str, Any]] = []

    def __call__(self, req: Any, timeout: float = 0) -> _Resp:
        url = req.full_url
        base = PRIMARY if url.startswith(PRIMARY) else FALLBACK
        auth = req.get_header("Authorization")
        if url.endswith("/models"):
            self.calls.append({"base": base, "path": "models", "auth": auth})
            return _Resp(json.dumps({"data": [{"id": m} for m in self.models[base]]}).encode())
        body = json.loads(req.data.decode())
        self.calls.append({"base": base, "path": "completions", "auth": auth, "model": body["model"]})
        b = self.behaviour[base]
        if b == "ok":
            return _Resp(_completion())
        if b == "timeout":
            raise TimeoutError("timed out")
        if b == "refused":
            raise urllib.error.URLError(ConnectionRefusedError("refused"))
        code = int(b)
        raise urllib.error.HTTPError(url, code, "err", {}, io.BytesIO(b'{"error":"x"}'))  # type: ignore[arg-type]


def _judge(monkeypatch: pytest.MonkeyPatch, net: _Net, *, model: str | None = "nyaya-judge-4b") -> HouseJudge:
    monkeypatch.setattr("urllib.request.urlopen", net)
    return HouseJudge(tau=0.5, statute_chars=600, base_url=PRIMARY, model=model, max_tokens=30, top_logprobs=20,
                      timeout_s=5, api_key="primary-secret", fallback_urls=[FALLBACK])


def _req() -> nyaya_judges.JudgeRequest:
    return nyaya_judges.JudgeRequest("c", "an element", False, "statute", "narrative", (("F1", "a fact"),))


@pytest.mark.parametrize("code", ["401", "403", "404", "400"])
def test_a_client_error_from_the_primary_surfaces_and_never_falls_back(monkeypatch: pytest.MonkeyPatch, code: str) -> None:
    net = _Net(primary=code)
    j = _judge(monkeypatch, net)
    with pytest.raises(RuntimeError, match=code):
        j.judge(_req())
    assert [c for c in net.calls if c["base"] == FALLBACK] == []


@pytest.mark.parametrize("failure", ["timeout", "refused", "503", "500", "429"])
def test_a_transient_primary_failure_falls_back_and_is_recorded(monkeypatch: pytest.MonkeyPatch, failure: str) -> None:
    net = _Net(primary=failure)
    out = _judge(monkeypatch, net).judge(_req())
    assert out.backend_used == 1


def test_the_fallback_answers_under_its_own_model_id(monkeypatch: pytest.MonkeyPatch) -> None:
    net = _Net(primary="503")
    _judge(monkeypatch, net).judge(_req())
    fb = [c for c in net.calls if c["base"] == FALLBACK and c["path"] == "completions"]
    assert fb and fb[0]["model"] == "AxisMeru/prabhasa-nyaya-element-judge-4b-v0"


def test_the_key_goes_to_the_primary_only(monkeypatch: pytest.MonkeyPatch) -> None:
    net = _Net(primary="503")
    _judge(monkeypatch, net).judge(_req())
    assert all(c["auth"] == "Bearer primary-secret" for c in net.calls if c["base"] == PRIMARY)
    assert all(c["auth"] is None for c in net.calls if c["base"] == FALLBACK)


def test_a_healthy_primary_is_used_and_the_fallback_never_touched(monkeypatch: pytest.MonkeyPatch) -> None:
    net = _Net(primary="ok")
    assert _judge(monkeypatch, net).judge(_req()).backend_used == 0
    assert [c for c in net.calls if c["base"] == FALLBACK] == []


def test_the_key_never_appears_in_the_error(monkeypatch: pytest.MonkeyPatch) -> None:
    net = _Net(primary="401")
    with pytest.raises(RuntimeError) as exc:
        _judge(monkeypatch, net).judge(_req())
    assert "primary-secret" not in str(exc.value)


def test_deployment_env_sets_model_and_fallbacks(tmp_path: object, monkeypatch: pytest.MonkeyPatch) -> None:
    from pathlib import Path

    from pravrudhi.application.nyaya_agent import load_agent_config

    root = Path(__file__).resolve().parent.parent
    monkeypatch.setenv("NYAYA_HOUSE_JUDGE_MODEL", "nyaya-judge-4b")
    monkeypatch.setenv("NYAYA_HOUSE_JUDGE_FALLBACK_URLS", "http://a/v1, http://b/v1")
    hj = load_agent_config(root).house_judge
    assert hj["model"] == "nyaya-judge-4b"
    assert hj["base_urls_fallback"] == ["http://a/v1", "http://b/v1"]
