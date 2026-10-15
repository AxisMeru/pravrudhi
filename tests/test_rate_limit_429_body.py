"""#403: the 429 body names the limit and the window and says what to do; `detail` and `Retry-After` are unchanged.
Both arms: anonymous vs signed-in vs the per-key limiter, on both routes that use the address limiter."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from pravrudhi.api import identity
from pravrudhi.api.partner import PartnerApiConfig, RateLimiter, build_partner_router, rate_limited_response
from pravrudhi.application import tenancy
from tests.test_api_partner import _agent, _proof_script
from tests.test_partner_key_metering import ADMIN, FakeClock, _key, _req

H = tenancy.API_KEY_HEADER
ANON_TEXT = (
    "You have reached the limit for visitors who are not signed in ({limit} requests per minute). "
    "Try again in {retry} seconds, or sign in or use an API key to get your own limit."
)
CALLER_TEXT = "You have reached your limit of {limit} requests per minute. Try again in {retry} seconds."
VERIFY_ANON_TEXT = (
    "You have reached the limit for callers without an API key ({limit} requests per minute). "
    "Try again in {retry} seconds, or use an API key to get your own limit."
)
KEY_TEXT = "Your key has reached its limit of {limit} requests per minute. Try again in {retry} seconds."
ROUTES = [
    pytest.param("/api/v1/analyse-facts", _req(), id="analyse-facts"),
    pytest.param("/api/v1/verify-citations", {"citation": "(1977) 3 SCC 247"}, id="verify-citations"),
]



def _client(tmp_path: Path, *, signed_in: bool, limit: int = 2) -> TestClient:
    cfg = PartnerApiConfig(rate_limit_per_minute=limit, max_concurrent=100, trust_proxy_header=False)
    app = FastAPI()
    app.include_router(build_partner_router(
        tmp_path, agent_factory=lambda _r: _agent(tmp_path, _proof_script()), config=cfg, rate_clock=FakeClock()
    ))
    app.dependency_overrides[identity.current_user] = lambda: ADMIN if signed_in else None
    return TestClient(app)


def _until_429(c: TestClient, path: str, body: dict[str, Any], headers: dict[str, str] | None = None) -> Any:
    for _ in range(10):
        r = c.post(path, json=body, headers=headers or {})
        if r.status_code == 429:
            return r
    raise AssertionError("never rate limited")


def _check_shape(r: Any, *, limit: int, scope: str, text: str) -> None:
    b = r.json()
    assert b["detail"] == "rate limit exceeded"  # the machine field is unchanged
    assert b["limit_per_minute"] == limit and b["window_seconds"] == 60 and b["scope"] == scope
    assert b["retry_after_seconds"] == int(r.headers["Retry-After"]) and 1 <= b["retry_after_seconds"] <= 60
    assert b["message"] == text.format(limit=limit, retry=b["retry_after_seconds"])


@pytest.mark.parametrize("path,body", ROUTES)
def test_an_anonymous_caller_is_told_the_visitor_limit_and_to_sign_in(tmp_path: Path, path: str, body: dict[str, Any]) -> None:
    r = _until_429(_client(tmp_path, signed_in=False, limit=2), path, body)
    if path.endswith("verify-citations"):
        _check_shape(r, limit=2, scope="anonymous", text=VERIFY_ANON_TEXT)  # key-only route: no "sign in"
        assert "sign in" not in r.json()["message"]
    else:
        _check_shape(r, limit=2, scope="anonymous", text=ANON_TEXT)
        assert "sign in" in r.json()["message"] and "API key" in r.json()["message"]


def test_the_limit_in_the_body_is_the_configured_value(tmp_path: Path) -> None:
    r = _until_429(_client(tmp_path, signed_in=False, limit=6), "/api/v1/analyse-facts", _req())
    assert r.json()["limit_per_minute"] == 6


def test_a_signed_in_caller_whose_address_bucket_fired_is_not_told_to_sign_in(tmp_path: Path) -> None:
    c = _client(tmp_path, signed_in=True, limit=2)
    r = _until_429(c, "/api/v1/analyse-facts", _req())
    # Signed-in callers are bucketed by identity: this is their own limit
    _check_shape(r, limit=2, scope="caller", text=CALLER_TEXT)
    assert "sign in" not in r.json()["message"]


def test_a_keyed_caller_over_its_own_limit_gets_the_key_text_and_keeps_the_rate_headers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("PRAVRUDHI_ADMINS", ADMIN.id)
    c = _client(tmp_path, signed_in=True, limit=1000)
    secret = _key(c, "acme", limit=2)
    r = _until_429(c, "/api/v1/analyse-facts", _req(), {H: secret})
    _check_shape(r, limit=2, scope="key", text=KEY_TEXT)
    assert "X-RateLimit-Limit" in r.headers and "Retry-After" in r.headers


def test_the_helper_never_changes_the_machine_fields_or_leaks_extra_headers() -> None:
    r = rate_limited_response(limit=6, retry_after=17, scope="anonymous")
    assert r.status_code == 429 and r.headers["Retry-After"] == "17"
    assert set(r.headers) <= {"retry-after", "content-length", "content-type"}


def test_the_limiter_exposes_its_configured_value() -> None:
    assert RateLimiter(per_minute=6).per_minute == 6


def test_an_unknown_scope_falls_to_the_plain_caller_text() -> None:
    r = rate_limited_response(limit=3, retry_after=5, scope="other")
    assert r.body is not None and b"You have reached your limit of 3 requests per minute" in bytes(r.body)


def test_verify_citations_ignores_sign_in_so_a_signed_in_user_gets_the_key_only_text(tmp_path: Path) -> None:
    """R2's gap on #410: verify-citations only knows API keys (no session dependency), so a signed-in user without a key is in
    the anonymous address bucket there, and the message must not tell them to sign in for a limit they would not get."""
    r = _until_429(_client(tmp_path, signed_in=True, limit=2), "/api/v1/verify-citations", {"citation": "(1977) 3 SCC 247"})
    _check_shape(r, limit=2, scope="anonymous", text=VERIFY_ANON_TEXT)
    assert "sign in" not in r.json()["message"]


def test_verify_citations_keyed_caller_gets_the_key_scope(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("PRAVRUDHI_ADMINS", ADMIN.id)
    c = _client(tmp_path, signed_in=True, limit=1000)
    secret = _key(c, "acme", limit=2)
    r = _until_429(c, "/api/v1/verify-citations", {"citation": "(1977) 3 SCC 247"}, {H: secret})
    _check_shape(r, limit=2, scope="key", text=KEY_TEXT)


def test_the_provisioning_limiter_keeps_its_plain_body_by_design(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Not in #403's scope (R1 worded the analyse/verify limiters only): the sign-in provisioning bucket answers the old body."""
    monkeypatch.setenv("PRAVRUDHI_ADMINS", ADMIN.id)
    c = _client(tmp_path, signed_in=True, limit=1000)
    last = None
    for n in range(12):
        last = c.post("/api/v1/orgs", json={"org_id": f"o{n}", "name": f"o{n}"})
        if last.status_code == 429:
            break
    assert last is not None and last.status_code == 429
    assert last.json() == {"detail": "rate limit exceeded"}  # unchanged on purpose
