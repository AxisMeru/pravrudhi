"""#922: in `required` mode the whole-surface gate lets a key-only caller reach the partner routes (each validates the key itself) and the
tenancy secret reach the org/key admin routes, and nothing else. Both arms PER ROUTE. Constructed credentials only; no network."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from pravrudhi.api import identity
from pravrudhi.api.server import create_app
from pravrudhi.application import tenancy

REPO = Path(__file__).resolve().parent.parent
SECRET = "p" * 40
BAD = "pnk_aaaaaaaaaaaaaaaa_notthesecret"

# (method, path, json body). Every path here is in PARTNER_KEY_PATHS or matches its job pattern.
FACTS = {"facts": ["A person was seen near the shop."], "contract_ids": ["ipc405"]}
KEY_ROUTES: list[tuple[str, str, dict[str, Any] | None]] = [
    ("POST", "/api/v1/analyse-facts", FACTS),
    ("POST", "/api/v1/analyse-facts/jobs", FACTS),
    ("GET", "/api/v1/analyse-facts/jobs/abc", None),
    ("POST", "/api/v1/analyse-facts/assist", {**FACTS, "consent": True}),
    ("POST", "/api/v1/verify-citations", {"citation": "(1977) 3 SCC 247"}),
    ("GET", "/api/v1/audit", None),
    ("POST", "/api/v1/intake", {"text": "1. A person was seen near the shop."}),
    ("POST", "/api/v1/intake/assist", {"text": "1. A person was seen near the shop.", "consent": True}),
]
ORG_ROUTES: list[tuple[str, str, dict[str, Any] | None]] = [
    ("POST", "/api/v1/orgs", {"org_id": "zz", "name": "Z"}),
    ("GET", "/api/v1/orgs/acme/keys", None),
    ("POST", "/api/v1/orgs/acme/keys", {}),
    ("POST", "/api/v1/orgs/acme/keys/kid/revoke", None),
    ("GET", "/api/v1/orgs/acme/usage", None),
    ("GET", "/api/v1/orgs/acme/usage/summary", None),
]


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[TestClient, str]:
    monkeypatch.setenv("PRAVRUDHI_AUTH", "required")
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_JWT_SECRET", "s" * 40)
    monkeypatch.setenv("PRAVRUDHI_DISABLE_LOCAL_GUARD", "1")  # the hosted shape; the local token is not what is under test
    monkeypatch.setenv(tenancy.TENANCY_PROVISION_SECRET_ENV, SECRET)
    root = tmp_path / "root"
    shutil.copytree(REPO / "configs", root / "configs")
    tenancy.create_org(root, "acme", "Acme")
    key = tenancy.create_key(root, "acme", label="t").secret
    return TestClient(create_app(root), headers={"host": "127.0.0.1:8008"}), key


def _call(c: TestClient, method: str, path: str, body: dict[str, Any] | None, headers: dict[str, str]) -> Any:
    return c.request(method, path, json=body, headers=headers) if body is not None else c.request(method, path, headers=headers)


def test_the_gate_header_names_equal_tenancys() -> None:
    assert identity._API_KEY_HEADER == tenancy.API_KEY_HEADER
    assert identity._TENANCY_SECRET_HEADER == tenancy.TENANCY_PROVISION_HEADER


@pytest.mark.parametrize(("method", "path", "body"), KEY_ROUTES)
def test_a_partner_route_without_any_credential_is_refused_by_the_gate(env: Any, method: str, path: str, body: Any) -> None:
    c, _key = env
    r = _call(c, method, path, body, {})
    assert r.status_code == 401 and r.json()["detail"] == "Missing bearer token"


@pytest.mark.parametrize(("method", "path", "body"), KEY_ROUTES)
def test_a_partner_route_with_a_bad_key_is_401_never_anonymous(env: Any, method: str, path: str, body: Any) -> None:
    c, _key = env
    r = _call(c, method, path, body, {tenancy.API_KEY_HEADER: BAD})
    assert r.status_code == 401 and r.json()["detail"] == "Invalid or revoked API key", (path, r.text[:120])


@pytest.mark.parametrize(("method", "path", "body"), KEY_ROUTES)
def test_a_partner_route_with_a_valid_key_gets_past_the_gate(env: Any, method: str, path: str, body: Any) -> None:
    c, key = env
    r = _call(c, method, path, body, {tenancy.API_KEY_HEADER: key})
    assert r.json().get("detail") not in ("Missing bearer token", "Invalid or revoked API key"), (path, r.status_code, r.text[:120])


@pytest.mark.parametrize(("method", "path", "body"), KEY_ROUTES)
def test_a_key_does_not_excuse_a_bad_identity_token(env: Any, method: str, path: str, body: Any) -> None:
    c, key = env
    r = _call(c, method, path, body, {tenancy.API_KEY_HEADER: key, "Authorization": "Bearer not-a-token"})
    assert r.status_code == 401 and "Invalid token" in r.json()["detail"]


@pytest.mark.parametrize(
    ("method", "path"),
    [("GET", "/api/v1/status"), ("GET", "/api/nyaya/corpus"), ("GET", "/api/state"), ("POST", "/api/nyaya/ask"),
     ("GET", "/api/v1/analyse-facts/jobs"), ("GET", "/api/v1/audit/extra")],
)
def test_a_valid_key_does_not_open_any_other_path(env: Any, method: str, path: str) -> None:
    c, key = env
    r = _call(c, method, path, {} if method == "POST" else None, {tenancy.API_KEY_HEADER: key})
    assert r.status_code in (401, 404, 405) and r.json().get("detail") != "Invalid or revoked API key"
    if r.status_code == 401:
        assert r.json()["detail"] == "Missing bearer token"


@pytest.mark.parametrize(("method", "path", "body"), ORG_ROUTES)
def test_org_admin_routes_pass_the_gate_only_with_the_tenancy_secret(env: Any, method: str, path: str, body: Any) -> None:
    c, key = env
    assert _call(c, method, path, body, {}).json()["detail"] == "Missing bearer token"
    with_key = _call(c, method, path, body, {tenancy.API_KEY_HEADER: key})
    assert with_key.status_code == 401 and with_key.json()["detail"] == "Missing bearer token"  # a key alone does not open /orgs
    ok = _call(c, method, path, body, {tenancy.TENANCY_PROVISION_HEADER: SECRET})
    assert ok.json().get("detail") != "Missing bearer token" and ok.status_code != 401, (path, ok.status_code, ok.text[:120])
    wrong = _call(c, method, path, body, {tenancy.TENANCY_PROVISION_HEADER: "w" * 40})
    # past the gate, refused by the route itself (it validates the secret): 403, not the gate's 401
    assert wrong.status_code == 403 and "provisioning secret" in wrong.json()["detail"], (path, wrong.status_code)


def test_the_tenancy_secret_does_not_open_the_partner_key_routes(env: Any) -> None:
    c, _key = env
    r = c.post("/api/v1/verify-citations", json={}, headers={tenancy.TENANCY_PROVISION_HEADER: SECRET})
    assert r.status_code == 401 and r.json()["detail"] == "Missing bearer token"


def test_every_route_under_api_v1_is_either_listed_or_refused_with_a_key(env: Any) -> None:
    """A route added later is refused for a key-only caller until it is added on purpose: the allowlist and the app's routes agree."""
    from fastapi.routing import APIRoute

    c, key = env

    def walk(routes: Any) -> Any:
        for r in routes:
            if isinstance(r, APIRoute):
                yield r
            elif getattr(r, "original_router", None) is not None:
                yield from walk(r.original_router.routes)

    listed = {p for _m, p, _b in KEY_ROUTES} | {p for _m, p, _b in ORG_ROUTES}
    for r in walk(c.app.routes):
        if not r.path.startswith("/api/v1/"):
            continue
        concrete = r.path.replace("{job_id}", "abc").replace("{org_id}", "acme").replace("{key_id}", "kid")
        in_gate = concrete in identity.PARTNER_KEY_PATHS or identity._PARTNER_KEY_PATH_RE.match(concrete) or identity._ORGS_PATH_RE.match(concrete)
        assert bool(in_gate) == (concrete in listed or r.path == "/api/v1/status" and False), f"{r.path}: gate list and tests disagree"
