"""Failed-key attempts are budgeted per address BEFORE the password-hash check is spent, and charged only when the check fails; a
valid key is never charged. Also pins the bucket identities (`key:<id>` / `user:<id>`) and the injected-clock plumbing of the
per-IP and provisioning limiters. Constructed inputs only; no real key, address or secret."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from pravrudhi.api import identity
from pravrudhi.api import partner as P
from pravrudhi.api.identity import User
from pravrudhi.api.partner import (
    CLIENT_IP_HEADER,
    CLIENT_IP_SECRET_ENV,
    CLIENT_IP_SECRET_HEADER,
    PartnerApiConfig,
    RateLimiter,
    build_partner_router,
)
from pravrudhi.application import tenancy
from tests.test_api_partner import _agent, _proof_script
from tests.test_partner_key_metering import ADMIN, FakeClock, _req

H = tenancy.API_KEY_HEADER
SECRET = "s" * 32
ENDPOINTS = [
    pytest.param("/api/v1/analyse-facts", _req(), 200, id="analyse-facts"),
    pytest.param(
        "/api/v1/verify-citations", {"citation": "(1977) 3 SCC 247"}, 503, id="verify-citations"
    ),  # 503 = admitted, no index here
]
GARBAGE = "pvk_invented_notakey"


class Env:
    def __init__(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, limit: int = 6, secret: str | None = None, provision: int = 10
    ) -> None:
        monkeypatch.setenv("PRAVRUDHI_ADMINS", ADMIN.id)
        if secret is None:
            monkeypatch.delenv(CLIENT_IP_SECRET_ENV, raising=False)
        else:
            monkeypatch.setenv(CLIENT_IP_SECRET_ENV, secret)
        self.clock, self.user = FakeClock(), ADMIN
        cfg = PartnerApiConfig(
            rate_limit_per_minute=limit, max_concurrent=100, trust_proxy_header=False, provision_rate_limit_per_minute=provision
        )
        app = FastAPI()
        app.include_router(
            build_partner_router(
                tmp_path, agent_factory=lambda _r: _agent(tmp_path, _proof_script()), config=cfg, rate_clock=self.clock
            )
        )
        app.dependency_overrides[identity.current_user] = lambda: self.user
        self.c = TestClient(app)
        self.verifies: list[str] = []
        real = tenancy.verify_key

        def counting(root: Path, token: str) -> Any:
            self.verifies.append(token)
            return real(root, token)

        monkeypatch.setattr(tenancy, "verify_key", counting)

    def key(self, org: str, **kw: Any) -> tuple[str, str]:
        self.user = ADMIN
        self.c.post("/api/v1/orgs", json={"org_id": org, "name": org})
        made = self.c.post(f"/api/v1/orgs/{org}/keys", json=kw).json()
        self.user = None  # type: ignore[assignment]
        return str(made["secret"]), str(made["key_id"])

    def post(self, path: str, body: dict[str, Any], headers: dict[str, str] | None = None) -> int:
        return self.c.post(path, json=body, headers=headers or {}).status_code


def sign(ip: str) -> dict[str, str]:
    return {CLIENT_IP_HEADER: ip, CLIENT_IP_SECRET_HEADER: SECRET}


@pytest.mark.parametrize(("path", "body", "ok"), ENDPOINTS)
def test_a_flood_of_garbage_keys_is_refused_before_the_hash_check_after_the_budget(tmp_path, monkeypatch, path, body, ok):
    e = Env(tmp_path, monkeypatch)
    codes = [e.post(path, body, {H: GARBAGE}) for _ in range(20)]
    assert codes == [401] * 6 + [429] * 14
    assert len(e.verifies) == 6  # the other 14 never reached the hash check


@pytest.mark.parametrize(("path", "body", "ok"), ENDPOINTS)
def test_valid_keys_are_never_charged_to_the_failed_key_budget(tmp_path, monkeypatch, path, body, ok):
    e = Env(tmp_path, monkeypatch)
    secret, _ = e.key("acme")
    assert [e.post(path, body, {H: secret}) for _ in range(5)] == [ok] * 5  # five valid calls
    assert [e.post(path, body, {H: GARBAGE}) for _ in range(6)] == [401] * 6  # the budget is still whole
    assert e.post(path, body, {H: GARBAGE}) == 429


@pytest.mark.parametrize(("path", "body", "ok"), ENDPOINTS)
def test_a_request_without_a_key_header_neither_spends_nor_is_blocked_by_the_budget(tmp_path, monkeypatch, path, body, ok):
    e = Env(tmp_path, monkeypatch)
    assert [e.post(path, body, {H: GARBAGE}) for _ in range(7)] == [401] * 6 + [429]  # the address is out of failed-key budget
    e.user = ADMIN
    assert e.post(path, body) == ok  # no key header: not gated by the failed-key budget


@pytest.mark.parametrize(("path", "body", "ok"), ENDPOINTS)
def test_the_budget_is_per_proven_address(tmp_path, monkeypatch, path, body, ok):
    e = Env(tmp_path, monkeypatch, secret=SECRET)
    assert [e.post(path, body, {H: GARBAGE, **sign("203.0.113.1")}) for _ in range(7)] == [401] * 6 + [429]
    assert e.post(path, body, {H: GARBAGE, **sign("203.0.113.2")}) == 401  # another proven address: its own budget
    secret, _ = e.key("acme")
    assert e.post(path, body, {H: secret, **sign("203.0.113.2")}) == ok  # and a valid key from it is served
    assert e.post(path, body, {H: secret, **sign("203.0.113.1")}) == 429  # the exhausted address is refused even with a valid key


@pytest.mark.parametrize(("path", "body", "ok"), ENDPOINTS)
def test_without_a_signed_address_a_flood_from_the_shared_peer_also_refuses_keyed_callers_behind_it(
    tmp_path, monkeypatch, path, body, ok
):
    e = Env(tmp_path, monkeypatch)
    secret, _ = e.key("acme")
    assert e.post(path, body, {H: secret}) == ok
    for _ in range(6):
        e.post(path, body, {H: GARBAGE})
    assert e.post(path, body, {H: secret}) == 429  # documented consequence until the signed path is configured


@pytest.mark.parametrize(("path", "body", "ok"), ENDPOINTS)
def test_the_budget_comes_back_in_the_next_window(tmp_path, monkeypatch, path, body, ok):
    e = Env(tmp_path, monkeypatch)
    assert [e.post(path, body, {H: GARBAGE}) for _ in range(7)] == [401] * 6 + [429]
    e.clock.advance(60)
    assert e.post(path, body, {H: GARBAGE}) == 401


@pytest.mark.parametrize(("path", "body", "ok"), ENDPOINTS)
def test_a_revoked_key_is_a_failure_and_is_charged(tmp_path, monkeypatch, path, body, ok):
    e = Env(tmp_path, monkeypatch)
    secret, key_id = e.key("acme")
    e.user = ADMIN
    assert e.c.post(f"/api/v1/orgs/acme/keys/{key_id}/revoke").status_code == 200
    e.user = None  # type: ignore[assignment]
    assert [e.post(path, body, {H: secret}) for _ in range(7)] == [401] * 6 + [429]


# ---- RateLimiter.peek ----
def test_peek_reads_only_it_counts_nothing_and_creates_no_entry():
    t = [1_000_030.0]
    r = RateLimiter(2, now=lambda: t[0])
    for _ in range(100):
        assert r.peek("a") is True
    assert r._windows == {}
    assert r.allow("a") and r.allow("a") and not r.allow("a")
    assert r.peek("a") is False and r.peek("b") is True and "b" not in r._windows
    t[0] += 60
    assert r.peek("a") is True and r.allow("a")


# ---- the bucket identities (key:/user:) and the injected clock ----
def test_the_ip_bucket_of_a_keyed_caller_is_key_colon_id_and_of_a_signed_in_user_is_user_colon_id(tmp_path, monkeypatch):
    e = Env(tmp_path, monkeypatch, limit=1000)
    seen: list[str] = []
    real = RateLimiter.allow

    def spy(self: RateLimiter, key: str) -> bool:
        seen.append(key)
        return real(self, key)

    secret, key_id = e.key("acme")
    monkeypatch.setattr(RateLimiter, "allow", spy)
    e.post("/api/v1/analyse-facts", _req(), {H: secret})
    e.post("/api/v1/verify-citations", {"citation": "(1977) 3 SCC 247"}, {H: secret})
    e.user = User(id="u-7", email="u@example.test", role="authenticated")
    e.post("/api/v1/analyse-facts", _req())
    assert f"key:{key_id}" in seen and seen.count(f"key:{key_id}") == 2 and "user:u-7" in seen
    assert not any(k.startswith("key:") and k != f"key:{key_id}" for k in seen)


@pytest.mark.parametrize(("path", "body", "ok"), ENDPOINTS)
def test_the_per_ip_limiter_uses_the_injected_clock(tmp_path, monkeypatch, path, body, ok):
    e = Env(tmp_path, monkeypatch, limit=1)
    secret, _ = e.key("acme")
    assert e.post(path, body, {H: secret}) == ok
    assert e.post(path, body, {H: secret}) == 429
    e.clock.advance(60)
    assert e.post(path, body, {H: secret}) == ok


def test_the_provisioning_limiter_uses_the_injected_clock(tmp_path, monkeypatch):
    e = Env(tmp_path, monkeypatch, provision=2)
    e.user = ADMIN
    codes = [e.c.post("/api/v1/orgs", json={"org_id": f"o{n}", "name": "x"}).status_code for n in range(3)]
    assert codes[:2] == [200, 200] and codes[2] == 429
    e.clock.advance(60)
    assert e.c.post("/api/v1/orgs", json={"org_id": "o9", "name": "x"}).status_code == 200


def test_the_failed_key_budget_follows_the_configured_limit(tmp_path, monkeypatch):
    e = Env(tmp_path, monkeypatch, limit=2)
    assert [e.post("/api/v1/analyse-facts", _req(), {H: GARBAGE}) for _ in range(3)] == [401, 401, 429]
    assert P.RateLimiter is RateLimiter


@pytest.mark.parametrize(("path", "body", "ok"), ENDPOINTS)
def test_a_key_that_verifies_but_is_gone_from_the_org_is_a_failure_and_is_charged(tmp_path, monkeypatch, path, body, ok):
    e = Env(tmp_path, monkeypatch)
    secret, _ = e.key("acme")
    monkeypatch.setattr(tenancy, "key_record", lambda root, key_id, org_id=None: None)
    assert [e.post(path, body, {H: secret}) for _ in range(7)] == [401] * 6 + [429]
