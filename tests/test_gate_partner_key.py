"""#922: in `required` mode the whole-surface gate lets a key-only caller reach the partner routes (each validates the key itself)
and the tenancy secret reach the org/key admin routes, and nothing else. Both arms PER ROUTE. Constructed credentials only."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from pravrudhi.api import identity
from pravrudhi.api.server import create_app
from pravrudhi.application import tenancy
from pravrudhi.application.a0_assist import runtime as a0rt

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


@pytest.fixture(autouse=True)
def _fresh_assist_runtime():
    """The assist cases reach the route's 404 before the key check when a cached runtime is dark or absent, so the result
    depended on what an earlier test module left in the process-wide runtime. Build it from this test's own config."""
    a0rt.reset()
    yield
    a0rt.reset()


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
    assert r.json().get("detail") not in ("Missing bearer token", "Invalid or revoked API key"), (
        path,
        r.status_code,
        r.text[:120],
    )


@pytest.mark.parametrize(("method", "path", "body"), KEY_ROUTES)
def test_a_key_does_not_excuse_a_bad_identity_token(env: Any, method: str, path: str, body: Any) -> None:
    c, key = env
    r = _call(c, method, path, body, {tenancy.API_KEY_HEADER: key, "Authorization": "Bearer not-a-token"})
    assert r.status_code == 401 and "Invalid token" in r.json()["detail"]


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/v1/status"),
        ("GET", "/api/nyaya/corpus"),
        ("GET", "/api/state"),
        ("POST", "/api/nyaya/ask"),
        ("GET", "/api/v1/analyse-facts/jobs"),
        ("GET", "/api/v1/audit/extra"),
    ],
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
    usage = path.endswith("/usage")
    with_key = _call(c, method, path, body, {tenancy.API_KEY_HEADER: key})
    if usage:  # an org's own key may read usage: the gate passes it and the route decides
        assert with_key.json().get("detail") != "Missing bearer token"
    else:  # a key alone does not open the admin routes
        assert with_key.status_code == 401 and with_key.json()["detail"] == "Missing bearer token"
    ok = _call(c, method, path, body, {tenancy.TENANCY_PROVISION_HEADER: SECRET})
    assert ok.json().get("detail") != "Missing bearer token" and ok.status_code != 401, (path, ok.status_code, ok.text[:120])
    wrong = _call(c, method, path, body, {tenancy.TENANCY_PROVISION_HEADER: "w" * 40})
    # past the gate, refused by the route itself (it validates the secret): never the gate's message, never a success
    assert wrong.json().get("detail") != "Missing bearer token" and wrong.status_code in (401, 403), (path, wrong.status_code)
    if not usage and not path.endswith("/summary"):
        assert wrong.status_code == 403 and "provisioning secret" in wrong.json()["detail"], (path, wrong.status_code)


def test_the_tenancy_secret_does_not_open_the_partner_key_routes(env: Any) -> None:
    c, _key = env
    r = c.post("/api/v1/verify-citations", json={}, headers={tenancy.TENANCY_PROVISION_HEADER: SECRET})
    assert r.status_code == 401 and r.json()["detail"] == "Missing bearer token"


def test_every_route_and_method_under_api_v1_is_opened_only_when_listed(env: Any) -> None:
    """A route or a method added later stays refused for a key-only or secret-only caller until it is added on purpose."""
    from fastapi.routing import APIRoute

    c, key = env

    def walk(routes: Any) -> Any:
        for r in routes:
            if isinstance(r, APIRoute):
                yield r
            elif getattr(r, "original_router", None) is not None:
                yield from walk(r.original_router.routes)

    key_listed = {(m, p) for m, p, _b in KEY_ROUTES} | {("GET", "/api/v1/orgs/acme/usage")}
    # ON in the packaged config (B3 19 Oct 2026; A2-A8, S1, S3 with 0.6.23): each registers its own gate routes
    key_listed |= {
        ("POST", "/api/v1/s138-checklist"),
        ("POST", "/api/v1/chronology"),
        ("POST", "/api/v1/review-agenda"),
        ("POST", "/api/v1/matter-citation-sweep"),
        ("POST", "/api/v1/quashing-agenda"),
        ("GET", "/api/v1/quashing-agenda/health"),
        ("POST", "/api/v1/cheque-case-check"),
        ("POST", "/api/v1/matter-hearing-pack"),
        ("POST", "/api/v1/synopsis-dates"),
        ("POST", "/api/v1/bail-draft"),
        ("POST", "/api/v1/matter-spans/delete"),
        ("POST", "/api/v1/matter-spans/documents"),
        ("POST", "/api/v1/matter-spans/list"),
        ("POST", "/api/v1/matter-spans/matters"),
        ("POST", "/api/v1/matter-spans/my-matters"),  # wf_a2_list_matters, ON with its switch-on
        ("POST", "/api/v1/matter-spans/query"),
    }
    secret_listed = {(m, p) for m, p, _b in ORG_ROUTES}
    seen = 0
    for r in walk(c.app.routes):
        if not r.path.startswith("/api/v1/"):
            continue
        concrete = r.path.replace("{job_id}", "abc").replace("{org_id}", "acme").replace("{key_id}", "kid")
        for method in sorted(r.methods - {"HEAD", "OPTIONS"}):
            seen += 1
            for header, listed in ((tenancy.API_KEY_HEADER, key_listed), (tenancy.TENANCY_PROVISION_HEADER, secret_listed)):
                value = key if header == tenancy.API_KEY_HEADER else SECRET
                resp = _call(c, method, concrete, {} if method == "POST" else None, {header: value})
                gate_refused = resp.status_code == 401 and resp.json().get("detail") == "Missing bearer token"
                opened = (method, concrete) in listed or (
                    method == "GET" and concrete == "/api/v1/analyse-facts/jobs/abc" and header == tenancy.API_KEY_HEADER
                )
                assert gate_refused != opened, (method, concrete, header, resp.status_code)
    assert seen >= 15


@pytest.mark.parametrize(
    "path", ["/api/v1/audit/", "/API/V1/AUDIT", "/api/v1//audit", "/api/v1/audit%0A", "/api/v1/orgs/acme/usage%0A"]
)
def test_odd_spellings_of_a_listed_path_are_not_opened(env: Any, path: str) -> None:
    c, key = env
    r = c.get(path, headers={tenancy.API_KEY_HEADER: key})
    assert r.status_code != 200, (path, r.status_code)
    assert r.status_code in (401, 404, 307, 308, 405)


def test_a_repeated_key_header_is_not_a_credential_the_gate_trusts(env: Any) -> None:
    c, key = env
    r = c.get("/api/v1/audit", headers=[(tenancy.API_KEY_HEADER, key), (tenancy.API_KEY_HEADER, BAD)])
    assert r.status_code == 401 and r.json()["detail"] == "Missing bearer token"
    r2 = c.get("/api/v1/audit", headers={tenancy.API_KEY_HEADER: ""})
    assert r2.status_code == 401 and r2.json()["detail"] == "Missing bearer token"


@pytest.mark.parametrize("method", ["DELETE", "PATCH", "PUT"])
def test_another_method_on_a_listed_path_is_refused_by_the_gate(env: Any, method: str) -> None:
    c, key = env
    for path, header, value in (
        ("/api/v1/audit", tenancy.API_KEY_HEADER, key),
        ("/api/v1/orgs/acme/keys", tenancy.TENANCY_PROVISION_HEADER, SECRET),
        ("/api/v1/analyse-facts/jobs/abc", tenancy.API_KEY_HEADER, key),
    ):
        r = c.request(method, path, headers={header: value})
        assert r.status_code == 401 and r.json()["detail"] == "Missing bearer token", (method, path, r.status_code)


@pytest.mark.parametrize(
    "path", ["/api/v1/analyse-facts/jobs/abc/extra", "/api/v1/orgs/acme/usage/extra", "/api/v1/orgs/acme/keys/kid"]
)
def test_a_longer_path_than_a_listed_one_is_not_opened(env: Any, path: str) -> None:
    c, key = env
    for header, value in ((tenancy.API_KEY_HEADER, key), (tenancy.TENANCY_PROVISION_HEADER, SECRET)):
        r = c.get(path, headers={header: value})
        assert r.status_code == 401 and r.json()["detail"] == "Missing bearer token", (path, r.status_code)


@pytest.mark.parametrize(("method", "path", "body"), [*KEY_ROUTES, ("GET", "/api/v1/orgs/acme/usage", None)])
def test_a_revoked_key_is_401_on_every_listed_route_and_never_runs_as_anonymous(
    env: Any, tmp_path: Path, method: str, path: str, body: Any
) -> None:
    c, _key = env
    created = tenancy.create_key(tmp_path / "root", "acme", label="to-revoke")
    tenancy.revoke_key(tmp_path / "root", created.record.key_id)
    r = _call(c, method, path, body, {tenancy.API_KEY_HEADER: created.secret})
    assert r.status_code == 401 and r.json()["detail"] == "Invalid or revoked API key", (path, r.status_code, r.text[:100])


@pytest.mark.parametrize("method", ["POST", "DELETE", "PUT", "PATCH"])
def test_another_method_on_the_org_usage_route_is_refused_to_an_org_key(env: Any, method: str) -> None:
    c, key = env
    r = c.request(method, "/api/v1/orgs/acme/usage", headers={tenancy.API_KEY_HEADER: key})
    assert r.status_code == 401 and r.json()["detail"] == "Missing bearer token", (method, r.status_code)
