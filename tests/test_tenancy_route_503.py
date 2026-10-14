# ruff: noqa: F811
"""Route-level guards for the hosted tenancy backend (R2's named gaps on #439): with the store down every keyed partner route
answers 503 `key service unavailable` (never 401, never served) and a key revoked through the admin route stops working at once
in this process despite the positive cache. The store is an in-memory double; the real SQL is covered by the env-gated test."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from pravrudhi.api import identity
from pravrudhi.api.partner import PartnerApiConfig, build_partner_router
from pravrudhi.application import tenancy
from tests.test_tenancy_supabase_backend import FakeStore, sb  # noqa: F401  (the fixture)

H = tenancy.API_KEY_HEADER
SECRET = "correct-horse-battery-staple-32-chars-long"
FACTS = {"facts": ["A person was seen near the shop."], "contract_ids": ["ipc405"]}
KEY_CALLS: list[tuple[str, str, dict[str, Any] | None]] = [
    ("POST", "/api/v1/verify-citations", {"citation": "(1977) 3 SCC 247"}),
    ("POST", "/api/v1/analyse-facts", FACTS),
    ("POST", "/api/v1/analyse-facts/jobs", FACTS),
    ("GET", "/api/v1/audit", None),
    ("POST", "/api/v1/intake", {"text": "1. A person was seen near the shop."}),
]


def _client(root: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv(tenancy.TENANCY_PROVISION_SECRET_ENV, SECRET)
    cfg = PartnerApiConfig(rate_limit_per_minute=1000, max_concurrent=100, trust_proxy_header=False)
    app = FastAPI()
    app.include_router(build_partner_router(root, config=cfg))
    app.dependency_overrides[identity.current_user] = lambda: None
    return TestClient(app)


def _call(c: TestClient, method: str, path: str, body: Any, headers: dict[str, str]) -> Any:
    return c.request(method, path, json=body, headers=headers) if body is not None else c.request(method, path, headers=headers)


@pytest.mark.parametrize(("method", "path", "body"), KEY_CALLS)
def test_with_the_store_down_a_keyed_call_is_503_never_401_and_never_served(
    sb: Any,
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    path: str,
    body: Any,
) -> None:
    store, root = sb
    c = _client(root, monkeypatch)
    tenancy.create_org(root, "acme", "Acme")
    k = tenancy.create_key(root, "acme")
    tenancy.reset_backend_for_tests()  # a cold cache: nothing can answer from memory
    store.down = True
    r = _call(c, method, path, body, {H: k.secret})
    assert r.status_code == 503 and r.json()["detail"] == "key service unavailable", (path, r.status_code, r.text[:80])
    unknown = _call(c, method, path, body, {H: "pnk_unknownkeyid0000_xxxxxxxx"})
    assert unknown.status_code == 503 and unknown.json()["detail"] == "key service unavailable"  # not 401: the store is down


def test_with_the_store_down_the_admin_routes_are_503_too(sb: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    store, root = sb
    c = _client(root, monkeypatch)
    store.down = True
    admin = {tenancy.TENANCY_PROVISION_HEADER: SECRET}
    for method, path, body in (
        ("POST", "/api/v1/orgs", {"org_id": "zz", "name": "Z"}),
        ("GET", "/api/v1/orgs/acme/keys", None),
        ("POST", "/api/v1/orgs/acme/keys", {}),
    ):
        r = _call(c, method, path, body, admin)
        assert r.status_code == 503 and r.json()["detail"] == "key service unavailable", (path, r.status_code)


def test_a_key_revoked_through_the_admin_route_stops_working_at_once_despite_the_cache(
    sb: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, root = sb
    c = _client(root, monkeypatch)
    tenancy.create_org(root, "acme", "Acme")
    k = tenancy.create_key(root, "acme")
    ok = c.post("/api/v1/verify-citations", json={"citation": "(1977) 3 SCC 247"}, headers={H: k.secret})
    assert (
        ok.status_code in (200, 503) and ok.json().get("detail") != "Invalid or revoked API key"
    )  # served; this warms the cache
    revoke = c.post(f"/api/v1/orgs/acme/keys/{k.record.key_id}/revoke", headers={tenancy.TENANCY_PROVISION_HEADER: SECRET})
    assert revoke.status_code == 200
    gone = c.post("/api/v1/verify-citations", json={"citation": "(1977) 3 SCC 247"}, headers={H: k.secret})
    assert gone.status_code == 401 and gone.json()["detail"] == "Invalid or revoked API key"  # not served from the 15 s cache
    again = c.get("/api/v1/audit", headers={H: k.secret})
    assert again.status_code == 401


def test_a_failing_store_never_turns_a_valid_key_into_a_401_even_after_a_warm_cache_expires(
    sb: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import time

    store, root = sb
    c = _client(root, monkeypatch)
    tenancy.create_org(root, "acme", "Acme")
    k = tenancy.create_key(root, "acme")
    body = {"citation": "(1977) 3 SCC 247"}
    c.post("/api/v1/verify-citations", json=body, headers={H: k.secret})  # warms the cache
    store.down = True
    store.calls.clear()
    inside = c.post("/api/v1/verify-citations", json=body, headers={H: k.secret})
    assert (
        inside.json().get("detail") != "key service unavailable" and "get_key" not in store.calls
    )  # inside the TTL: from memory
    real = time.monotonic
    monkeypatch.setattr(tenancy.time, "monotonic", lambda: real() + 16)  # past the 15 s TTL
    r = c.post("/api/v1/verify-citations", json=body, headers={H: k.secret})
    assert r.status_code == 503 and r.json()["detail"] == "key service unavailable"
