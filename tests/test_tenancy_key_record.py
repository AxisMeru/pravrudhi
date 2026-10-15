"""A keyed request reads ONE key row (the one verify_key just read), not the whole org's key list."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from pravrudhi.application import tenancy
from tests.test_tenancy_supabase_backend import FakeStore, sb  # noqa: F401  (the fixture)


def test_key_record_returns_the_row_and_none_for_unknown_or_another_org(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(tenancy.BACKEND_ENV, raising=False)
    tenancy.reset_backend_for_tests()
    tenancy.create_org(tmp_path, "acme", "Acme")
    tenancy.create_org(tmp_path, "other", "Other")
    k = tenancy.create_key(tmp_path, "acme", rate_limit_per_minute=7)
    rec = tenancy.key_record(tmp_path, k.record.key_id, org_id="acme")
    assert rec is not None and rec.rate_limit_per_minute == 7 and rec.org_id == "acme" and rec.revoked is False
    assert tenancy.key_record(tmp_path, k.record.key_id, org_id="acme") is not None
    assert tenancy.key_record(tmp_path, k.record.key_id, org_id="other") is None  # another org's key id is not found
    assert tenancy.key_record(tmp_path, "nosuchkey0000000", org_id="acme") is None
    with pytest.raises(TypeError):
        tenancy.key_record(tmp_path, k.record.key_id)  # type: ignore[call-arg]  # an unscoped lookup by key id is not offered
    tenancy.revoke_key(tmp_path, k.record.key_id)
    revoked = tenancy.key_record(tmp_path, k.record.key_id, org_id="acme")
    assert revoked is not None and revoked.revoked is True


def test_in_the_hosted_backend_key_record_uses_the_cached_row_and_never_lists_the_org(sb: Any) -> None:  # noqa: F811
    store, root = sb
    tenancy.create_org(root, "acme", "Acme")
    k = tenancy.create_key(root, "acme", rate_limit_per_minute=9)
    tenancy.verify_key(root, k.secret)  # warms the cache: one get_key
    store.calls.clear()
    rec = tenancy.key_record(root, k.record.key_id, org_id="acme")
    assert rec is not None and rec.rate_limit_per_minute == 9
    assert store.calls == []  # served from the cache: no database call at all, and never keys_for_org


def test_a_keyed_verify_call_makes_no_keys_for_org_call_in_the_hosted_backend(sb: Any, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from pravrudhi.api import identity
    from pravrudhi.api.partner import PartnerApiConfig, build_partner_router

    store, root = sb
    tenancy.create_org(root, "acme", "Acme")
    k = tenancy.create_key(root, "acme")
    cfg = PartnerApiConfig(rate_limit_per_minute=1000, max_concurrent=100, trust_proxy_header=False)
    app = FastAPI()
    app.include_router(build_partner_router(root, config=cfg))
    app.dependency_overrides[identity.current_user] = lambda: None
    store.calls.clear()
    r = TestClient(app).post(
        "/api/v1/verify-citations", json={"citation": "(1977) 3 SCC 247"}, headers={tenancy.API_KEY_HEADER: k.secret}
    )
    assert r.status_code in (200, 503)  # no index here: the route's own answer; the point is the admission path
    assert "keys_for_org" not in store.calls and store.calls.count("get_key") <= 1


def test_a_revoked_key_is_401_on_the_keyed_route_in_the_hosted_backend(sb: Any) -> None:  # noqa: F811
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from pravrudhi.api import identity
    from pravrudhi.api.partner import PartnerApiConfig, build_partner_router

    store, root = sb
    tenancy.create_org(root, "acme", "Acme")
    k = tenancy.create_key(root, "acme")
    cfg = PartnerApiConfig(rate_limit_per_minute=1000, max_concurrent=100, trust_proxy_header=False)
    app = FastAPI()
    app.include_router(build_partner_router(root, config=cfg))
    app.dependency_overrides[identity.current_user] = lambda: None
    c = TestClient(app)
    body = {"citation": "(1977) 3 SCC 247"}
    assert c.post("/api/v1/verify-citations", json=body, headers={tenancy.API_KEY_HEADER: k.secret}).status_code in (200, 503)
    tenancy.revoke_key(root, k.record.key_id)
    r = c.post("/api/v1/verify-citations", json=body, headers={tenancy.API_KEY_HEADER: k.secret})
    assert r.status_code == 401 and r.json()["detail"] == "Invalid or revoked API key"
