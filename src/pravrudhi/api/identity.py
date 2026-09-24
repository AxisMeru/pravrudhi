"""Who is making this request — Supabase identity for Pravrudhi's multi-user surface.

This is deliberately narrower than `localguard.py`. `localguard` answers whether a state-changing request
came from the same-origin page this engine served, and it remains the CSRF guard for every deployment
shape, including a hosted one with Supabase auth wired in front of it. This module answers a different
question — which account, if any, sent the request — and never substitutes for that guard. A `disabled`-
or `optional`-mode engine still requires the local token on POST/PUT/DELETE; a `required`-mode engine
requires both the local token and a verified bearer token.

Verification mirrors `/home/ss/projects/kundali/backend/app/auth.py`, the operator's proven Supabase JWT
verifier, but not its style: JWKS is fetched from `{SUPABASE_URL}/auth/v1/.well-known/jwks.json` and cached
for an hour, ES256/RS256 tokens are verified against it with audience "authenticated", HS256 tokens fall
back to `SUPABASE_JWT_SECRET`, and anything else falls back to introspection against `/auth/v1/user`. The
HTTP fetch is an injected parameter rather than a hard-wired `httpx.get`, so the JWKS and introspection
paths can be exercised in tests with no network.

`docs/superpowers/specs/2026-09-05-pravrudhi-multitenant-design.md`'s Amendment ("one ledger per
workspace, no kernel change") is why this module never reads or writes a ledger row: a user id here only
selects a workspace directory (see `pravrudhi.application.workspaces`), never a filter on shared kernel
state.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

import httpx
from fastapi import Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.requests import HTTPConnection
from starlette.types import ASGIApp, Receive, Scope, Send

HttpFetch = Callable[..., httpx.Response]

_JWKS_CACHE: dict[str, Any] = {"keys": None, "fetched_at": 0.0}
_JWKS_TTL = 3600.0
_INTROSPECT_CACHE: dict[str, dict[str, Any]] = {}
_INTROSPECT_TTL = 300.0


class AuthMode(StrEnum):
    """How hard this engine insists on knowing who is asking.

    `disabled` is the default: the primary product is a local single-user engine that must keep booting
    with no login screen (design doc §1, Shape a). `optional` verifies a bearer token when one is present
    but does not require one. `required` rejects every request with no valid token — the fully-hosted
    shape (c)."""

    DISABLED = "disabled"
    OPTIONAL = "optional"
    REQUIRED = "required"


@dataclass(frozen=True, slots=True)
class User:
    """The identity of the caller, once verified. Nothing here is evidence — it is who asked, not what
    the ledger says happened."""

    id: str
    email: str | None
    role: str


def auth_mode() -> AuthMode:
    raw = os.environ.get("PRAVRUDHI_AUTH", "disabled").strip().lower()
    try:
        return AuthMode(raw)
    except ValueError:
        return AuthMode.DISABLED


def _supabase_url() -> str:
    return os.environ.get("SUPABASE_URL", "").rstrip("/")


def _jwks_url() -> str:
    base = _supabase_url()
    return f"{base}/auth/v1/.well-known/jwks.json" if base else ""


def _deployed_env() -> bool:
    return bool(os.environ.get("VERCEL") or os.environ.get("RENDER"))


def guard_boot() -> None:
    """Refuse to start in a configuration that would silently expose or disable this engine.

    Mirrors kundali's `_guard_auth_disabled_in_deployed_env`: a deployment-platform marker (`VERCEL` or
    `RENDER`) means the process is reachable from the public internet, so anything short of `required`
    there is refused, exactly as kundali refuses `AUTH_DISABLED=1` on a deployment. `required` mode is
    also refused everywhere if there is no Supabase project to verify tokens against — a required mode
    that cannot verify anything would reject every request, which is a worse failure than refusing to
    start.
    """
    mode = auth_mode()
    if mode == AuthMode.REQUIRED and not _supabase_url():
        raise RuntimeError(
            "PRAVRUDHI_AUTH=required but SUPABASE_URL is not set — refusing to start with no way to verify a token."
        )
    if mode != AuthMode.REQUIRED and _deployed_env():
        raise RuntimeError(
            "PRAVRUDHI_AUTH is not 'required' but a deployed-environment marker (VERCEL/RENDER) is set — refusing "
            "to start unauthenticated on a publicly reachable engine."
        )
    opened = _demo_anon_env()
    if opened - DEMO_ANON_CAPABLE:
        raise RuntimeError(
            f"PRAVRUDHI_DEMO_ANON_PATHS names {sorted(opened - DEMO_ANON_CAPABLE)}, outside the routes that may answer "
            f"anonymously ({sorted(DEMO_ANON_CAPABLE)}) — refusing to start."
        )
    if opened and _deployed_env():
        raise RuntimeError(
            "PRAVRUDHI_DEMO_ANON_PATHS is set on a deployment platform (VERCEL/RENDER) — the anonymous demo allowance "
            "is for the operator's own host only; refusing to start."
        )


def _default_fetch(url: str, *, headers: dict[str, str] | None = None) -> httpx.Response:
    return httpx.get(url, headers=headers or {}, timeout=10.0)


def _get_jwks(url: str, fetch: HttpFetch) -> dict[str, Any]:
    now = time.time()
    cached = _JWKS_CACHE["keys"]
    if cached is not None and now - _JWKS_CACHE["fetched_at"] < _JWKS_TTL:
        return dict(cached)
    resp = fetch(url)
    resp.raise_for_status()
    jwks = resp.json()
    _JWKS_CACHE.update(keys=jwks, fetched_at=now)
    return dict(jwks)


def _introspect(token: str, fetch: HttpFetch) -> dict[str, Any]:
    base = _supabase_url()
    if not base:
        raise HTTPException(status_code=401, detail="Cannot verify token (no Supabase URL)")
    now = time.time()
    cached = _INTROSPECT_CACHE.get(token)
    if cached and now - cached["at"] < _INTROSPECT_TTL:
        return dict(cached["claims"])
    resp = fetch(f"{base}/auth/v1/user", headers={"Authorization": f"Bearer {token}"})
    if resp.status_code != 200:
        raise HTTPException(status_code=401, detail="Token rejected by Supabase")
    body = resp.json()
    claims = {"sub": body.get("id"), "email": body.get("email"), "role": "authenticated"}
    if len(_INTROSPECT_CACHE) > 512:
        _INTROSPECT_CACHE.clear()
    _INTROSPECT_CACHE[token] = {"claims": claims, "at": now}
    return dict(claims)


def verify_token(token: str, *, fetch: HttpFetch = _default_fetch) -> dict[str, Any]:
    """Verify a Supabase-issued bearer token and return its claims. Raises on failure.

    `fetch` is injected so JWKS lookups and introspection calls need no network in tests; production code
    never needs to pass it.
    """
    import jwt as pyjwt
    from jwt import PyJWK

    header = pyjwt.get_unverified_header(token)
    alg = header.get("alg", "")
    jwks_url = _jwks_url()

    if alg in ("ES256", "RS256") and jwks_url:
        jwks = _get_jwks(jwks_url, fetch)
        kid = header.get("kid")
        key_data = next((k for k in jwks.get("keys", []) if k.get("kid") == kid), None)
        if key_data is None:
            # kid rotation: force refetch once
            _JWKS_CACHE["fetched_at"] = 0.0
            jwks = _get_jwks(jwks_url, fetch)
            key_data = next((k for k in jwks.get("keys", []) if k.get("kid") == kid), None)
        if key_data is None:
            raise HTTPException(status_code=401, detail="Unknown signing key")
        key = PyJWK.from_dict(key_data).key
        result: dict[str, Any] = pyjwt.decode(token, key=key, algorithms=[alg], audience="authenticated")
        return result

    secret = os.environ.get("SUPABASE_JWT_SECRET", "")
    if alg == "HS256" and secret:
        hs_result: dict[str, Any] = pyjwt.decode(token, key=secret, algorithms=["HS256"], audience="authenticated")
        return hs_result

    return _introspect(token, fetch)


PUBLIC_PATHS: frozenset[str] = frozenset({"/api/health"})
"""What an internet-facing engine answers without identity: the tunnel's and the gateway's liveness check, which
carries no state and names nothing. Everything else under `/api` is somebody's."""

DEMO_ANON_CAPABLE: frozenset[str] = frozenset({"/api/v1/analyse-facts", "/api/nyaya/registry/contracts"})
"""The only routes a deployment may open to anonymous callers (operator, 2026-09-23: every feature demoable without
login on the 5090-hosted deployment). Both are stateless for the caller: analyse-facts is rate- and concurrency-
limited by configs/partner_api.yaml and writes only the engine's own audit trail; the contract list is read-only.
Which of them are open is the deployment's choice (`PRAVRUDHI_DEMO_ANON_PATHS`); widening this set is a code
change with its own review."""


def _demo_anon_env() -> frozenset[str]:
    raw = os.environ.get("PRAVRUDHI_DEMO_ANON_PATHS", "")
    return frozenset(p.strip() for p in raw.split(",") if p.strip())


def demo_anon_paths() -> frozenset[str]:
    """The routes this deployment answers without a token: the env's list, never more than `DEMO_ANON_CAPABLE`."""
    return _demo_anon_env() & DEMO_ANON_CAPABLE


def _identity_header_name() -> str:
    """The name of the header from which to read the user token.

    When PRAVRUDHI_IDENTITY_HEADER is set, read identity from that header instead of Authorization.
    This supports RunPod LB gateways that require Bearer authorization but the engine needs the
    user's Supabase token. The Cloudflare Worker moves the user's Authorization to this header and
    puts the RunPod key in Authorization.
    """
    raw = os.environ.get("PRAVRUDHI_IDENTITY_HEADER", "").strip()
    return raw.lower() if raw else "authorization"


def _with_query_token(conn: HTTPConnection) -> Mapping[str, str]:
    """The request's headers, with `?access_token=` standing in for a missing custom identity header.

    A browser's EventSource cannot set headers, so the run event stream (`/api/runs/{id}/events`) carries the
    session token in the query string instead. When PRAVRUDHI_IDENTITY_HEADER is set, the query param fills
    that header; otherwise it fills Authorization. A provided header always wins (query param is fallback only).
    """
    headers = dict(conn.headers)
    header_name = _identity_header_name()
    if header_name in headers:
        return headers
    token = conn.query_params.get("access_token")
    if token:
        headers[header_name] = f"Bearer {token}"
    return headers


def user_from_headers(headers: Mapping[str, str]) -> User | None:
    """Resolve the caller from request headers, or None when identity is not required and none was sent.

    Reads the bearer token from the header named by PRAVRUDHI_IDENTITY_HEADER if set, otherwise from
    Authorization. Raises 401 in `required` mode when no valid bearer token is present. Shared by the
    per-route dependency and the whole-surface gate so the two can never disagree about who a caller is.
    """
    mode = auth_mode()
    header_name = _identity_header_name()
    auth = headers.get(header_name, "")
    if not auth.lower().startswith("bearer "):
        if mode == AuthMode.REQUIRED:
            raise HTTPException(status_code=401, detail="Missing bearer token")
        return None
    if mode == AuthMode.DISABLED:
        return None
    token = auth.split(" ", 1)[1].strip()
    try:
        claims = verify_token(token)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 — any verification failure is a 401
        raise HTTPException(status_code=401, detail=f"Invalid token: {exc}") from exc
    return User(id=str(claims.get("sub")), email=claims.get("email"), role=str(claims.get("role") or "authenticated"))


async def current_user(request: Request) -> User | None:
    """FastAPI dependency: who sent this request, or None when identity is not required.

    This is identity, not authorization for state changes: `localguard`'s local token remains the sole
    CSRF guard on POST/PUT/DELETE in every deployment shape. A route depends on this to know *who*, never
    to decide *whether* — that decision stays with `localguard` (same-origin + token) and, in `required`
    mode, with the whole-surface gate `RequireIdentity` installs, which refuses an anonymous caller before
    any route runs.
    """
    headers = _with_query_token(request)
    header_name = _identity_header_name()
    if header_name not in headers and request.url.path in demo_anon_paths():
        return None
    return user_from_headers(headers)


class RequireIdentity:
    """ASGI middleware: in `required` mode, no `/api` path outside `PUBLIC_PATHS` answers an anonymous caller.

    Until ADR-0051 addendum 3 put an engine on the internet, `required` only meant "a route that asks who is
    calling gets an answer or a 401"; routes that never asked — the state, the ledger, the requests store —
    answered anyone who found the port. The gate closes that: a preflight (no token by design) passes so the
    browser can learn the CORS answer, a websocket without a token is closed with 4401, and the health check
    stays open for the tunnel. It is installed inside the CORS layer so a 401 reaches the browser with its
    origin headers rather than as an opaque network error.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] in ("http", "websocket") and auth_mode() == AuthMode.REQUIRED:
            path: str = scope.get("path", "")
            conn = HTTPConnection(scope)
            header_name = _identity_header_name()
            anon_ok = path in demo_anon_paths() and header_name not in conn.headers
            if (
                path.startswith("/api/")
                and path not in PUBLIC_PATHS
                and not anon_ok
                and scope.get("method") != "OPTIONS"
            ):
                try:
                    user_from_headers(_with_query_token(conn))
                except HTTPException as exc:
                    if scope["type"] == "http":
                        await JSONResponse({"detail": exc.detail}, status_code=exc.status_code)(scope, receive, send)
                    else:
                        await send({"type": "websocket.close", "code": 4401, "reason": str(exc.detail)})
                    return
        await self.app(scope, receive, send)


CurrentUserDep = Depends(current_user)

guard_boot()
