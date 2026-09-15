"""The operator's own line into the model network this engine already routes the swarm through.

request r-799f8dfb: the operator asked for a way to keep talking to the engine from inside Super Pravrudhi
(the Studio/admin surface, `roles.ADMIN_ONLY`) if the session driving this work hits its limit. The network
this reaches is the same one `application/network_chat.py` already built for the swarm's own dispatches --
this route is the front door onto it, registered in `api/server.py` next to every other admin surface, gated
the same way, and never present on a product install (`roles.gate`, S-edition).

These tests go through `create_app` and a real `TestClient`, not `converse` called directly, because the thing
being proven is that the route as wired into the running application -- past the local-token guard and the
admin gate -- answers an operator's turn with the model's, not merely that the underlying function works.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from pravrudhi.api.localguard import TOKEN_HEADER, app_token
from pravrudhi.api.roles import ADMIN_ONLY
from pravrudhi.api.server import create_app
from pravrudhi.application.init import init_project


class FakeModel:
    """A scripted `Complete`: one canned answer per round, and a record of what it was shown."""

    def __init__(self, *script: dict[str, Any]) -> None:
        self.script = list(script)
        self.seen: list[list[dict[str, str]]] = []

    def __call__(self, messages: list[dict[str, str]], tools: list[dict[str, Any]]) -> dict[str, Any]:
        self.seen.append(list(messages))
        return self.script.pop(0) if self.script else {"content": "", "tool_calls": []}


def _client(root: Path, *, operator_complete: Any | None = None) -> TestClient:
    return TestClient(create_app(root, operator_complete=operator_complete), base_url="http://127.0.0.1:8008")


def test_the_route_is_registered_in_server_py(tmp_path: Path) -> None:
    assert "/api/operator/chat" in create_app(tmp_path).openapi()["paths"]


def test_it_is_classified_as_an_operator_only_surface() -> None:
    assert "/api/operator/chat" in ADMIN_ONLY


def test_an_operator_turn_gets_the_agent_turn_back_through_the_running_app(tmp_path: Path) -> None:
    model = FakeModel({"content": "the swarm is idle and every route is healthy.", "tool_calls": []})
    client = _client(tmp_path, operator_complete=model)
    tok = {TOKEN_HEADER: app_token(tmp_path)}

    resp = client.post("/api/operator/chat", json={"message": "status?", "thread_id": None}, headers=tok)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["reply"] == "the swarm is idle and every route is healthy."
    assert body["thread_id"]
    assert body["citations"] == [] and body["tool_calls"] == [] and body["refusals"] == []
    assert model.seen, "the operator's message must have actually reached the model"


def test_a_second_turn_continues_the_same_thread(tmp_path: Path) -> None:
    model = FakeModel(
        {"content": "first reply", "tool_calls": []},
        {"content": "second reply", "tool_calls": []},
    )
    client = _client(tmp_path, operator_complete=model)
    tok = {TOKEN_HEADER: app_token(tmp_path)}

    first = client.post("/api/operator/chat", json={"message": "one", "thread_id": None}, headers=tok).json()
    second = client.post(
        "/api/operator/chat", json={"message": "two", "thread_id": first["thread_id"]}, headers=tok
    ).json()

    assert second["thread_id"] == first["thread_id"]
    assert any(m["content"] == "one" for m in model.seen[-1]), "the second turn must see the first"


def test_an_empty_message_is_refused(tmp_path: Path) -> None:
    client = _client(tmp_path, operator_complete=FakeModel())
    tok = {TOKEN_HEADER: app_token(tmp_path)}

    resp = client.post("/api/operator/chat", json={"message": "   ", "thread_id": None}, headers=tok)

    assert resp.status_code == 422


def test_a_non_admin_signed_in_caller_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The gate `roles.gate` attaches to every `ADMIN_ONLY` path must cover this one too."""
    import jwt

    secret = "s3cret-long-enough-for-hs256-testing-purposes"
    monkeypatch.setenv("PRAVRUDHI_AUTH", "optional")
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_JWT_SECRET", secret)
    monkeypatch.setenv("PRAVRUDHI_ADMINS", "the-operator")
    monkeypatch.delenv("SUPABASE_SERVICE_KEY", raising=False)
    token = jwt.encode(
        {"sub": "some-user", "email": "some-user@example.com", "role": "authenticated",
         "aud": "authenticated", "exp": 4102444800},
        secret, algorithm="HS256",
    )
    client = _client(tmp_path, operator_complete=FakeModel())
    tok = {TOKEN_HEADER: app_token(tmp_path), "authorization": f"Bearer {token}"}

    resp = client.post("/api/operator/chat", json={"message": "hi", "thread_id": None}, headers=tok)

    assert resp.status_code == 403


def test_absent_on_a_product_edition_install(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Studio surfaces do not exist on a product engine (roles.gate, `_not_in_this_edition`) -- confirming this
    one is wired the same way as every other Super Pravrudhi surface, not merely admin-checked."""
    init_project(tmp_path)
    monkeypatch.setenv("PRAVRUDHI_EDITION", "product")
    client = _client(tmp_path, operator_complete=FakeModel())
    tok = {TOKEN_HEADER: app_token(tmp_path)}

    resp = client.post("/api/operator/chat", json={"message": "hi", "thread_id": None}, headers=tok)

    assert resp.status_code == 404


def test_with_no_endpoint_configured_it_still_reaches_the_model_network_by_default(tmp_path: Path) -> None:
    """Nothing under test wires a live network route, but the default (`operator_complete=None`) must be the
    engine's own routing table rather than a hardcoded local endpoint -- that continuity, not a bare reply, is
    the whole reason this surface was asked for (r-799f8dfb): so it keeps answering if this very session does not."""
    from pravrudhi.application.network_chat import NoModelAvailable

    client = _client(tmp_path, operator_complete=None)
    tok = {TOKEN_HEADER: app_token(tmp_path)}

    resp = client.post("/api/operator/chat", json={"message": "hi", "thread_id": None}, headers=tok)

    assert resp.status_code == 503
    assert "no model in the network" in resp.json()["detail"]
    assert NoModelAvailable  # the route's default raises this family of error, not a generic 500
