"""#1038: the browser's preflight for the screening after a cold-start wake names `X-Wake-Ticket`.

The engine's CORS allow-list must list it. The gateway Worker (deploy/gateway/worker.js) has no CORS code and
forwards every request header untouched, so this allow-list is the only place the header can be refused. Nothing
else about CORS changes: the same origins, the same methods, and every other custom header is still refused.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from pravrudhi.application.app_serve import build_app
from pravrudhi.application.init import init_project

H = {"host": "127.0.0.1:8008"}
ORIGIN = "https://pravrudhi.vercel.app"


def _client(tmp_path: Path, monkeypatch) -> TestClient:
    monkeypatch.setenv("PRAVRUDHI_ALLOWED_ORIGINS", ORIGIN)
    init_project(tmp_path)
    return TestClient(build_app(tmp_path), headers=H)


def _preflight(c: TestClient, request_headers: str, origin: str = ORIGIN):
    return c.options(
        "/api/state",
        headers={"origin": origin, "access-control-request-method": "POST", "access-control-request-headers": request_headers},
    )


def test_the_preflight_for_the_screening_after_a_wake_lists_x_wake_ticket(tmp_path: Path, monkeypatch) -> None:
    r = _preflight(_client(tmp_path, monkeypatch), "content-type, authorization, x-wake-ticket")
    assert r.status_code == 200, r.text
    allowed = {h.strip() for h in r.headers["access-control-allow-headers"].lower().split(",")}
    assert "x-wake-ticket" in allowed and {"content-type", "authorization"} <= allowed
    assert r.headers["access-control-allow-origin"] == ORIGIN


def test_the_allow_list_changed_by_exactly_one_header(tmp_path: Path, monkeypatch) -> None:
    r = _preflight(_client(tmp_path, monkeypatch), "content-type")
    allowed = {h.strip() for h in r.headers["access-control-allow-headers"].lower().split(",")}
    safelisted = {
        "accept",
        "accept-language",
        "content-language",
        "content-type",
    }  # Starlette always adds the CORS-safelisted names
    assert allowed - safelisted == {"authorization", "x-pravrudhi-token", "x-pravrudhi-operator", "x-wake-ticket"}


def test_other_custom_headers_and_other_origins_are_still_refused(tmp_path: Path, monkeypatch) -> None:
    c = _client(tmp_path, monkeypatch)
    # an unlisted header refuses the whole preflight
    assert _preflight(c, "x-wake-ticket, x-something-else").status_code == 400
    # the box waker's HMAC header is never a browser header
    assert _preflight(c, "x-waker-signature").status_code == 400
    other = _preflight(c, "x-wake-ticket", origin="https://evil.example")
    assert "access-control-allow-origin" not in other.headers
