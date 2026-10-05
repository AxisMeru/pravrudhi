"""The Studio edition admits administrators only: an engine-wide gate, evaluated before any route code runs.

The product and Studio share one identity provider, so without it any signed-in product account could reach a Studio
engine through its tunnel and, with #227/#244, the operator's keys and CLI seat."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from pravrudhi.api import identity
from pravrudhi.api.localguard import TOKEN_HEADER, app_token
from pravrudhi.api.server import create_app
from pravrudhi.application import tenancy

TOKENS = {
    "admin-token": {"sub": "op-1", "email": "op@example.com", "role": "authenticated"},
    "plain-token": {"sub": "u-2", "email": "someone@example.com", "role": "authenticated"},
    "forged-token": {"sub": "u-3", "email": "forger@example.com", "role": "admin"},  # claims admin; claim is ignored
}
ROUTES = [("GET", "/api/nyaya/vendors"), ("POST", "/api/nyaya/ask"), ("GET", "/api/me"), ("GET", "/api/state")]
BODY = {"question": "q?", "vendors": ["openai-api"]}


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    for v in ("PRAVRUDHI_EDITION", "PRAVRUDHI_STUDIO_LOOPBACK_ONLY", "PRAVRUDHI_IDENTITY_HEADER", "PRAVRUDHI_ADMINS"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("PRAVRUDHI_AUTH", "required")
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("PRAVRUDHI_ADMINS", "op-1")

    def fake_verify(token: str, **_k: Any) -> dict[str, Any]:
        if token not in TOKENS:
            raise identity.HTTPException(status_code=401, detail="Invalid token")
        return TOKENS[token]

    monkeypatch.setattr(identity, "verify_token", fake_verify)


@pytest.fixture
def engine(tmp_path: Path) -> Path:
    eng = tmp_path / "engine"
    eng.mkdir()
    return eng


def _client(engine: Path) -> TestClient:
    return TestClient(create_app(engine), base_url="http://localhost", raise_server_exceptions=False)


def _call(c: TestClient, engine: Path, method: str, path: str, token: str | None, **kw: Any) -> Any:
    headers = {TOKEN_HEADER: app_token(engine), **kw.pop("headers", {})}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return c.request(method, path, headers=headers, json=BODY if method == "POST" else None, **kw)


def _studio(monkeypatch: pytest.MonkeyPatch, loopback: bool) -> None:
    monkeypatch.setenv("PRAVRUDHI_EDITION", "studio")
    if loopback:
        monkeypatch.setenv("PRAVRUDHI_STUDIO_LOOPBACK_ONLY", "1")


@pytest.mark.parametrize("loopback", [False, True], ids=["studio", "studio-loopback"])
@pytest.mark.parametrize(("method", "path"), ROUTES)
def test_studio_refuses_anonymous_401_non_admin_403_and_admits_the_admin(
    engine: Path, monkeypatch: pytest.MonkeyPatch, loopback: bool, method: str, path: str
) -> None:
    _studio(monkeypatch, loopback)
    c = _client(engine)
    assert _call(c, engine, method, path, None).status_code == 401
    assert _call(c, engine, method, path, "plain-token").status_code == 403
    assert _call(c, engine, method, path, "forged-token").status_code == 403  # the token's own role claim is ignored
    assert _call(c, engine, method, path, "bad-token").status_code == 401
    assert _call(c, engine, method, path, "admin-token").status_code not in (401, 403)


@pytest.mark.parametrize("edition", ["product", None, "dev"])
def test_other_editions_are_not_gated(engine: Path, monkeypatch: pytest.MonkeyPatch, edition: str | None) -> None:
    if edition:
        monkeypatch.setenv("PRAVRUDHI_EDITION", edition)
    c = _client(engine)
    assert _call(c, engine, "GET", "/api/me", "plain-token").status_code == 200


def test_the_edition_is_read_as_tenant_vendors_reads_it(engine: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PRAVRUDHI_EDITION", " STUDIO ")
    assert _call(_client(engine), engine, "GET", "/api/nyaya/vendors", "plain-token").status_code == 403


def test_authentication_off_keeps_the_local_operator_working(engine: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _studio(monkeypatch, loopback=True)
    monkeypatch.setenv("PRAVRUDHI_AUTH", "disabled")
    c = _client(engine)
    assert _call(c, engine, "GET", "/api/nyaya/vendors", None).status_code == 200
    assert _call(c, engine, "GET", "/api/me", None).status_code == 200


@pytest.mark.parametrize("admins", [None, "", " , "])
def test_an_empty_or_unset_allowlist_names_nobody(engine: Path, monkeypatch: pytest.MonkeyPatch, admins: str | None) -> None:
    _studio(monkeypatch, loopback=True)
    if admins is None:
        monkeypatch.delenv("PRAVRUDHI_ADMINS")
    else:
        monkeypatch.setenv("PRAVRUDHI_ADMINS", admins)
    c = _client(engine)
    for token in ("admin-token", "plain-token"):
        assert _call(c, engine, "GET", "/api/nyaya/vendors", token).status_code == 403


def test_the_allowlist_matches_by_email_as_well_as_id(engine: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _studio(monkeypatch, loopback=False)
    monkeypatch.setenv("PRAVRUDHI_ADMINS", "Op@Example.com")
    c = _client(engine)
    assert _call(c, engine, "GET", "/api/me", "admin-token").status_code == 200
    assert _call(c, engine, "GET", "/api/me", "plain-token").status_code == 403


def test_health_preflight_and_non_api_paths_stay_open(engine: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _studio(monkeypatch, loopback=False)
    c = _client(engine)
    assert c.get("/api/health").status_code == 200
    preflight = {"Origin": "http://localhost", "Access-Control-Request-Method": "POST"}
    assert c.options("/api/nyaya/ask", headers=preflight).status_code != 403
    assert c.get("/ping").status_code not in (401, 403)


def test_refusal_happens_before_the_route_runs_no_vendor_call(engine: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import pravrudhi.models.openai_compat as oc

    calls: list[int] = []
    monkeypatch.setattr(oc.ChatClient, "chat", lambda *a, **k: calls.append(1))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-" + "o" * 24)
    _studio(monkeypatch, loopback=True)
    c = _client(engine)
    assert _call(c, engine, "POST", "/api/nyaya/ask", "plain-token").status_code == 403
    assert calls == []
    assert not (engine / "research" / "nyaya" / "asks").exists()


def test_a_partner_key_caller_is_refused_in_studio_even_with_no_bearer(engine: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tenancy.create_org(engine, "acme", "Acme")
    secret = tenancy.create_key(engine, "acme", label="t").secret
    _studio(monkeypatch, loopback=False)
    c = _client(engine)
    r = c.post("/api/v1/analyse-facts", json={"facts": ["TOY: x"], "contract_ids": ["bns69"]},
               headers={tenancy.API_KEY_HEADER: secret, TOKEN_HEADER: app_token(engine)})
    assert r.status_code == 403


def test_an_identity_header_override_cannot_bypass_the_gate(engine: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _studio(monkeypatch, loopback=True)
    monkeypatch.setenv("PRAVRUDHI_IDENTITY_HEADER", "x-user-token")
    c = _client(engine)
    path = "/api/me"
    assert _call(c, engine, "GET", path, None, headers={"x-user-token": "Bearer plain-token"}).status_code == 403
    assert _call(c, engine, "GET", path, "plain-token").status_code == 401  # Authorization is not the identity header now
    assert _call(c, engine, "GET", path, "admin-token").status_code == 401
    assert _call(c, engine, "GET", path, None, headers={"x-user-token": "Bearer admin-token"}).status_code == 200


def test_the_query_token_path_is_gated_too(engine: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _studio(monkeypatch, loopback=False)
    c = _client(engine)
    h = {TOKEN_HEADER: app_token(engine)}
    assert c.get("/api/me?access_token=plain-token", headers=h).status_code == 403
    assert c.get("/api/me?access_token=admin-token", headers=h).status_code == 200
    assert c.get("/api/me", headers=h).status_code == 401


def test_a_websocket_is_gated_not_just_the_header_path(engine: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _studio(monkeypatch, loopback=False)
    c = _client(engine)
    for query, code in (("", 4401), ("?access_token=plain-token", 4403)):
        with pytest.raises(WebSocketDisconnect) as e, c.websocket_connect(f"/api/anything{query}"):
            pass
        assert e.value.code == code


@pytest.mark.parametrize("path", ["/openapi.json", "/docs", "/redoc"])
def test_the_schema_and_docs_pages_are_the_operators_on_studio(engine: Path, monkeypatch: pytest.MonkeyPatch, path: str) -> None:
    _studio(monkeypatch, loopback=False)
    c = _client(engine)
    assert c.get(path).status_code == 401
    assert c.get(path, headers={"Authorization": "Bearer plain-token"}).status_code == 403
    assert c.get(path, headers={"Authorization": "Bearer admin-token"}).status_code == 200
    monkeypatch.setenv("PRAVRUDHI_EDITION", "product")  # the product keeps serving its schema
    assert _client(engine).get(path).status_code == 200


@pytest.mark.parametrize("edition", ["Studio-ish", "prod", "staging"])
def test_an_unrecognised_edition_refuses_to_start_instead_of_serving_ungated(
    engine: Path, monkeypatch: pytest.MonkeyPatch, edition: str
) -> None:
    from pravrudhi.deployment import DeploymentConfigError, validate

    monkeypatch.setenv("PRAVRUDHI_EDITION", edition)
    with pytest.raises(DeploymentConfigError):
        validate()
