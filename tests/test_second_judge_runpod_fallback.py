"""A second RunPod endpoint as the second judge's fallback: it takes the primary's key and model pin (same host),
a fallback on any other host never does, and a parked primary with a live fallback is not 'offline'."""

from __future__ import annotations

import http.server
import io
import json
import threading
import urllib.error
from typing import Any

import pytest

from pravrudhi.application import judge_endpoint_state as jes
from pravrudhi.application import nyaya_judges
from pravrudhi.application.nyaya_judges import HouseJudge, ServedModelMismatch, key_may_follow, runpod_endpoint_of

PRIMARY = "https://api.runpod.ai/v2/epaaa/openai/v1"
CLONE = "https://api.runpod.ai/v2/epbbb/openai/v1"
LOCAL = "http://192.0.2.7:8132/v1"
ALLOW = frozenset({"epaaa", "epbbb"})
_TOP = {" established": -0.1, " not": -3.0}


class _Resp(io.BytesIO):
    def __enter__(self) -> _Resp:
        return self

    def __exit__(self, *a: object) -> None:
        self.close()


class _Net:
    def __init__(self, behaviour: dict[str, str], served: dict[str, str] | None = None) -> None:
        self.behaviour, self.calls = behaviour, []
        self.served = served or {}

    def __call__(self, req: Any, timeout: float = 0) -> _Resp:
        base = next(b for b in self.behaviour if req.full_url.startswith(b))
        auth = req.get_header("Authorization")
        if req.full_url.endswith("/models"):
            return _Resp(json.dumps({"data": [{"id": self.served.get(base, "judge32b")}]}).encode())
        body = json.loads(req.data.decode())
        self.calls.append({"base": base, "auth": auth, "model": body["model"]})
        if self.behaviour[base] == "timeout":
            raise TimeoutError("timed out")
        if self.behaviour[base] != "ok":
            code = int(self.behaviour[base])
            raise urllib.error.HTTPError(req.full_url, code, "err", {}, io.BytesIO(b"{}"))  # type: ignore[arg-type]
        return _Resp(json.dumps({"model": self.served.get(base, "judge32b"), "choices": [{"text": " established F1",
                     "finish_reason": "length", "logprobs": {"top_logprobs": [_TOP]}}]}).encode())


def _req() -> nyaya_judges.JudgeRequest:
    return nyaya_judges.JudgeRequest("c", "an element", False, "statute", "narrative", (("F1", "a fact"),))


def _judge(mp: pytest.MonkeyPatch, net: _Net, fallbacks: list[str], *, enforce: bool = False,
           allow: frozenset[str] = ALLOW) -> HouseJudge:
    mp.setattr("urllib.request.urlopen", net)
    return HouseJudge(tau=0.97, statute_chars=600, base_url=PRIMARY, model="judge32b", max_tokens=30, top_logprobs=20,
                      timeout_s=5, api_key="secret", fallback_urls=fallbacks, enforce_served_model=enforce,
                      key_endpoint_ids=allow)


@pytest.mark.parametrize("url,expected", [
    ("https://api.runpod.ai/v2/epbbb/openai/v1", "epbbb"),
    ("https://API.RUNPOD.AI/v2/epbbb/openai/v1", None),  # hostname() lowercases: accepted below via case-fold test
    ("https://api.runpod.ai:443/v2/epbbb/openai/v1", "epbbb"),
    ("https://api.runpod.ai:8443/v2/epbbb/openai/v1", None),
    ("http://api.runpod.ai/v2/epbbb/openai/v1", None),
    ("https://api.runpod.ai.evil.example/v2/epbbb/openai/v1", None),
    ("https://api.runpod.ai@evil.example/v2/epbbb/openai/v1", None),
    ("https://user:pw@api.runpod.ai/v2/epbbb/openai/v1", None),
    ("https://api.runpod.ai./v2/epbbb/openai/v1", None),
    ("https://evil.example/v2/epbbb/openai/v1", None),
    ("https://api.runpod.ai/openai/v1", None),
    ("https://epbbb1.api.runpod.ai/v1", "epbbb1"),  # load-balancer form: one label before the host
    ("https://epbbb1.api.runpod.ai:443/v1", "epbbb1"),
    ("https://x.epbbb1.api.runpod.ai/v1", None),  # two labels
    ("https://epbbb1.api.runpod.ai.evil.example/v1", None),
    ("https://epbbb1.api.runpod.ai@evil.example/v1", None),
    ("http://epbbb1.api.runpod.ai/v1", None),
    ("https://ep_bb.api.runpod.ai/v1", None),
    ("https://short.api.runpod.ai/v1", None),  # a 5-char label is not an endpoint id
    ("", None),
])
def test_runpod_endpoint_of_is_parsed_not_matched(url: str, expected: str | None) -> None:
    got = runpod_endpoint_of(url)
    if url.startswith("https://API.RUNPOD.AI"):
        assert got == "epbbb"  # host is case-folded by the parser, which is the stated rule
    else:
        assert got == expected


def test_key_may_follow_needs_the_allowlist() -> None:
    assert key_may_follow(CLONE, ALLOW)
    assert not key_may_follow("https://api.runpod.ai/v2/someoneelse/openai/v1", ALLOW)  # same host, not OUR id
    assert not key_may_follow(CLONE, frozenset())
    assert not key_may_follow(LOCAL, ALLOW)


@pytest.mark.parametrize("primary", ["timeout", "503"])
def test_same_host_fallback_gets_the_key_and_the_primary_model_id(monkeypatch: pytest.MonkeyPatch, primary: str) -> None:
    net = _Net({PRIMARY: primary, CLONE: "ok"})
    out = _judge(monkeypatch, net, [CLONE]).judge(_req())
    assert out.backend_used == 1
    clone_calls = [c for c in net.calls if c["base"] == CLONE]
    assert clone_calls and clone_calls[0]["auth"] == "Bearer secret" and clone_calls[0]["model"] == "judge32b"


def test_other_host_fallback_never_gets_the_key(monkeypatch: pytest.MonkeyPatch) -> None:
    net = _Net({PRIMARY: "503", LOCAL: "ok"}, served={LOCAL: "local-id"})
    _judge(monkeypatch, net, [LOCAL]).judge(_req())
    local = [c for c in net.calls if c["base"] == LOCAL]
    assert local and local[0]["auth"] is None and local[0]["model"] == "local-id"


def test_same_host_fallback_is_pinned_when_enforced(monkeypatch: pytest.MonkeyPatch) -> None:
    net = _Net({PRIMARY: "503", CLONE: "ok"}, served={CLONE: "some-other-model"})
    with pytest.raises((ServedModelMismatch, RuntimeError), match="pins"):
        _judge(monkeypatch, net, [CLONE], enforce=True).judge(_req())


def test_other_host_fallback_stays_unpinned_when_enforced(monkeypatch: pytest.MonkeyPatch) -> None:
    net = _Net({PRIMARY: "503", LOCAL: "ok"}, served={LOCAL: "local-id"})
    assert _judge(monkeypatch, net, [LOCAL], enforce=True).judge(_req()).backend_used == 1


def test_a_401_from_the_same_host_fallback_surfaces(monkeypatch: pytest.MonkeyPatch) -> None:
    net = _Net({PRIMARY: "503", CLONE: "401"})
    with pytest.raises(RuntimeError, match="401"):
        _judge(monkeypatch, net, [CLONE]).judge(_req())


def test_both_fail_raises_as_today(monkeypatch: pytest.MonkeyPatch) -> None:
    net = _Net({PRIMARY: "timeout", CLONE: "timeout"})
    with pytest.raises(RuntimeError):
        _judge(monkeypatch, net, [CLONE]).judge(_req())


def _health(ready: int = 0, queued: int = 0) -> dict[str, Any]:
    return {"jobs": {"inQueue": queued, "inProgress": 0, "completed": 1},
            "workers": {"ready": ready, "idle": 0, "running": 0, "initializing": 0, "throttled": 0, "unhealthy": 0}}


def _check(states: dict[str, dict[str, Any] | None]) -> jes.JudgeOfflineCheck:
    def fetch(url: str, headers: Any) -> dict[str, Any]:
        for ep, h in states.items():
            if f"/{ep}/" in url:
                if h is None:
                    raise urllib.error.URLError("down")
                return h
        raise AssertionError(url)

    return jes.JudgeOfflineCheck(fetch=fetch)


def _off(check: jes.JudgeOfflineCheck, block: dict[str, Any]) -> bool:
    return check.offline({"second": block}, {"second": "K"})


def test_parked_primary_without_fallback_is_offline() -> None:
    assert _off(_check({"epaaa": _health()}), {"base_url": PRIMARY})


def test_parked_primary_with_ready_runpod_fallback_is_not_offline() -> None:
    assert not _off(_check({"epaaa": _health(), "epbbb": _health(ready=1)}), {"base_url": PRIMARY, "base_urls_fallback": [CLONE]})


def test_parked_primary_and_parked_fallback_is_offline() -> None:
    assert _off(_check({"epaaa": _health(), "epbbb": _health()}), {"base_url": PRIMARY, "base_urls_fallback": [CLONE]})


def test_parked_primary_with_unreadable_fallback_is_not_offline() -> None:
    assert not _off(_check({"epaaa": _health(), "epbbb": None}), {"base_url": PRIMARY, "base_urls_fallback": [CLONE]})


def test_parked_primary_with_non_runpod_fallback_is_not_offline() -> None:
    assert not _off(_check({"epaaa": _health()}), {"base_url": PRIMARY, "base_urls_fallback": [LOCAL]})


def test_warm_primary_is_not_offline_whatever_the_fallback() -> None:
    assert not _off(_check({"epaaa": _health(ready=1)}), {"base_url": PRIMARY, "base_urls_fallback": [CLONE]})


def test_a_same_host_fallback_not_on_the_allowlist_gets_no_key(monkeypatch: pytest.MonkeyPatch) -> None:
    other = "https://api.runpod.ai/v2/someoneelse/openai/v1"
    net = _Net({PRIMARY: "503", other: "ok"}, served={other: "x"})
    _judge(monkeypatch, net, [other]).judge(_req())
    assert [c["auth"] for c in net.calls if c["base"] == other] == [None]


def test_no_allowlist_means_no_fallback_key(monkeypatch: pytest.MonkeyPatch) -> None:
    net = _Net({PRIMARY: "503", CLONE: "ok"})
    _judge(monkeypatch, net, [CLONE], allow=frozenset()).judge(_req())
    assert [c["auth"] for c in net.calls if c["base"] == CLONE] == [None]


def test_provenance_records_endpoint_and_served_model(monkeypatch: pytest.MonkeyPatch) -> None:
    net = _Net({PRIMARY: "503", CLONE: "ok"})
    out = _judge(monkeypatch, net, [CLONE]).judge(_req())
    assert (out.backend_used, out.backend_endpoint, out.backend_model) == (1, "epbbb", "judge32b")
    net2 = _Net({PRIMARY: "ok", CLONE: "ok"})
    out2 = _judge(monkeypatch, net2, [CLONE]).judge(_req())
    assert (out2.backend_used, out2.backend_endpoint) == (0, "epaaa")


def test_the_gate_refuses_a_fallback_without_a_passing_record(monkeypatch: pytest.MonkeyPatch) -> None:
    net = _Net({PRIMARY: "503", CLONE: "ok"})
    j = _judge(monkeypatch, net, [CLONE])
    j.fallback_gate = lambda ep: (False, "no record")
    with pytest.raises(RuntimeError, match="refused: no record"):
        j.judge(_req())
    assert [c for c in net.calls if c["base"] == CLONE] == []  # never reached


def test_the_gate_lets_a_fallback_with_a_passing_record_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    net = _Net({PRIMARY: "503", CLONE: "ok"})
    j = _judge(monkeypatch, net, [CLONE])
    seen: list[str] = []
    j.fallback_gate = lambda ep: (seen.append(ep) or True, "ok")
    assert j.judge(_req()).backend_used == 1 and seen == ["epbbb"]


def test_the_gate_never_applies_to_the_primary(monkeypatch: pytest.MonkeyPatch) -> None:
    net = _Net({PRIMARY: "ok", CLONE: "ok"})
    j = _judge(monkeypatch, net, [CLONE])
    j.fallback_gate = lambda ep: (False, "no record")
    assert j.judge(_req()).backend_used == 0


class _Hop(http.server.BaseHTTPRequestHandler):
    seen: list[str | None] = []
    target = ""

    def do_GET(self) -> None:  # noqa: N802
        if self.path.startswith("/start"):
            self.send_response(307)
            self.send_header("Location", _Hop.target + "/models")
            self.end_headers()
        else:
            _Hop.seen.append(self.headers.get("Authorization"))
            body = b'{"data":[{"id":"m"}]}'
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    def log_message(self, *a: object) -> None:
        pass


def test_a_redirect_never_carries_the_key() -> None:
    from pravrudhi.models.openai_compat import ChatClient

    a = http.server.HTTPServer(("127.0.0.1", 0), _Hop)
    b = http.server.HTTPServer(("127.0.0.1", 0), _Hop)
    _Hop.target = f"http://127.0.0.1:{b.server_port}"
    _Hop.seen = []
    ts = [threading.Thread(target=s.serve_forever, daemon=True) for s in (a, b)]
    for t in ts:
        t.start()
    try:
        c = ChatClient(base_url=f"http://127.0.0.1:{a.server_port}/start", model="m", api_key="secret", timeout_s=5)
        c.list_models()
    finally:
        for s in (a, b):
            s.shutdown()
            s.server_close()
    assert _Hop.seen == [None]  # the hop was followed, and the second server saw no Authorization header


# -- config wiring (nyaya_agent.load_agent_config) ---------------------------------------------------------------
from pathlib import Path  # noqa: E402

from pravrudhi.application import nyaya_agent  # noqa: E402

REPO = Path(nyaya_agent.__file__).parents[3]
_ENV = ("NYAYA_SECOND_JUDGE_BASE_URL", "NYAYA_SECOND_JUDGE_MODEL", "NYAYA_SECOND_JUDGE_TAU", "NYAYA_SECOND_JUDGE_TIMEOUT_S",
        "NYAYA_SECOND_JUDGE_FALLBACK_URLS", "NYAYA_SECOND_JUDGE_FALLBACK_ENDPOINT_IDS", "NYAYA_SECOND_JUDGE_FALLBACK_RECORD_DIR",
        "NYAYA_HOUSE_JUDGE_MODEL")


def _second_env(mp: pytest.MonkeyPatch, **extra: str) -> None:
    for v in _ENV:
        mp.delenv(v, raising=False)
    mp.setenv("PRAVRUDHI_EDITION", "dev")
    for k, v in {"NYAYA_SECOND_JUDGE_BASE_URL": PRIMARY, "NYAYA_SECOND_JUDGE_MODEL": "judge32b",
                 "NYAYA_SECOND_JUDGE_TAU": "0.97", "NYAYA_SECOND_JUDGE_TIMEOUT_S": "90", **extra}.items():
        mp.setenv(k, v)


def test_a_runpod_fallback_without_a_record_dir_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    _second_env(monkeypatch, NYAYA_SECOND_JUDGE_FALLBACK_URLS=CLONE)
    with pytest.raises(ValueError, match="FALLBACK_RECORD_DIR"):
        nyaya_agent.load_agent_config(REPO)


def test_a_non_runpod_fallback_needs_no_record_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    _second_env(monkeypatch, NYAYA_SECOND_JUDGE_FALLBACK_URLS=LOCAL)
    assert nyaya_agent.load_agent_config(REPO).second_judge["base_urls_fallback"] == [LOCAL]


def test_a_runpod_fallback_with_ids_and_a_record_dir_loads(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _second_env(monkeypatch, NYAYA_SECOND_JUDGE_FALLBACK_URLS=CLONE,
                NYAYA_SECOND_JUDGE_FALLBACK_ENDPOINT_IDS="epaaa111, epbbb111",
                NYAYA_SECOND_JUDGE_FALLBACK_RECORD_DIR=str(tmp_path))
    sj = nyaya_agent.load_agent_config(REPO).second_judge
    assert sj["fallback_key_endpoint_ids"] == ["epaaa111", "epbbb111"] and sj["fallback_record_dir"] == str(tmp_path)


@pytest.mark.parametrize("bad", ["", " , ", "ep/../x", "a b", "short"])
def test_a_malformed_allowlist_is_refused(monkeypatch: pytest.MonkeyPatch, bad: str) -> None:
    _second_env(monkeypatch, NYAYA_SECOND_JUDGE_FALLBACK_ENDPOINT_IDS=bad)
    if bad == "":
        nyaya_agent.load_agent_config(REPO)  # unset-equivalent: ignored as today
    else:
        with pytest.raises(ValueError, match="ENDPOINT_IDS"):
            nyaya_agent.load_agent_config(REPO)


_IDS = frozenset({"epbbb111", "epaaa111"})


@pytest.mark.parametrize("url,expected", [
    ("https://epbbb111.api.runpod.ai/v1", True),  # load-balancer form, listed id
    ("https://EPBBB111.API.RUNPOD.AI/v1", True),  # case-folded host
    ("https://api.runpod.ai/v2/epbbb111/openai/v1", True),  # queue form, listed id
    ("https://epccc111.api.runpod.ai/v1", False),  # same domain, unlisted label
    ("https://api.runpod.ai/v2/epccc111/openai/v1", False),  # path form, unlisted id
    ("https://api.runpod.ai/openai/v1", False),
    ("https://a.epbbb111.api.runpod.ai/v1", False),  # nested label
    ("https://epbbb111.api.runpod.ai.example/v1", False),  # suffix trick
    ("https://epbbb111.api.runpod.ai@evil.example/v1", False),  # userinfo trick
    ("https://user:pw@epbbb111.api.runpod.ai/v1", False),
    ("https://epbbb111.api.runpod.ai./v1", False),  # trailing dot
    ("https://epbbb111.api.runpod.ai:443/v1", False),  # explicit port
    ("https://epbbb111.api.runpod.ai:8443/v1", False),
    ("http://epbbb111.api.runpod.ai/v1", False),  # not https
    ("https://evil.example/v2/epbbb111/openai/v1", False),
    ("https://epbbb111.api.runpod.ai.evil.example/v2/epbbb111/", False),
    ("", False),
])
def test_exact_host_set_decides_who_gets_the_key(url: str, expected: bool) -> None:
    assert key_may_follow(url, _IDS) is expected


def test_host_form_and_path_form_do_not_cross_match() -> None:
    assert not key_may_follow("https://api.runpod.ai/v2/epbbb111/openai/v1", frozenset({"epaaa111"}))
    assert not key_may_follow("https://epbbb111.api.runpod.ai/v1", frozenset())
