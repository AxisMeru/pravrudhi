"""Per-user rate limit and daily cap on /api/chat (each turn spends the operator's PRAVRUDHI_CHAT_API_KEY)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from pravrudhi.api import identity
from pravrudhi.api.chat import build_chat_router
from pravrudhi.api.chat_limits import DAILY_CAP_ENV, DEFAULT_DAILY_CAP, DEFAULT_PER_MINUTE, PER_MINUTE_ENV, ChatLimiter
from pravrudhi.api.identity import User

U1 = User(id="u-1", email="a@example.com", role="authenticated")
U2 = User(id="u-2", email="b@example.com", role="authenticated")


class Clock:
    def __init__(self, t: float = 1_700_000_000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


class Model:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, messages: list[dict[str, str]], tools: list[dict[str, Any]]) -> dict[str, Any]:
        self.calls += 1
        return {"content": "ok", "tool_calls": []}


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    for v in (PER_MINUTE_ENV, DAILY_CAP_ENV):
        monkeypatch.delenv(v, raising=False)


# -- the limiter ----------------------------------------------------------------------------------------------------


def test_defaults_are_sane_and_read_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    lim = ChatLimiter()
    assert (lim.per_minute, lim.daily_cap) == (DEFAULT_PER_MINUTE, DEFAULT_DAILY_CAP)
    monkeypatch.setenv(PER_MINUTE_ENV, "3")
    monkeypatch.setenv(DAILY_CAP_ENV, "7")
    lim = ChatLimiter()
    assert (lim.per_minute, lim.daily_cap) == (3, 7)


@pytest.mark.parametrize("bad", ["", "abc", "-5", "1.5"])
def test_an_unreadable_value_falls_back_to_the_default_never_to_unlimited(monkeypatch: pytest.MonkeyPatch, bad: str) -> None:
    monkeypatch.setenv(PER_MINUTE_ENV, bad)
    monkeypatch.setenv(DAILY_CAP_ENV, bad)
    lim = ChatLimiter()
    assert (lim.per_minute, lim.daily_cap) == (DEFAULT_PER_MINUTE, DEFAULT_DAILY_CAP)


def test_the_per_minute_window_refuses_then_rolls_over() -> None:
    clock = Clock()
    lim = ChatLimiter(3, 100, now=clock)
    assert [lim.check("a") for _ in range(3)] == [None, None, None]
    refusal = lim.check("a")
    assert refusal is not None and refusal.reason == "per_minute" and 1 <= refusal.retry_after_s <= 61
    clock.t += 61
    assert lim.check("a") is None


def test_the_daily_cap_refuses_until_the_utc_day_rolls_over() -> None:
    clock = Clock(1_700_000_000.0)  # 2023-11-14 22:13 UTC
    lim = ChatLimiter(0, 5, now=clock)  # per-minute off
    assert all(lim.check("a") is None for _ in range(5))
    refusal = lim.check("a")
    assert refusal is not None and refusal.reason == "daily"
    clock.t += 3600  # an hour later, still the same UTC day? (22:13 + 1h = 23:13)
    assert lim.check("a") is not None
    clock.t += 3600  # past UTC midnight
    assert lim.check("a") is None


def test_users_are_isolated_from_each_other() -> None:
    lim = ChatLimiter(2, 3, now=Clock())
    assert lim.check("a") is None and lim.check("a") is None and lim.check("a") is not None
    assert lim.check("b") is None and lim.check("b") is None  # b is untouched by a's breach


def test_zero_switches_a_limit_off_explicitly() -> None:
    lim = ChatLimiter(0, 0, now=Clock())
    assert all(lim.check("a") is None for _ in range(1000))


def test_a_new_key_is_never_admitted_by_evicting_a_live_one() -> None:
    lim = ChatLimiter(1, 10, now=Clock(), max_keys=2)
    assert lim.check("a") is None and lim.check("b") is None
    assert lim.check("c") is not None  # table full of live keys
    assert lim.check("a") is not None  # a is still counted, not reset by c's attempt


# -- the routes -----------------------------------------------------------------------------------------------------


def _client(tmp_path: Path, limiter: ChatLimiter, user: User | None, model: Model) -> TestClient:
    app = FastAPI()
    app.include_router(build_chat_router(tmp_path, complete=model, limiter=limiter))
    app.dependency_overrides[identity.current_user] = lambda: user
    return TestClient(app)


def test_the_route_answers_429_with_retry_after_on_breach_and_does_not_call_the_model(tmp_path: Path) -> None:
    model = Model()
    c = _client(tmp_path, ChatLimiter(2, 100, now=Clock()), U1, model)
    assert [c.post("/api/chat", json={"message": "hi"}).status_code for _ in range(2)] == [200, 200]
    r = c.post("/api/chat", json={"message": "hi"})
    assert r.status_code == 429 and int(r.headers["retry-after"]) >= 1 and "chat rate limit" in r.json()["detail"]
    assert model.calls == 2  # the refused turn never reached the operator's key
    s = c.post("/api/chat/stream", json={"message": "hi"})
    assert s.status_code == 429  # the stream route shares the budget


def test_the_daily_cap_is_a_429_with_its_own_wording(tmp_path: Path) -> None:
    c = _client(tmp_path, ChatLimiter(0, 1, now=Clock()), U1, Model())
    assert c.post("/api/chat", json={"message": "hi"}).status_code == 200
    r = c.post("/api/chat", json={"message": "hi"})
    assert r.status_code == 429 and "daily chat limit" in r.json()["detail"]


def test_another_user_is_unaffected_by_a_breach_on_a_shared_limiter(tmp_path: Path) -> None:
    limiter = ChatLimiter(1, 100, now=Clock())
    model = Model()
    a = _client(tmp_path / "a", limiter, U1, model)
    b = _client(tmp_path / "b", limiter, U2, model)
    assert a.post("/api/chat", json={"message": "hi"}).status_code == 200
    assert a.post("/api/chat", json={"message": "hi"}).status_code == 429
    assert b.post("/api/chat", json={"message": "hi"}).status_code == 200


def test_an_empty_message_is_a_422_and_costs_no_budget(tmp_path: Path) -> None:
    c = _client(tmp_path, ChatLimiter(1, 100, now=Clock()), U1, Model())
    assert c.post("/api/chat", json={"message": "  "}).status_code == 422
    assert c.post("/api/chat", json={"message": "hi"}).status_code == 200


def test_the_local_operator_with_authentication_off_is_not_limited(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PRAVRUDHI_AUTH", "disabled")
    c = _client(tmp_path, ChatLimiter(1, 1, now=Clock()), None, Model())
    assert all(c.post("/api/chat", json={"message": "hi"}).status_code == 200 for _ in range(5))


def test_an_anonymous_caller_on_an_authenticating_engine_is_limited_per_address(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PRAVRUDHI_AUTH", "optional")
    c = _client(tmp_path, ChatLimiter(1, 100, now=Clock()), None, Model())
    # The first turn is admitted by the limiter (whatever the memory store then says about an anonymous caller).
    assert c.post("/api/chat", json={"message": "hi"}).status_code != 429
    assert c.post("/api/chat", json={"message": "hi"}).status_code == 429


def test_the_reading_routes_are_not_limited(tmp_path: Path) -> None:
    c = _client(tmp_path, ChatLimiter(1, 1, now=Clock()), U1, Model())
    assert all(c.get("/api/chat/threads").status_code == 200 for _ in range(5))
