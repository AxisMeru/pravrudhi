"""A local engine that can start GPU work must not answer arbitrary web pages."""

from pathlib import Path

from fastapi.testclient import TestClient

from pravrudhi.api.localguard import TOKEN_HEADER, app_token, token_path
from pravrudhi.application.app_serve import build_app
from pravrudhi.application.init import init_project


def _client(tmp_path):
    """A client whose Host header is a real loopback address, as a browser on the local engine would send."""
    init_project(tmp_path)
    return TestClient(build_app(tmp_path), base_url="http://127.0.0.1:8008"), tmp_path


def test_reading_is_open_but_starting_a_run_needs_the_local_token(tmp_path):
    client, root = _client(tmp_path)
    assert client.get("/api/health").status_code == 200, "a dashboard should read without ceremony"
    refused = client.post("/api/runs", json={"target": "model"})
    assert refused.status_code == 401 and TOKEN_HEADER in refused.json()["detail"]
    ok = client.post("/api/runs", json={"target": "model", "budget_gpu_h": 0.01},
                     headers={TOKEN_HEADER: app_token(root)})
    assert ok.status_code != 401


def test_a_malicious_page_cannot_start_a_run_even_with_a_stolen_shape(tmp_path):
    client, root = _client(tmp_path)
    r = client.post("/api/runs", json={"target": "model"},
                    headers={"origin": "https://evil.example", TOKEN_HEADER: app_token(root)})
    assert r.status_code == 403 and "cross-origin" in r.json()["detail"]


def test_dns_rebinding_is_refused_by_the_host_header(tmp_path):
    client, _ = _client(tmp_path)
    assert client.get("/api/health", headers={"host": "attacker.example"}).status_code == 421
    assert client.get("/api/health", headers={"host": "127.0.0.1:8008"}).status_code == 200
    assert client.get("/api/health", headers={"host": "localhost:8008"}).status_code == 200


def test_no_permissive_cors_by_default_and_the_token_is_not_readable_cross_origin(tmp_path, monkeypatch):
    monkeypatch.delenv("PRAVRUDHI_ALLOWED_ORIGINS", raising=False)
    client, _ = _client(tmp_path)
    r = client.get("/api/health", headers={"origin": "https://evil.example"})
    assert r.headers.get("access-control-allow-origin") is None, "no wildcard for a local unauthenticated engine"
    assert client.get("/api/app-token", headers={"origin": "https://evil.example"}).status_code == 403
    assert client.get("/api/app-token").status_code == 200


def test_the_token_file_is_owner_only(tmp_path):
    _, root = _client(tmp_path)
    tok = app_token(root)
    assert len(tok) > 20
    assert oct(token_path(root).stat().st_mode)[-3:] == "600"
    assert app_token(root) == tok, "the token is stable once created"


def test_an_operator_can_name_origins_when_they_mean_to(tmp_path, monkeypatch):
    monkeypatch.setenv("PRAVRUDHI_ALLOWED_ORIGINS", "https://pravrudhi.vercel.app")
    client, root = _client(tmp_path)
    r = client.get("/api/health", headers={"origin": "https://pravrudhi.vercel.app"})
    assert r.headers.get("access-control-allow-origin") == "https://pravrudhi.vercel.app"
    assert client.post("/api/runs", json={"target": "model"},
                       headers={"origin": "https://pravrudhi.vercel.app", TOKEN_HEADER: app_token(root)}).status_code != 403


def test_the_host_allowlist_matches_whole_names_not_substrings(tmp_path, monkeypatch):
    """An allowed origin of https://pravrudhi.vercel.app must not admit a Host of 'app' or 'vercel.app'."""
    from pravrudhi.api.localguard import hostname_of, permitted_hostnames

    monkeypatch.setenv("PRAVRUDHI_ALLOWED_ORIGINS", "https://pravrudhi.vercel.app")
    client, _ = _client(tmp_path)
    for spoof in ("app", "vercel.app", "p", "pravrudhi.vercel.app.evil.example"):
        assert client.get("/api/health", headers={"host": spoof}).status_code == 421, spoof
    assert client.get("/api/health", headers={"host": "pravrudhi.vercel.app"}).status_code == 200
    assert client.get("/api/health", headers={"host": "pravrudhi.vercel.app:443"}).status_code == 200
    assert permitted_hostnames() == {"pravrudhi.vercel.app"}
    assert hostname_of("https://example.com:8443/x") == "example.com"
    assert hostname_of("[::1]:8008") == "[::1]"
    assert hostname_of("127.0.0.1:8008") == "127.0.0.1"


def test_the_engine_does_not_refuse_its_own_interface(tmp_path: Path) -> None:
    """A browser sends Origin on every non-GET request, same-origin ones included. The guard must not read that as
    a cross-site attempt, or every button on the interface the engine itself serves is dead."""
    from pravrudhi.api.localguard import same_origin

    assert same_origin("http://127.0.0.1:8008", "127.0.0.1:8008") is True
    assert same_origin("http://localhost:8123", "localhost:8123") is True
    assert same_origin("", "127.0.0.1:8008") is True  # no Origin at all
    assert same_origin("https://evil.example", "127.0.0.1:8008") is False
    assert same_origin("http://127.0.0.1:9999", "127.0.0.1:8008") is False  # a different port is a different origin
    assert same_origin("file://", "127.0.0.1:8008") is False


def test_a_browser_preflight_that_names_the_bearer_header_is_allowed(tmp_path: Path, monkeypatch) -> None:
    """A signed-in web door sends `Authorization: Bearer …` on every call, which makes the browser preflight
    with that header named; the engine's allow-list once omitted it (and PUT/DELETE), so every authenticated
    call from https://pravrudhi.vercel.app died at the CORS layer as "Disallowed CORS headers" while anonymous
    ones passed. Found by the operator on 2026-09-12, the first time anyone signed in through a Worker."""
    monkeypatch.setenv("PRAVRUDHI_ALLOWED_ORIGINS", "https://pravrudhi.vercel.app")
    c, _ = _client(tmp_path)
    for method in ("GET", "POST", "PUT", "DELETE"):
        r = c.options("/api/state", headers={
            "origin": "https://pravrudhi.vercel.app",
            "access-control-request-method": method,
            "access-control-request-headers": "authorization,content-type",
        })
        assert r.status_code == 200, f"{method}: {r.status_code} {r.text}"
        assert "authorization" in r.headers.get("access-control-allow-headers", "").lower()
