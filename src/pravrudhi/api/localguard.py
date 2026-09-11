"""Protecting a local engine from the browser the user is also using.

`pravrudhi app` runs an unauthenticated API on localhost that can start GPU work. That combination is the classic
setup for cross-site request forgery: any page a user visits while the engine is running can issue requests to
127.0.0.1, and if the engine answers them the page can start runs, stop runs, or read the user's results. Being
bound to loopback prevents nothing here, because the attacker's code runs inside the user's own browser.

Three defences, each covering what the others do not.

Cross-origin requests are refused by default. The API sends no permissive CORS headers unless the operator names
the origins, so a malicious page cannot read a response even when it can send a request.

The Host header must name a loopback address. Refusing anything else defeats DNS rebinding, where an attacker's
domain resolves to 127.0.0.1 so that the browser believes the request is same-origin.

State-changing requests carry a token. The token is generated on first use, stored readable only by the user, and
served only to a same-origin caller, so a page that can still reach the engine cannot start a run without it. Read
endpoints stay open, because a local dashboard should work without ceremony and reading is not the risk.
"""

from __future__ import annotations

import os
import secrets
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, Response

LOCAL_HOSTS = ("127.0.0.1", "localhost", "[::1]", "::1")
TOKEN_HEADER = "x-pravrudhi-token"


def token_path(root: Path) -> Path:
    return Path(root) / ".pravrudhi" / "app_token"


def app_token(root: Path) -> str:
    """The engine's local token, created on first use with owner-only permissions."""
    p = token_path(root)
    if p.exists():
        text = p.read_text().strip()
        if text:
            return text
    p.parent.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(32)
    p.write_text(token + "\n")
    os.chmod(p, 0o600)
    return token


def hostname_of(value: str) -> str:
    """The bare hostname of a Host header or an origin URL, lowercased, without port or scheme.

    Both sides of the comparison go through this, so a Host header is never matched against a URL by substring.
    An earlier version asked whether any allowed origin *ended with* the requested host, which let a Host of
    "app" match an allowed origin of "https://pravrudhi.vercel.app". Only exact hostname equality is accepted now.
    """
    v = (value or "").strip().lower()
    if "://" in v:
        v = v.split("://", 1)[1]
    v = v.split("/", 1)[0]
    if v.startswith("["):  # bracketed IPv6, e.g. [::1]:8008
        return v.split("]", 1)[0] + "]"
    return v.split(":", 1)[0]


def _host_is_local(host_header: str) -> bool:
    host = hostname_of(host_header)
    return host in LOCAL_HOSTS or host == ""


def same_origin(origin: str, host_header: str) -> bool:
    """Whether this request came from the page this engine itself served.

    A browser sends `Origin` on every request whose method is not GET or HEAD, same-origin ones included. The
    earlier form of the check refused any POST carrying an Origin that was not in the operator's allowlist, and
    that allowlist is empty by default — so the engine refused every state change made from its own interface,
    including starting a run. Comparing the origin's authority against the Host header restores that without
    relaxing anything: a cross-site page cannot forge Origin, and the local token is still required.
    """
    if not origin:
        return True
    o = urlsplit(origin)
    return o.scheme in ("http", "https") and o.netloc == host_header


def permitted_hostnames() -> set[str]:
    """Exact hostnames drawn from the operator's allowed origins."""
    return {hostname_of(o) for o in allowed_origins() if hostname_of(o)}


def allowed_origins() -> list[str]:
    """Origins the operator has explicitly allowed. Empty by default: no cross-origin access at all."""
    raw = os.environ.get("PRAVRUDHI_ALLOWED_ORIGINS", "")
    return [o.strip() for o in raw.split(",") if o.strip()]


class LocalGuard(BaseHTTPMiddleware):
    """Reject non-loopback Host headers, and require the local token for state-changing methods."""

    def __init__(self, app: FastAPI, root: Path, *, enforce: bool = True) -> None:
        super().__init__(app)
        self.root = Path(root)
        self.enforce = enforce

    async def dispatch(self, request: Request, call_next: Any) -> Any:
        if not self.enforce:
            return await call_next(request)
        host = request.headers.get("host", "")
        origin = request.headers.get("origin", "")
        permitted = allowed_origins()
        if not _host_is_local(host) and hostname_of(host) not in permitted_hostnames():
            return JSONResponse(
                {"detail": "refused: the Host header is not a loopback address (DNS-rebinding guard)"}, status_code=421
            )
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            if origin and origin not in permitted and not same_origin(origin, host):
                return JSONResponse({"detail": "refused: cross-origin state change"}, status_code=403)
            supplied = request.headers.get(TOKEN_HEADER, "")
            if not secrets.compare_digest(supplied, app_token(self.root)):
                return JSONResponse(
                    {"detail": f"refused: send the engine's local token in {TOKEN_HEADER} (see .pravrudhi/app_token)"},
                    status_code=401,
                )
        return await call_next(request)


def install(app: FastAPI, root: Path, *, enforce: bool = True) -> None:
    from fastapi.middleware.cors import CORSMiddleware

    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins(),
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        # `authorization` carries the signed-in user's token from a hosted web door (ADR-0051 addendum 3); a
        # browser preflights any request that names it, and an allow-list without it refuses every such call.
        allow_headers=["content-type", "authorization", TOKEN_HEADER, "x-pravrudhi-operator"],
    )
    app.add_middleware(LocalGuard, root=root, enforce=enforce)  # type: ignore[arg-type]

    @app.get("/api/app-token")
    def token(request: Request) -> Response:
        """The local token, for the interface this engine serves. Same-origin callers only."""
        origin = request.headers.get("origin", "")
        host = request.headers.get("host", "")
        if origin and origin not in allowed_origins() and not same_origin(origin, host):
            raise HTTPException(status_code=403, detail="cross-origin callers cannot read the local token")
        return JSONResponse({"token": app_token(root)})
