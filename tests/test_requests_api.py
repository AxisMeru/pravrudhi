from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from pravrudhi.api.localguard import TOKEN_HEADER, app_token
from pravrudhi.api.server import create_app
from pravrudhi.application.requests import Criterion, capture


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(tmp_path), base_url="http://127.0.0.1:8008")


def _auth(root: Path) -> dict[str, str]:
    return {TOKEN_HEADER: app_token(root)}


def test_backlog_shape(tmp_path: Path, client: TestClient) -> None:
    capture(
        tmp_path, "make the dashboard show request status",
        criteria=[Criterion("ship it", source="operator")],
    )

    resp = client.get("/api/requests")

    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["open"] == 1
    assert "by_state" in data
    assert "oldest_open_days" in data
    row = data["requests"][0]
    assert row["text"] == "make the dashboard show request status"
    assert row["progress"] == [0, 1]
    assert row["criteria"][0]["source"] == "operator"
    assert row["criteria"][0]["met"] is False


def test_advance_to_delivered_with_unmet_criterion_is_409(tmp_path: Path, client: TestClient) -> None:
    req = capture(
        tmp_path, "wire the API to the ledger",
        criteria=[Criterion("endpoint responds", source="operator")],
    )
    in_progress = client.post(
        f"/api/requests/{req.id}/advance", json={"state": "in_progress"}, headers=_auth(tmp_path)
    )
    assert in_progress.status_code == 200

    resp = client.post(f"/api/requests/{req.id}/advance", json={"state": "delivered"}, headers=_auth(tmp_path))

    assert resp.status_code == 409
    assert "endpoint responds" in resp.json()["detail"]


def test_posting_evidence_marks_criterion_met(tmp_path: Path, client: TestClient) -> None:
    req = capture(
        tmp_path, "wire the API to the ledger",
        criteria=[Criterion("endpoint responds", source="operator")],
    )
    client.post(f"/api/requests/{req.id}/advance", json={"state": "in_progress"}, headers=_auth(tmp_path))

    resp = client.post(
        f"/api/requests/{req.id}/criteria/0/evidence",
        json={"kind": "commit", "ref": "abc1234", "note": "verified locally"},
        headers=_auth(tmp_path),
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["criteria"][0]["met"] is True
    assert body["criteria"][0]["evidence"][0] == {"kind": "commit", "ref": "abc1234", "note": "verified locally"}

    delivered = client.post(f"/api/requests/{req.id}/advance", json={"state": "delivered"}, headers=_auth(tmp_path))
    assert delivered.status_code == 200
    assert delivered.json()["state"] == "delivered"


def test_no_response_field_carries_a_secret_shaped_string(tmp_path: Path, client: TestClient) -> None:
    req = capture(
        tmp_path, "add a status page for the request backlog",
        criteria=[Criterion("checked", source="operator")],
    )
    token = app_token(tmp_path)

    for resp in (client.get("/api/requests"), client.get(f"/api/requests/{req.id}")):
        assert resp.status_code == 200
        assert token not in resp.text
        lowered = resp.text.lower()
        assert not any(marker in lowered for marker in ("api_key", "secret", "password", "bearer "))


def test_the_web_door_can_originate_an_ask(tmp_path: Path, client: TestClient) -> None:
    """r-e84f8a50: only the operator's local hook (`pravrudhi requests-capture`) could write the requests
    store; the web door had no way to originate an ask at all."""
    resp = client.post("/api/requests", json={"text": "wire the dashboard's ask box"}, headers=_auth(tmp_path))

    assert resp.status_code == 200
    body = resp.json()
    assert body["text"] == "wire the dashboard's ask box"
    assert body["state"] == "captured"
    assert body["criteria"] == []

    # Read back through GET, same as the criterion asks.
    listing = client.get("/api/requests", headers=_auth(tmp_path))
    assert listing.json()["total"] == 1
    assert listing.json()["requests"][0]["id"] == body["id"]


def test_an_asked_at_is_honoured_when_given(tmp_path: Path, client: TestClient) -> None:
    resp = client.post(
        "/api/requests", json={"text": "sharpen the retry logic", "asked_at": "2026-01-01T00:00:00.000Z"},
        headers=_auth(tmp_path),
    )
    assert resp.json()["asked_at"] == "2026-01-01T00:00:00.000Z"


def test_the_session_is_derived_from_the_caller_never_accepted_from_the_body(
    tmp_path: Path, client: TestClient
) -> None:
    """`capture` takes an arbitrary `session` label; a client-supplied one would be a caller claiming whatever
    provenance it likes for its own ask. The field is not even in the request schema."""
    resp = client.post(
        "/api/requests",
        json={"text": "sharpen the retry logic", "session": "agent-for-operator"},
        headers=_auth(tmp_path),
    )
    assert resp.status_code == 200
    assert resp.json()["session"] == "web"  # authentication is off in this client; not the injected label


def test_capturing_without_the_local_token_is_refused(tmp_path: Path, client: TestClient) -> None:
    """`LocalGuard` requires the local token on every state-changing method regardless of `auth_mode` (see
    `api/identity.py`'s own account: an `optional`-mode engine still requires it on POST/PUT/DELETE)."""
    resp = client.post("/api/requests", json={"text": "sharpen the retry logic"})
    assert resp.status_code == 401


def test_a_fresh_workspace_answers_every_read_route_rather_than_erroring(tmp_path):
    """A new user's first screen asked for the inbox and got a 500: the listing replayed a ledger that did not
    exist yet. Every read-only route must answer on a workspace that has never run a night."""
    from fastapi.testclient import TestClient

    from pravrudhi.api.server import create_app

    client = TestClient(create_app(tmp_path))
    headers = {"host": "127.0.0.1:8008"}
    for path in ("/api/health", "/api/inbox", "/api/requests", "/api/status", "/api/update", "/api/swarm"):
        r = client.get(path, headers=headers)
        assert r.status_code == 200, f"{path} answered {r.status_code} on a fresh workspace"
    assert client.get("/api/inbox", headers=headers).json() == []
