"""#229: X-RateLimit-* on successful analyse-facts responses, Retry-After (and the same headers) on 429, from the
per-key meter; the OpenAPI document declares them. Constructed inputs only."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from pravrudhi.application import tenancy
from tests.test_partner_key_metering import ADMIN, H, _app, _key, _req

POST = "/api/v1/analyse-facts"
RL = ("x-ratelimit-limit", "x-ratelimit-remaining", "x-ratelimit-reset")


def test_success_carries_limit_remaining_and_reset_counting_down(monkeypatch: Any, tmp_path: Path) -> None:
    monkeypatch.setenv("PRAVRUDHI_ADMINS", ADMIN.id)
    c = TestClient(_app(tmp_path))
    secret = _key(c, "acme", limit=3)
    seen = []
    for _ in range(3):
        r = c.post(POST, json=_req(), headers={H: secret})
        assert r.status_code == 200
        seen.append((r.headers["x-ratelimit-limit"], r.headers["x-ratelimit-remaining"]))
        assert 1 <= int(r.headers["x-ratelimit-reset"]) <= 60
    assert seen == [("3", "2"), ("3", "1"), ("3", "0")]


def test_429_has_retry_after_and_the_rate_limit_headers(monkeypatch: Any, tmp_path: Path) -> None:
    monkeypatch.setenv("PRAVRUDHI_ADMINS", ADMIN.id)
    c = TestClient(_app(tmp_path))
    secret = _key(c, "acme", limit=1)
    assert c.post(POST, json=_req(), headers={H: secret}).status_code == 200
    r = c.post(POST, json=_req(), headers={H: secret})
    assert r.status_code == 429
    assert 1 <= int(r.headers["retry-after"]) <= 60
    assert (r.headers["x-ratelimit-limit"], r.headers["x-ratelimit-remaining"]) == ("1", "0")


def test_limiting_behaviour_is_unchanged_and_keys_are_independent(monkeypatch: Any, tmp_path: Path) -> None:
    monkeypatch.setenv("PRAVRUDHI_ADMINS", ADMIN.id)
    c = TestClient(_app(tmp_path))
    low, other = _key(c, "low", limit=2), _key(c, "other", limit=50)
    assert [c.post(POST, json=_req(), headers={H: low}).status_code for _ in range(4)] == [200, 200, 429, 429]
    r = c.post(POST, json=_req(), headers={H: other})
    assert r.status_code == 200 and r.headers["x-ratelimit-remaining"] == "49"
    assert tenancy.usage_counts(tmp_path, next(k.key_id for k in tenancy.keys_for_org(tmp_path, "low")))[0] == 2


def test_snapshot_is_read_only_and_resets_with_the_window() -> None:
    now = [120.0]
    rl = tenancy.KeyRateLimiter(now=lambda: now[0])
    assert rl.snapshot("k", 2) == (2, 2, 60)
    assert rl.allow("k", 2) and rl.snapshot("k", 2) == (2, 1, 60)
    assert rl.snapshot("k", 2) == (2, 1, 60)
    now[0] = 150.0
    assert rl.snapshot("k", 2) == (2, 1, 30)
    now[0] = 180.0
    assert rl.snapshot("k", 2) == (2, 2, 60)


def test_an_unkeyed_call_gets_no_per_key_headers(monkeypatch: Any, tmp_path: Path) -> None:
    monkeypatch.setenv("PRAVRUDHI_ADMINS", ADMIN.id)
    r = TestClient(_app(tmp_path)).post(POST, json=_req())
    assert r.status_code == 200 and not [h for h in RL if h in r.headers]


def test_openapi_declares_the_headers_on_200_and_429(tmp_path: Path) -> None:
    op = _app(tmp_path).openapi()["paths"][POST]["post"]["responses"]
    assert set(op["200"]["headers"]) == {"X-RateLimit-Limit", "X-RateLimit-Remaining", "X-RateLimit-Reset"}
    assert {"Retry-After", "X-RateLimit-Limit"} <= set(op["429"]["headers"])
