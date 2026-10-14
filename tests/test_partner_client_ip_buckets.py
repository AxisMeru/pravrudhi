"""Per-IP rate-limit buckets behind the gateway: an address header counts only when signed with the shared secret; otherwise an
identified caller (API key, signed-in user) is bucketed by that identity and an anonymous caller by the socket peer. Constructed
inputs only; no real address, key or secret."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request

from pravrudhi.api import identity
from pravrudhi.api.identity import User
from pravrudhi.api.partner import (
    CLIENT_IP_HEADER,
    CLIENT_IP_SECRET_ENV,
    CLIENT_IP_SECRET_HEADER,
    PartnerApiConfig,
    _client_ip,
    build_partner_router,
    load_partner_api_config,
)
from pravrudhi.application import tenancy
from tests.test_api_partner import _agent, _proof_script
from tests.test_partner_key_metering import ADMIN, FakeClock, _key, _req

REPO = Path(__file__).resolve().parent.parent
H = tenancy.API_KEY_HEADER
SECRET = "s" * 32
ENDPOINTS = [
    pytest.param("/api/v1/analyse-facts", _req(), 200, id="analyse-facts"),
    pytest.param(
        "/api/v1/verify-citations", {"citation": "(1977) 3 SCC 247"}, 503, id="verify-citations"
    ),  # 503 = admitted, no index here
]


class Who:
    """The signed-in identity the identity dependency returns (swapped between requests)."""

    def __init__(self, user: User | None) -> None:
        self.user = user


def make(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, secret: str | None = None, who: Who | None = None
) -> tuple[TestClient, Who]:
    monkeypatch.setenv("PRAVRUDHI_ADMINS", ADMIN.id)
    if secret is None:
        monkeypatch.delenv(CLIENT_IP_SECRET_ENV, raising=False)
    else:
        monkeypatch.setenv(CLIENT_IP_SECRET_ENV, secret)
    who = who or Who(ADMIN)
    cfg = PartnerApiConfig(rate_limit_per_minute=1, max_concurrent=100, trust_proxy_header=False)
    app = FastAPI()
    app.include_router(
        build_partner_router(
            tmp_path, agent_factory=lambda _r: _agent(tmp_path, _proof_script()), config=cfg, rate_clock=FakeClock()
        )
    )
    app.dependency_overrides[identity.current_user] = lambda: who.user
    return TestClient(app), who


def post(c: TestClient, path: str, body: dict[str, Any], headers: dict[str, str] | None = None) -> int:
    return c.post(path, json=body, headers=headers or {}).status_code


def keys(c: TestClient, who: Who, *orgs: str) -> list[str]:
    who.user = ADMIN  # org and key provisioning needs the admin identity
    return [_key(c, o) for o in orgs]


# ---- arm 1: no proven address: identity decides the bucket ----
@pytest.mark.parametrize(("path", "body", "ok"), ENDPOINTS)
def test_two_keyed_callers_behind_one_proxy_peer_do_not_share_a_bucket(tmp_path, monkeypatch, path, body, ok):
    c, who = make(tmp_path, monkeypatch)
    a, b = keys(c, who, "acme", "beta")
    who.user = None
    assert post(c, path, body, {H: a}) == ok
    assert post(c, path, body, {H: b}) == ok  # a different key: its own budget of 1
    assert post(c, path, body, {H: a}) == 429 and post(c, path, body, {H: b}) == 429


@pytest.mark.parametrize(("path", "body", "ok"), ENDPOINTS)
@pytest.mark.parametrize(
    "forged",
    [
        {CLIENT_IP_HEADER: "203.0.113.9"},
        {CLIENT_IP_HEADER: "203.0.113.9", CLIENT_IP_SECRET_HEADER: "wrong" * 8},
        {CLIENT_IP_SECRET_HEADER: "s" * 32},
    ],
    ids=["header-only", "wrong-secret", "secret-only"],
)
def test_a_forged_address_header_is_ignored_the_keyed_caller_stays_in_its_own_bucket(
    tmp_path, monkeypatch, path, body, ok, forged
):
    c, who = make(tmp_path, monkeypatch, secret=SECRET)
    (a,) = keys(c, who, "acme")
    who.user = None
    rotating = [{**forged, CLIENT_IP_HEADER: f"203.0.113.{n}"} if CLIENT_IP_HEADER in forged else dict(forged) for n in (1, 2, 3)]
    codes = [post(c, path, body, {H: a, **h}) for h in rotating]
    assert codes == [ok, 429, 429]  # rotating the forged value changes nothing: one key, one bucket


@pytest.mark.parametrize(("path", "body", "ok"), ENDPOINTS)
@pytest.mark.parametrize("secret", [None, "too-short", SECRET])
def test_anonymous_callers_still_share_the_socket_peer_bucket(tmp_path, monkeypatch, path, body, ok, secret):
    c, who = make(tmp_path, monkeypatch, secret=secret, who=Who(None))
    first = post(c, path, body, {CLIENT_IP_HEADER: "203.0.113.1"})
    second = post(c, path, body, {CLIENT_IP_HEADER: "203.0.113.2"})  # a different, unsigned address: not believed
    assert (first, second) == (ok, 429)


@pytest.mark.parametrize(("path", "body", "ok"), ENDPOINTS)
def test_a_secret_that_is_too_short_is_ignored_fail_closed_and_warns_once_without_the_value(
    tmp_path, monkeypatch, caplog, path, body, ok
):
    import pravrudhi.api.partner as P

    monkeypatch.setattr(P, "_client_ip_secret_warned", False)
    c, who = make(tmp_path, monkeypatch, secret="too-short-secret-value", who=Who(None))
    with caplog.at_level(logging.WARNING):
        r = [
            post(c, path, body, {CLIENT_IP_HEADER: f"203.0.113.{n}", CLIENT_IP_SECRET_HEADER: "too-short-secret-value"})
            for n in (1, 2, 3)
        ]
    assert r == [ok, 429, 429]
    assert "too-short-secret-value" not in caplog.text
    assert sum("shorter than" in rec.getMessage() for rec in caplog.records) == 1  # exactly one warning per process


# ---- arm 2: a proven (signed) address wins ----
@pytest.mark.parametrize(("path", "body", "ok"), ENDPOINTS)
def test_a_signed_address_is_the_bucket_even_for_a_keyed_caller(tmp_path, monkeypatch, path, body, ok):
    c, who = make(tmp_path, monkeypatch, secret=SECRET)
    a, b = keys(c, who, "acme", "beta")
    who.user = None
    sign = lambda ip: {CLIENT_IP_HEADER: ip, CLIENT_IP_SECRET_HEADER: SECRET}  # noqa: E731
    assert post(c, path, body, {H: a, **sign("203.0.113.1")}) == ok
    assert post(c, path, body, {H: a, **sign("203.0.113.2")}) == ok  # same key, another proven address: its own bucket
    assert post(c, path, body, {H: b, **sign("203.0.113.1")}) == 429  # another key from the SAME proven address: shared
    assert post(c, path, body, {H: a, **sign("203.0.113.1")}) == 429


@pytest.mark.parametrize(("path", "body", "ok"), ENDPOINTS)
def test_the_per_key_limiter_is_untouched_by_the_bucket_change(tmp_path, monkeypatch, path, body, ok):
    c, who = make(tmp_path, monkeypatch, secret=SECRET)
    c.post("/api/v1/orgs", json={"org_id": "acme", "name": "acme"})
    secret = str(c.post("/api/v1/orgs/acme/keys", json={"rate_limit_per_minute": 1}).json()["secret"])
    who.user = None
    sign = lambda ip: {CLIENT_IP_HEADER: ip, CLIENT_IP_SECRET_HEADER: SECRET}  # noqa: E731
    assert post(c, path, body, {H: secret, **sign("203.0.113.1")}) == ok
    assert (
        post(c, path, body, {H: secret, **sign("203.0.113.2")}) == 429
    )  # a fresh address, but the KEY's own limit of 1 is spent


# ---- signed-in users (analyse-facts): separate buckets ----
def test_signed_in_users_behind_one_peer_get_separate_buckets_and_one_user_gets_one(tmp_path, monkeypatch):
    c, who = make(tmp_path, monkeypatch, who=Who(User(id="u-1", email="a@example.test", role="authenticated")))
    path, body = "/api/v1/analyse-facts", _req()
    assert post(c, path, body) == 200
    who.user = User(id="u-2", email="b@example.test", role="authenticated")
    assert post(c, path, body) == 200
    assert post(c, path, body) == 429  # u-2 again
    who.user = User(id="u-1", email="a@example.test", role="authenticated")
    assert post(c, path, body) == 429  # u-1 again


# ---- _client_ip precedence, unit ----
PROXIES = ("203.0.113.250",)


def req(headers: dict[str, str], peer: str = "203.0.113.250") -> Request:
    return Request(
        {"type": "http", "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()], "client": (peer, 5000)}
    )


def test_precedence_signed_address_then_trusted_forwarded_then_caller_key_then_peer(monkeypatch):
    monkeypatch.setenv(CLIENT_IP_SECRET_ENV, SECRET)
    signed = {CLIENT_IP_HEADER: "198.51.100.7", CLIENT_IP_SECRET_HEADER: SECRET, "x-forwarded-for": "192.0.2.5"}
    assert _client_ip(req(signed), trust_proxy_header=True, trusted_proxies=PROXIES, caller_key="key:k1") == "198.51.100.7"
    xff = {"x-forwarded-for": "192.0.2.5"}
    assert _client_ip(req(xff), trust_proxy_header=True, trusted_proxies=PROXIES, caller_key="key:k1") == "192.0.2.5"
    assert _client_ip(req(xff), trust_proxy_header=False, trusted_proxies=(), caller_key="key:k1") == "key:k1"
    assert (
        _client_ip(req(xff, peer="203.0.113.99"), trust_proxy_header=True, trusted_proxies=PROXIES, caller_key="key:k1")
        == "key:k1"
    )
    assert _client_ip(req(xff), trust_proxy_header=False, trusted_proxies=(), caller_key=None) == "203.0.113.250"
    assert _client_ip(req(xff), trust_proxy_header=False, trusted_proxies=()) == "203.0.113.250"


def test_caller_keys_cannot_collide_with_an_address(monkeypatch):
    monkeypatch.delenv(CLIENT_IP_SECRET_ENV, raising=False)
    assert ":" in _client_ip(req({}), trust_proxy_header=False, trusted_proxies=(), caller_key="key:k1")
    assert (
        _client_ip(req({}, peer="key:k1"), trust_proxy_header=False, trusted_proxies=(), caller_key=None) == "key:k1"
    )  # a peer string is the peer's own


# ---- configuration pins ----
def test_the_shipped_config_does_not_trust_a_forwarded_header():
    cfg = load_partner_api_config(REPO)
    assert cfg.trust_proxy_header is False and tuple(cfg.trusted_proxies) == ()
    assert "trust_proxy_header: false" in (REPO / "configs" / "partner_api.yaml").read_text()
