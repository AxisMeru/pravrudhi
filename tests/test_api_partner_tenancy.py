"""HTTP wiring for tenancy on the partner router (L4, `docs/decisions/LEG-PLAN-2026-09-23.md`):
`POST /api/v1/orgs`, `POST/GET /api/v1/orgs/{org_id}/keys`, the revoke route, and `/usage`.

Admin gating uses the same `identity.current_user` dependency override pattern `test_nyaya.py` uses,
rather than real Supabase tokens; `application/tenancy.py`'s own tests cover key hashing and scoping without
any HTTP layer at all.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from pravrudhi.api import identity
from pravrudhi.api.identity import User
from pravrudhi.api.partner import PartnerApiConfig, build_partner_router
from pravrudhi.application import tenancy

_NO_LIMIT_CONFIG = PartnerApiConfig(rate_limit_per_minute=1000, max_concurrent=100, trust_proxy_header=False)


def _app(tmp_path: Path) -> FastAPI:
    app = FastAPI()
    app.include_router(build_partner_router(tmp_path, config=_NO_LIMIT_CONFIG))
    return app


def _client_as(app: FastAPI, user: User | None) -> TestClient:
    app.dependency_overrides[identity.current_user] = lambda: user
    return TestClient(app)


ADMIN_USER = User(id="op-1", email="op@example.com", role="authenticated")
PLAIN_USER = User(id="u-2", email="someone@example.com", role="authenticated")


@pytest.fixture(autouse=True)
def _admins_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PRAVRUDHI_ADMINS", ADMIN_USER.id)


class TestOrgCreationIsAdminOnly:
    def test_admin_can_create_an_org(self, tmp_path: Path) -> None:
        client = _client_as(_app(tmp_path), ADMIN_USER)
        r = client.post("/api/v1/orgs", json={"org_id": "acme", "name": "Acme Legal"})
        assert r.status_code == 200, r.text
        assert r.json()["id"] == "acme"

    def test_a_plain_user_is_refused(self, tmp_path: Path) -> None:
        client = _client_as(_app(tmp_path), PLAIN_USER)
        r = client.post("/api/v1/orgs", json={"org_id": "acme", "name": "Acme Legal"})
        assert r.status_code == 403

    def test_duplicate_org_is_409(self, tmp_path: Path) -> None:
        client = _client_as(_app(tmp_path), ADMIN_USER)
        client.post("/api/v1/orgs", json={"org_id": "acme", "name": "Acme"})
        r = client.post("/api/v1/orgs", json={"org_id": "acme", "name": "Acme Again"})
        assert r.status_code == 409


class TestKeyCreationIsAdminOnlyAndSecretIsShownOnce:
    def test_admin_creates_a_key_and_gets_the_secret(self, tmp_path: Path) -> None:
        client = _client_as(_app(tmp_path), ADMIN_USER)
        client.post("/api/v1/orgs", json={"org_id": "acme", "name": "Acme"})
        r = client.post("/api/v1/orgs/acme/keys", json={"label": "prod"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["secret"].startswith(f"{tenancy.KEY_PREFIX}_")
        assert "hash" not in body

    def test_a_plain_user_cannot_create_a_key(self, tmp_path: Path) -> None:
        app = _app(tmp_path)
        _client_as(app, ADMIN_USER).post("/api/v1/orgs", json={"org_id": "acme", "name": "Acme"})
        r = _client_as(app, PLAIN_USER).post("/api/v1/orgs/acme/keys", json={"label": "prod"})
        assert r.status_code == 403

    def test_listing_keys_never_returns_a_secret_or_hash(self, tmp_path: Path) -> None:
        client = _client_as(_app(tmp_path), ADMIN_USER)
        client.post("/api/v1/orgs", json={"org_id": "acme", "name": "Acme"})
        client.post("/api/v1/orgs/acme/keys", json={"label": "prod"})
        r = client.get("/api/v1/orgs/acme/keys")
        assert r.status_code == 200
        for row in r.json()["keys"]:
            assert "secret" not in row and "hash" not in row

    def test_key_for_unknown_org_is_404(self, tmp_path: Path) -> None:
        client = _client_as(_app(tmp_path), ADMIN_USER)
        r = client.post("/api/v1/orgs/ghost/keys", json={"label": "x"})
        assert r.status_code == 404


class TestRevocation:
    def test_revoked_key_no_longer_authenticates(self, tmp_path: Path) -> None:
        admin = _client_as(_app(tmp_path), ADMIN_USER)
        admin.post("/api/v1/orgs", json={"org_id": "acme", "name": "Acme"})
        created = admin.post("/api/v1/orgs/acme/keys", json={"label": "prod"}).json()
        secret = created["secret"]
        key_id = created["key_id"]

        r = admin.post(f"/api/v1/orgs/acme/keys/{key_id}/revoke")
        assert r.status_code == 200
        assert r.json()["revoked"] is True

        usage = admin.get("/api/v1/orgs/acme/usage", headers={tenancy.API_KEY_HEADER: secret})
        assert usage.status_code == 401

    def test_revoking_a_key_under_the_wrong_org_is_404(self, tmp_path: Path) -> None:
        admin = _client_as(_app(tmp_path), ADMIN_USER)
        admin.post("/api/v1/orgs", json={"org_id": "acme", "name": "Acme"})
        admin.post("/api/v1/orgs", json={"org_id": "globex", "name": "Globex"})
        created = admin.post("/api/v1/orgs/acme/keys", json={"label": "prod"}).json()
        r = admin.post(f"/api/v1/orgs/globex/keys/{created['key_id']}/revoke")
        assert r.status_code == 404


class TestUsageCrossOrgAndRateLimit:
    def _org_with_key(self, client: TestClient, org_id: str, *, rate_limit_per_minute: int = 60) -> str:
        client.post("/api/v1/orgs", json={"org_id": org_id, "name": org_id})
        created = client.post(
            f"/api/v1/orgs/{org_id}/keys", json={"label": "prod", "rate_limit_per_minute": rate_limit_per_minute}
        ).json()
        return str(created["secret"])

    def test_a_key_can_read_its_own_orgs_usage(self, tmp_path: Path) -> None:
        admin = _client_as(_app(tmp_path), ADMIN_USER)
        secret = self._org_with_key(admin, "acme")
        r = admin.get("/api/v1/orgs/acme/usage", headers={tenancy.API_KEY_HEADER: secret})
        assert r.status_code == 200
        assert r.json()["org_id"] == "acme"

    def test_a_key_cannot_read_another_orgs_usage(self, tmp_path: Path) -> None:
        app = _app(tmp_path)
        admin = _client_as(app, ADMIN_USER)
        self._org_with_key(admin, "acme")
        globex_secret = self._org_with_key(admin, "globex")
        # A non-admin caller presenting globex's key against acme's usage route: the key is real and
        # unrevoked, it just does not belong to the org in the URL -- that mismatch is what must 403.
        plain = _client_as(app, PLAIN_USER)
        r = plain.get("/api/v1/orgs/acme/usage", headers={tenancy.API_KEY_HEADER: globex_secret})
        assert r.status_code == 403

    def test_a_plain_user_with_no_key_cannot_read_usage(self, tmp_path: Path) -> None:
        app = _app(tmp_path)
        self._org_with_key(_client_as(app, ADMIN_USER), "acme")
        r = _client_as(app, PLAIN_USER).get("/api/v1/orgs/acme/usage")
        assert r.status_code == 403

    def test_429_after_the_keys_own_rate_limit(self, tmp_path: Path) -> None:
        admin = _client_as(_app(tmp_path), ADMIN_USER)
        secret = self._org_with_key(admin, "acme", rate_limit_per_minute=1)
        first = admin.get("/api/v1/orgs/acme/usage", headers={tenancy.API_KEY_HEADER: secret})
        assert first.status_code == 200
        second = admin.get("/api/v1/orgs/acme/usage", headers={tenancy.API_KEY_HEADER: secret})
        assert second.status_code == 429
        assert "Retry-After" in second.headers

    def test_an_invalid_key_header_is_401_not_403(self, tmp_path: Path) -> None:
        admin = _client_as(_app(tmp_path), ADMIN_USER)
        self._org_with_key(admin, "acme")
        r = admin.get("/api/v1/orgs/acme/usage", headers={tenancy.API_KEY_HEADER: "garbage"})
        assert r.status_code == 401


def _real_client(tmp_path: Path) -> TestClient:
    """A client with NO `identity.current_user` dependency override at all -- exercises the real,
    unoverridden identity resolution every other test in this file replaces with `_client_as`. Reviewer 1's
    blocker was demonstrated exactly this way: on a self-hosted default config (no `PRAVRUDHI_AUTH`, no
    `PRAVRUDHI_ADMINS`, no `VERCEL`/`RENDER`), `roles.role_of(None)` resolves an anonymous caller to `ADMIN`
    by construction, so the first version of these routes (gated with `roles.require_admin`) gave an
    anonymous `POST /api/v1/orgs` a 200 and a live plaintext key."""
    return TestClient(_app(tmp_path))


def _clear_auth_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """The exact "nothing configured" starting point reviewer 1's repro used -- overrides this file's own
    `_admins_env` autouse fixture, which every other test in the file relies on to make the dependency-
    overridden ADMIN_USER an admin."""
    monkeypatch.delenv("PRAVRUDHI_AUTH", raising=False)
    monkeypatch.delenv("PRAVRUDHI_ADMINS", raising=False)
    monkeypatch.delenv(tenancy.TENANCY_PROVISION_SECRET_ENV, raising=False)
    monkeypatch.delenv("VERCEL", raising=False)
    monkeypatch.delenv("RENDER", raising=False)


class TestProvisioningFailsClosedOnRealIdentityResolution:
    """Reviewer 1, fix-before-merge: the four provisioning routes must never authorise off
    `roles.role_of`/`roles.is_admin`'s auth-disabled-means-operator fallback. Every test here uses
    `_real_client` -- no dependency override, so `identity.current_user` runs for real."""

    def test_with_nothing_configured_all_four_routes_are_403(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _clear_auth_env(monkeypatch)
        client = _real_client(tmp_path)

        r = client.post("/api/v1/orgs", json={"org_id": "acme", "name": "Acme"})
        assert r.status_code == 403, r.text

        # Seed an org directly through the module (bypassing HTTP, which is exactly refused) so the other
        # three routes have something real to be refused against rather than hiding a 403 behind a 404.
        tenancy.create_org(tmp_path, "acme", "Acme")
        assert client.post("/api/v1/orgs/acme/keys", json={"label": "x"}).status_code == 403
        assert client.get("/api/v1/orgs/acme/keys").status_code == 403
        assert client.post("/api/v1/orgs/acme/keys/whatever-key-id/revoke").status_code == 403

    def test_an_anonymous_caller_is_refused_even_when_admins_is_set(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _clear_auth_env(monkeypatch)
        monkeypatch.setenv("PRAVRUDHI_ADMINS", "someone@example.com")
        client = _real_client(tmp_path)
        r = client.post("/api/v1/orgs", json={"org_id": "acme", "name": "Acme"})
        assert r.status_code == 403, r.text

    def test_the_provisioning_secret_unlocks_it(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _clear_auth_env(monkeypatch)
        monkeypatch.setenv(tenancy.TENANCY_PROVISION_SECRET_ENV, "correct-horse-battery-staple")
        client = _real_client(tmp_path)
        r = client.post(
            "/api/v1/orgs",
            json={"org_id": "acme", "name": "Acme"},
            headers={tenancy.TENANCY_PROVISION_HEADER: "correct-horse-battery-staple"},
        )
        assert r.status_code == 200, r.text

    def test_a_wrong_secret_is_403(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _clear_auth_env(monkeypatch)
        monkeypatch.setenv(tenancy.TENANCY_PROVISION_SECRET_ENV, "correct-horse-battery-staple")
        client = _real_client(tmp_path)
        r = client.post(
            "/api/v1/orgs",
            json={"org_id": "acme", "name": "Acme"},
            headers={tenancy.TENANCY_PROVISION_HEADER: "wrong-secret"},
        )
        assert r.status_code == 403

    def test_usage_admin_bypass_also_fails_closed(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """The same class of hole existed in `/usage`'s admin bypass (not explicitly named in reviewer 1's
        report, fixed alongside it): an anonymous caller must not read another org's usage counters just
        because auth happens to be disabled."""
        _clear_auth_env(monkeypatch)
        tenancy.create_org(tmp_path, "acme", "Acme")
        r = _real_client(tmp_path).get("/api/v1/orgs/acme/usage")
        assert r.status_code == 403


class TestExistingAnalyseFactsPathIsUntouched:
    """The plan's requirement that adding tenancy leaves analyse-facts's existing Supabase and demo-anon
    behavior byte-identical -- covered in full by `test_api_partner.py`; this is a narrow smoke check that
    the route still exists, unauthenticated, on an app that also carries the new tenancy routes."""

    def test_analyse_facts_route_is_still_registered(self, tmp_path: Path) -> None:
        client = _client_as(_app(tmp_path), None)
        # No body at all is a 422 (request validation), never a 404 -- 404 would mean the route vanished.
        r = client.post("/api/v1/analyse-facts", json={})
        assert r.status_code != 404


class TestOpenApiListsTheNewRoutes:
    def test_openapi_schema_includes_tenancy_routes(self, tmp_path: Path) -> None:
        client = _client_as(_app(tmp_path), ADMIN_USER)
        schema = client.get("/openapi.json").json()
        paths = schema["paths"]
        assert "/api/v1/orgs" in paths
        assert "/api/v1/orgs/{org_id}/keys" in paths
        assert "/api/v1/orgs/{org_id}/keys/{key_id}/revoke" in paths
        assert "/api/v1/orgs/{org_id}/usage" in paths
        assert "/api/v1/analyse-facts" in paths
