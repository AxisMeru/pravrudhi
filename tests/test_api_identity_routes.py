"""Identity is optional: a local engine answers honestly that nobody is logged in, and a token names its owner."""

from __future__ import annotations

from pathlib import Path

import jwt
from fastapi.testclient import TestClient

from pravrudhi.api.localguard import TOKEN_HEADER, app_token
from pravrudhi.application.app_serve import build_app
from pravrudhi.application.init import init_project

H = {"host": "127.0.0.1:8008"}


def _client(tmp_path: Path) -> TestClient:
    init_project(tmp_path)
    return TestClient(build_app(tmp_path), headers=H)


def test_disabled_identity_says_so_and_offers_only_the_local_workspace(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PRAVRUDHI_AUTH", "disabled")
    c = _client(tmp_path)
    me = c.get("/api/me").json()
    # Identity fields are all empty, which is the point of this test. The two edition fields are not identity:
    # they say which of the two products the caller is looking at, and a local machine with authentication off
    # is the operator's, so it is Studio.
    assert {k: me[k] for k in ("mode", "authenticated", "id", "email", "role")} == {
        "mode": "disabled", "authenticated": False, "id": None, "email": None, "role": None
    }
    assert me["edition"] == "Pravrudhi Studio" and me["tagline"]
    ws = c.get("/api/workspaces").json()
    assert ws["owner"] == "local" and [w["slug"] for w in ws["workspaces"]] == ["local"]
    r = c.post("/api/workspaces", json={"slug": "legal"}, headers={TOKEN_HEADER: app_token(tmp_path)})
    assert r.status_code == 400


def test_a_verified_token_names_its_owner_and_owns_its_workspaces(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PRAVRUDHI_AUTH", "optional")
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_JWT_SECRET", "s3cret-long-enough-for-hs256-testing-purposes")
    monkeypatch.setenv("PRAVRUDHI_WORKSPACES", str(tmp_path / "ws"))
    monkeypatch.delenv("VERCEL", raising=False)
    monkeypatch.delenv("RENDER", raising=False)
    token = jwt.encode({"sub": "user-1", "email": "u@example.com", "role": "authenticated", "aud": "authenticated",
                        "exp": 4102444800}, "s3cret-long-enough-for-hs256-testing-purposes", algorithm="HS256")
    c = _client(tmp_path)
    auth = {"authorization": f"Bearer {token}"}
    me = c.get("/api/me", headers=auth).json()
    assert me["authenticated"] and me["id"] == "user-1" and me["email"] == "u@example.com"
    r = c.post("/api/workspaces", json={"slug": "legal"}, headers={**auth, TOKEN_HEADER: app_token(tmp_path)})
    assert r.status_code == 200, r.text
    assert (Path(r.json()["path"]) / ".pravrudhi").exists(), "a workspace is a real initialised directory"
    assert [w["slug"] for w in c.get("/api/workspaces", headers=auth).json()["workspaces"]] == ["legal"]
    bad = c.post("/api/workspaces", json={"slug": "../x"}, headers={**auth, TOKEN_HEADER: app_token(tmp_path)})
    assert bad.status_code == 422
    assert c.get("/api/me").json()["authenticated"] is False, "no token, no identity, in optional mode"


def test_required_mode_refuses_every_api_route_without_a_token(tmp_path: Path, monkeypatch) -> None:
    """`required` means required: on an internet-facing engine (ADR-0051 addendum 3) no JSON route answers an
    anonymous caller. Before this test, only routes that asked *who* was calling ever checked; `/api/state` and
    its siblings answered anyone who found the tunnel."""
    from pravrudhi.api.identity import PUBLIC_PATHS

    monkeypatch.setenv("PRAVRUDHI_AUTH", "required")
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("PRAVRUDHI_DISABLE_LOCAL_GUARD", "1")
    monkeypatch.delenv("VERCEL", raising=False)
    monkeypatch.delenv("RENDER", raising=False)
    init_project(tmp_path)
    app = build_app(tmp_path)
    c = TestClient(app, headers=H)
    plain_gets = sorted(
        path for path, ops in app.openapi()["paths"].items()
        if "get" in ops and "{" not in path and path.startswith("/api/")
    )
    assert len(plain_gets) > 20, "the route table should be the real one"
    open_routes = [p for p in plain_gets if p not in PUBLIC_PATHS and c.get(p).status_code != 401]
    assert open_routes == [], f"answered without identity in required mode: {open_routes}"
    for public in PUBLIC_PATHS:
        assert c.get(public).status_code != 401, f"{public} is the tunnel's health check and stays open"
    # A browser's preflight carries no token by design; refusing it would refuse the signed-in user too.
    assert c.options("/api/state", headers={"origin": "https://pravrudhi.vercel.app",
                                            "access-control-request-method": "GET"}).status_code != 401
