"""Tests for PRAVRUDHI_IDENTITY_HEADER feature (identity header override)."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from pravrudhi.api import identity


@pytest.fixture(autouse=True)
def _reset_caches() -> None:
    identity._JWKS_CACHE["keys"] = None
    identity._JWKS_CACHE["fetched_at"] = 0.0
    identity._INTROSPECT_CACHE.clear()


_CURRENT_USER_DEP = Depends(identity.current_user)


def _client_for_current_user() -> TestClient:
    app = FastAPI()

    @app.get("/whoami")
    def whoami(user: identity.User | None = _CURRENT_USER_DEP) -> dict[str, Any]:
        return {"user": None if user is None else {"id": user.id, "email": user.email, "role": user.role}}

    return TestClient(app)


def test_identity_header_reads_from_custom_header_when_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """When PRAVRUDHI_IDENTITY_HEADER is set, read the user token from that header, not Authorization."""
    pytest.importorskip("jwt")
    secret = "test-secret"
    monkeypatch.setenv("PRAVRUDHI_AUTH", "optional")
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_JWT_SECRET", secret)
    monkeypatch.setenv("PRAVRUDHI_IDENTITY_HEADER", "X-Pravrudhi-Authorization")

    # Create a valid token
    import time

    import jwt as pyjwt
    now = time.time()
    claims = {
        "sub": "user-custom-header",
        "email": "custom@example.com",
        "role": "authenticated",
        "aud": "authenticated",
        "exp": now + 300,
    }
    token = pyjwt.encode(claims, secret, algorithm="HS256")

    client = _client_for_current_user()

    # Test 1: Token in custom header should be recognized
    resp = client.get("/whoami", headers={"X-Pravrudhi-Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json()["user"]["id"] == "user-custom-header"

    # Test 2: Token in Authorization header should be ignored
    resp = client.get("/whoami", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json()["user"] is None

    # Test 3: RunPod key in Authorization header should not cause errors
    resp = client.get("/whoami", headers={"Authorization": "Bearer runpod-key-abc123xyz"})
    assert resp.status_code == 200
    assert resp.json()["user"] is None


def test_identity_header_default_behavior_unchanged_when_not_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """When PRAVRUDHI_IDENTITY_HEADER is not set, default behavior is unchanged."""
    pytest.importorskip("jwt")
    secret = "test-secret"
    monkeypatch.setenv("PRAVRUDHI_AUTH", "optional")
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_JWT_SECRET", secret)
    monkeypatch.delenv("PRAVRUDHI_IDENTITY_HEADER", raising=False)

    import time

    import jwt as pyjwt
    now = time.time()
    claims = {
        "sub": "user-default",
        "email": "default@example.com",
        "role": "authenticated",
        "aud": "authenticated",
        "exp": now + 300,
    }
    token = pyjwt.encode(claims, secret, algorithm="HS256")

    client = _client_for_current_user()

    # Token in Authorization header should be recognized (default behavior)
    resp = client.get("/whoami", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json()["user"]["id"] == "user-default"

    # Custom header should be ignored
    resp = client.get("/whoami", headers={"X-Pravrudhi-Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json()["user"] is None


def test_query_token_still_works_with_custom_header(monkeypatch: pytest.MonkeyPatch) -> None:
    """?access_token query param should populate the custom header when set."""
    pytest.importorskip("jwt")
    secret = "test-secret"
    monkeypatch.setenv("PRAVRUDHI_AUTH", "optional")
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_JWT_SECRET", secret)
    monkeypatch.setenv("PRAVRUDHI_IDENTITY_HEADER", "X-Pravrudhi-Authorization")

    import time

    import jwt as pyjwt
    now = time.time()
    claims = {
        "sub": "user-query-token",
        "email": "query@example.com",
        "role": "authenticated",
        "aud": "authenticated",
        "exp": now + 300,
    }
    token = pyjwt.encode(claims, secret, algorithm="HS256")

    client = _client_for_current_user()

    # ?access_token should populate the custom header when custom header is set
    resp = client.get(f"/whoami?access_token={token}")
    assert resp.status_code == 200
    # When custom header is set, ?access_token populates the custom header
    assert resp.json()["user"]["id"] == "user-query-token"

    # Custom header can also be provided explicitly
    resp = client.get(
        "/whoami",
        headers={"X-Pravrudhi-Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200
    assert resp.json()["user"]["id"] == "user-query-token"
