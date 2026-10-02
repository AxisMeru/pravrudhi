"""Tests for src/pravrudhi/api/identity.py."""

from __future__ import annotations

import time
from typing import Any

import httpx
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


def test_disabled_mode_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PRAVRUDHI_AUTH", "disabled")
    client = _client_for_current_user()
    resp = client.get("/whoami")
    assert resp.status_code == 200
    assert resp.json() == {"user": None}


def test_disabled_mode_ignores_a_supplied_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PRAVRUDHI_AUTH", "disabled")
    client = _client_for_current_user()
    resp = client.get("/whoami", headers={"Authorization": "Bearer whatever"})
    assert resp.status_code == 200
    assert resp.json() == {"user": None}


def test_required_mode_with_no_url_refuses_to_start(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PRAVRUDHI_AUTH", "required")
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    with pytest.raises(RuntimeError, match="SUPABASE_URL"):
        identity.guard_boot()


def test_deployed_env_refuses_non_required_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PRAVRUDHI_AUTH", "disabled")
    monkeypatch.setenv("VERCEL", "1")
    with pytest.raises(RuntimeError, match="deployed-environment"):
        identity.guard_boot()


def test_required_mode_with_url_and_no_token_401(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PRAVRUDHI_AUTH", "required")
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    client = _client_for_current_user()
    resp = client.get("/whoami")
    assert resp.status_code == 401


def _hs256_token(secret: str, *, exp_offset: float) -> str:
    jwt = pytest.importorskip("jwt")
    now = time.time()
    claims = {
        "sub": "user-hs256",
        "email": "hs@example.com",
        "role": "authenticated",
        "aud": "authenticated",
        "exp": now + exp_offset,
    }
    return jwt.encode(claims, secret, algorithm="HS256")


def test_hs256_fallback_verifies_with_shared_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("jwt")
    secret = "test-shared-secret"
    monkeypatch.setenv("SUPABASE_JWT_SECRET", secret)
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    token = _hs256_token(secret, exp_offset=300.0)
    claims = identity.verify_token(token)
    assert claims["sub"] == "user-hs256"
    assert claims["email"] == "hs@example.com"


def test_expired_token_is_401(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("jwt")
    secret = "test-shared-secret"
    monkeypatch.setenv("PRAVRUDHI_AUTH", "required")
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_JWT_SECRET", secret)
    token = _hs256_token(secret, exp_offset=-60.0)
    client = _client_for_current_user()
    resp = client.get("/whoami", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401


def test_jwks_es256_verification_via_injected_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("jwt", reason="PyJWT not installed in this environment")
    pytest.importorskip("cryptography", reason="cryptography not installed in this environment")
    import jwt as pyjwt
    from cryptography.hazmat.primitives.asymmetric import ec
    from jwt.algorithms import ECAlgorithm

    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.delenv("SUPABASE_JWT_SECRET", raising=False)

    private_key = ec.generate_private_key(ec.SECP256R1())
    algo = ECAlgorithm(ECAlgorithm.SHA256)
    import json as _json

    public_jwk = _json.loads(algo.to_jwk(private_key.public_key()))
    public_jwk["kid"] = "test-kid-1"
    public_jwk["use"] = "sig"
    jwks_body = {"keys": [public_jwk]}

    claims = {
        "sub": "user-es256",
        "email": "es@example.com",
        "role": "authenticated",
        "aud": "authenticated",
        "exp": time.time() + 300,
    }
    token = pyjwt.encode(claims, private_key, algorithm="ES256", headers={"kid": "test-kid-1"})

    calls: list[str] = []

    def fake_fetch(url: str, *, headers: dict[str, str] | None = None) -> httpx.Response:
        calls.append(url)
        assert url == identity._jwks_url()
        return httpx.Response(200, request=httpx.Request("GET", url), json=jwks_body)

    claims = identity.verify_token(token, fetch=fake_fetch)
    assert claims["sub"] == "user-es256"
    assert claims["email"] == "es@example.com"
    assert calls, "the injected fetch must be used instead of a real network call"


def _hs256_token_with(secret: str, **claim_offsets: float) -> str:
    jwt = pytest.importorskip("jwt")
    now = time.time()
    claims: dict[str, Any] = {
        "sub": "user-skew",
        "role": "authenticated",
        "aud": "authenticated",
        "exp": now + 300,
    }
    claims.update({k: now + v for k, v in claim_offsets.items()})
    return jwt.encode(claims, secret, algorithm="HS256")


def _hs256_env(monkeypatch: pytest.MonkeyPatch, secret: str = "skew-secret") -> str:
    monkeypatch.setenv("SUPABASE_JWT_SECRET", secret)
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("PRAVRUDHI_JWT_LEEWAY_S", raising=False)
    return secret


def test_iat_slightly_in_future_is_accepted_within_default_leeway(monkeypatch: pytest.MonkeyPatch) -> None:
    secret = _hs256_env(monkeypatch)
    token = _hs256_token_with(secret, iat=2.0)
    assert identity.verify_token(token)["sub"] == "user-skew"


def test_nbf_slightly_in_future_is_accepted_within_default_leeway(monkeypatch: pytest.MonkeyPatch) -> None:
    secret = _hs256_env(monkeypatch)
    token = _hs256_token_with(secret, nbf=2.0)
    assert identity.verify_token(token)["sub"] == "user-skew"


def test_iat_far_in_future_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    jwt = pytest.importorskip("jwt")
    secret = _hs256_env(monkeypatch)
    token = _hs256_token_with(secret, iat=120.0)
    with pytest.raises(jwt.InvalidTokenError):
        identity.verify_token(token)


def test_expired_beyond_leeway_still_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    jwt = pytest.importorskip("jwt")
    secret = _hs256_env(monkeypatch)
    token = _hs256_token_with(secret, exp=-60.0)
    with pytest.raises(jwt.ExpiredSignatureError):
        identity.verify_token(token)


def test_leeway_is_configurable_by_env(monkeypatch: pytest.MonkeyPatch) -> None:
    jwt = pytest.importorskip("jwt")
    secret = _hs256_env(monkeypatch)
    token = _hs256_token_with(secret, iat=30.0)
    with pytest.raises(jwt.InvalidTokenError):
        identity.verify_token(token)
    monkeypatch.setenv("PRAVRUDHI_JWT_LEEWAY_S", "60")
    assert identity.verify_token(token)["sub"] == "user-skew"


def test_bad_leeway_env_falls_back_to_default(monkeypatch: pytest.MonkeyPatch) -> None:
    secret = _hs256_env(monkeypatch)
    monkeypatch.setenv("PRAVRUDHI_JWT_LEEWAY_S", "not-a-number")
    assert identity.verify_token(_hs256_token_with(secret, iat=2.0))["sub"] == "user-skew"


def test_leeway_ceiling_is_60s(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    monkeypatch.setenv("PRAVRUDHI_JWT_LEEWAY_S", "60")
    assert identity._jwt_leeway_s() == 60.0
    for bad in ("61", "3600", "86400"):
        monkeypatch.setenv("PRAVRUDHI_JWT_LEEWAY_S", bad)
        caplog.clear()
        with caplog.at_level("WARNING"):
            assert identity._jwt_leeway_s() == identity._DEFAULT_JWT_LEEWAY_S
        assert "PRAVRUDHI_JWT_LEEWAY_S" in caplog.text


def test_oversized_leeway_does_not_disable_checks(monkeypatch: pytest.MonkeyPatch) -> None:
    jwt = pytest.importorskip("jwt")
    secret = _hs256_env(monkeypatch)
    monkeypatch.setenv("PRAVRUDHI_JWT_LEEWAY_S", "86400")
    with pytest.raises(jwt.ExpiredSignatureError):
        identity.verify_token(_hs256_token_with(secret, exp=-3600.0))
