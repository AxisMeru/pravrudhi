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
    # With nobody to identify, `roles.role_of` already resolves the local caller to the operator by
    # construction, and `access` must agree rather than read as an anonymous, unprivileged caller.
    assert me["access"] == "admin"
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
    assert me["access"] == "member", "signed in, not on PRAVRUDHI_ADMINS: a member, not an admin and not none"
    r = c.post("/api/workspaces", json={"slug": "legal"}, headers={**auth, TOKEN_HEADER: app_token(tmp_path)})
    assert r.status_code == 200, r.text
    assert (Path(r.json()["path"]) / ".pravrudhi").exists(), "a workspace is a real initialised directory"
    assert [w["slug"] for w in c.get("/api/workspaces", headers=auth).json()["workspaces"]] == ["legal"]
    bad = c.post("/api/workspaces", json={"slug": "../x"}, headers={**auth, TOKEN_HEADER: app_token(tmp_path)})
    assert bad.status_code == 422
    no_token = c.get("/api/me").json()
    assert no_token["authenticated"] is False, "no token, no identity, in optional mode"
    assert no_token["access"] == "none", "nobody signed in, and authentication is not disabled: not a member"


def test_access_names_the_operator_for_an_allowlisted_account(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PRAVRUDHI_AUTH", "optional")
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_JWT_SECRET", "s3cret-long-enough-for-hs256-testing-purposes")
    monkeypatch.setenv("PRAVRUDHI_ADMINS", "operator@example.com")
    monkeypatch.delenv("VERCEL", raising=False)
    monkeypatch.delenv("RENDER", raising=False)
    token = jwt.encode(
        {"sub": "op-1", "email": "operator@example.com", "role": "authenticated", "aud": "authenticated",
         "exp": 4102444800},
        "s3cret-long-enough-for-hs256-testing-purposes", algorithm="HS256",
    )
    c = _client(tmp_path)
    me = c.get("/api/me", headers={"authorization": f"Bearer {token}"}).json()
    assert me["access"] == "admin"


def test_no_request_field_can_move_access_off_what_the_allowlist_says(tmp_path: Path, monkeypatch) -> None:
    """`/api/me` reports `role` too -- the token's own, unverified claim -- precisely so a reader can see that
    it and `access` disagree here: a forged `role: admin` claim, a header naming the caller admin, and a query
    parameter asking for it all have no effect. `access` moves only with `PRAVRUDHI_ADMINS`."""
    monkeypatch.setenv("PRAVRUDHI_AUTH", "optional")
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_JWT_SECRET", "s3cret-long-enough-for-hs256-testing-purposes")
    monkeypatch.setenv("PRAVRUDHI_ADMINS", "operator@example.com")
    monkeypatch.delenv("VERCEL", raising=False)
    monkeypatch.delenv("RENDER", raising=False)
    forged = jwt.encode(
        {"sub": "attacker-1", "email": "attacker@example.com", "role": "admin", "aud": "authenticated",
         "exp": 4102444800},
        "s3cret-long-enough-for-hs256-testing-purposes", algorithm="HS256",
    )
    c = _client(tmp_path)
    me = c.get(
        "/api/me",
        headers={
            "authorization": f"Bearer {forged}", "x-pravrudhi-access": "admin", "x-pravrudhi-role": "admin",
        },
        params={"access": "admin", "role": "admin"},
    ).json()
    assert me["role"] == "admin", "the token's own unverified claim, unfiltered -- and exactly why it is not access"
    assert me["access"] == "member", "not on the allowlist, whatever the token, headers or query string claim"


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


def test_the_session_token_may_travel_in_the_query_string_for_event_streams(tmp_path: Path, monkeypatch) -> None:
    """EventSource cannot set headers; the run event stream carries `?access_token=` instead. The query token only
    stands in for a missing header, and the same verification applies (a bad one is still a 401)."""
    monkeypatch.setenv("PRAVRUDHI_AUTH", "required")
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_JWT_SECRET", "s3cret-long-enough-for-hs256-testing-purposes")
    monkeypatch.setenv("PRAVRUDHI_DISABLE_LOCAL_GUARD", "1")
    monkeypatch.delenv("VERCEL", raising=False)
    monkeypatch.delenv("RENDER", raising=False)
    token = jwt.encode({"sub": "user-1", "email": "u@example.com", "role": "authenticated", "aud": "authenticated",
                        "exp": 4102444800}, "s3cret-long-enough-for-hs256-testing-purposes", algorithm="HS256")
    c = _client(tmp_path)
    assert c.get("/api/me").status_code == 401
    assert c.get(f"/api/me?access_token={token}").json()["email"] == "u@example.com"
    assert c.get("/api/me?access_token=nope").status_code == 401
