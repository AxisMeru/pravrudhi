"""Tell the operator apart from the people the product is for.

Pravrudhi has two audiences that want opposite things from the same engine. A user brings their own model, their
own agent or their own repository, and wants the loop pointed at *that*. The operator builds Pravrudhi itself —
the kernel, the search, the nights that make the engine better — and that work has no place in a user's product.
Until now the engine could not tell them apart: the only identity it carried was Supabase's `authenticated`
claim, which every signed-in account has.

The role is resolved from an allowlist held on the machine, never from anything the caller sends. A token cannot
claim to be an operator, because the claim is not consulted; the account's id or address is compared against a
list the operator set. That asymmetry is the whole security argument, and it is why the forged-claim case has a
test of its own.

Everyone not on the list is a user, and a user is not a degraded operator. It is the audience the product exists
for, and the surfaces it reaches are the ones worth building well.

One case deserves care. With authentication switched off there is nobody to identify, which is how a single
operator runs this on their own machine — and `identity.guard_boot` already refuses that mode on a deployment
platform, so it cannot mean an open door on the public internet. In that configuration the local caller is the
operator by construction. With authentication on, an anonymous caller is emphatically not.
"""

from __future__ import annotations

import os
from enum import StrEnum

from fastapi import HTTPException, Request
from starlette.requests import HTTPConnection
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from pravrudhi.api.identity import PUBLIC_PATHS, AuthMode, User, _with_query_token, auth_mode, user_from_headers


class Role(StrEnum):
    ADMIN = "admin"
    USER = "user"


ADMIN = Role.ADMIN
USER = Role.USER

ADMIN_ENV = "PRAVRUDHI_ADMINS"
"""Comma-separated Supabase user ids or email addresses. Empty, or unset, means this machine names no operator,
and then nobody is one: defaulting the other way would turn a forgotten variable into an open door."""


def admin_ids() -> frozenset[str]:
    """The allowlist, lowercased and stripped, with blank entries dropped."""
    raw = os.environ.get(ADMIN_ENV, "")
    return frozenset(part.strip().lower() for part in raw.split(",") if part.strip())


def role_of(user: User | None) -> Role:
    """Which audience this caller belongs to.

    `user.role` is deliberately ignored. It carries whatever the identity provider put in the token, and a
    caller who can influence their own token could otherwise promote themselves by asking.
    """
    if user is None:
        return ADMIN if auth_mode() is AuthMode.DISABLED else USER
    allowed = admin_ids()
    if not allowed:
        return USER
    candidates = {user.id.strip().lower()}
    if user.email:
        candidates.add(user.email.strip().lower())
    return ADMIN if candidates & allowed else USER


def is_admin(user: User | None) -> bool:
    return role_of(user) is ADMIN


ACCESS_VALUES: frozenset[str] = frozenset({"admin", "member", "none"})
"""What `/api/me` reports under `access`, and the only values it may report."""


def access_for(user: User | None) -> str:
    """The three-valued answer a client actually needs, over `role_of`'s two-valued authorization split.

    `role_of` collapses "signed in, not on the allowlist" and "nobody signed in at all" to the same `USER`,
    which is correct for gating -- both are refused an admin surface alike -- and wrong for a reader of `/me`,
    who is asking a different question: is anyone here it can name. An anonymous caller on a deployed,
    authenticated install is not "a member who happens not to be an admin"; it is nobody. The one case where
    `user is None` does name somebody is authentication switched off, where `role_of` already resolves the
    local caller to the operator by construction, and that still reports `admin` here, unchanged.
    """
    if role_of(user) is ADMIN:
        return "admin"
    return "none" if user is None else "member"


def require_admin(user: User | None) -> User | None:
    """Let an operator through, refuse anyone else, and say nothing about who is on the list."""
    if role_of(user) is ADMIN:
        return user
    raise HTTPException(status_code=403, detail="This surface belongs to the engine's operator.")


# Which surfaces belong to the operator. These are the ones about Pravrudhi improving *itself*: the ledger and
# the nights behind it, the swarm that builds the engine, the promotion inbox, the fleet it runs on, and the
# evidence for its own hypotheses. None of it belongs in a product a user opens to improve their own work.
#
# The list is exact paths rather than prefixes, and `test_roles.py` fails if any route is missing from exactly
# one of these two sets. That is deliberate: a new route must be classified by whoever adds it, instead of
# defaulting to public because nobody thought about it.
ADMIN_ONLY: frozenset[str] = frozenset({
    "/api/agent-trace",
    "/api/agents", "/api/agents/cooldowns",
    "/api/appetite",
    "/api/benchmarks",
    "/api/candidates", "/api/candidates/{cid}",
    "/api/diffs", "/api/diffs/{task_id}",
    "/api/evidence/{name}",
    "/api/external",
    "/api/fleet", "/api/hosts",
    "/api/h1/{track}/{nights}",
    "/api/health-state", "/api/svasthya",
    "/api/heartbeat",
    "/api/inbox", "/api/inbox/sign",
    "/api/jobs", "/api/jobs/{job_id}/cancel",
    "/api/nights",
    "/api/observations",
    "/api/parity",
    "/api/routes",
    "/api/requests", "/api/requests/{rid}", "/api/requests/{rid}/advance",
    "/api/requests/{rid}/criteria/{index}/evidence",
    "/api/sandboxes",
    "/api/search",
    "/api/swarm", "/api/swarm/live",
})

# Operator-only in EVERY edition, product included (P0 hotfix, R2): these surfaces are engine-wide or spend the
# engine's own hardware and secrets, so a signed-in account that merely reaches the engine must not use them. Unlike
# ADMIN_ONLY, which a product install does not have at all (404), these exist in both editions and answer a
# non-admin 403 (and an anonymous caller 401 where identity is required). With authentication off the local caller
# is the operator by construction (`role_of`), so a single-operator install is unaffected.
_ADMIN_IN_BOTH_EDITIONS_BASE: frozenset[str] = frozenset({
    # The engine's local write token: reading it let any signed-in caller satisfy the local write guard.
    "/api/app-token",
    # Starting, stopping and watching a night spawns `python -m pravrudhi ...` on the engine host.
    "/api/runs", "/api/runs/{run_id}", "/api/runs/{run_id}/stop", "/api/runs/{run_id}/events",
    # Applying or rolling back an engine update replaces the running engine.
    "/api/update/apply", "/api/update/rollback",
})

LEGAL_MVP_CLOSED: frozenset[str] = frozenset({
    # Legal design-partner MVP (Lead-2 decision on #525, mode B, 6 Oct 2026): a surface the product hides must be CLOSED,
    # not only absent from the navigation (the app serves every route to anyone who types the URL). A signed-in member
    # gets 403, an anonymous caller 401, the operator passes. What stays member-facing is the legal surface: Matters
    # (/api/v1/analyse-facts and its jobs and status), the statute corpus, the contract list Matters needs, org keys and
    # usage (self-gated by `tenancy.require_tenancy_admin`), and the plain health, identity and workspace routes.
    # Provider keys (BYOK):
    "/api/providers", "/api/providers/{provider_id}/key",
    # Chat:
    "/api/chat", "/api/chat/stream", "/api/chat/threads", "/api/chat/threads/{thread_id}",
    # Memory:
    "/api/memory", "/api/memory/notes", "/api/memory/notes/{note_id}",
    # Objectives and their plans (the dispatch of a plan to host agents was already operator-only for writes):
    "/api/objectives", "/api/objectives/plan-preview", "/api/objectives/{oid}", "/api/objectives/{oid}/loom",
    "/api/objectives/{oid}/plan", "/api/objectives/{oid}/subagents",
    # Models, recipes, tools, and the vendor panel behind the vendor picker:
    "/api/models", "/api/recipes", "/api/tools", "/api/panel/vendors",
    # Nyaya's free-form multi-vendor Ask, the "Audit an answer" tab, the vendor list and the manual contract audit.
    # (`/api/nyaya/registry/contracts` stays open: Matters lists its contracts from it and it is anonymous-demo capable.)
    "/api/nyaya/ask", "/api/nyaya/asks", "/api/nyaya/audit", "/api/nyaya/vendors",
    "/api/nyaya/registry/{contract_id}/elements", "/api/nyaya/registry/check",
    # The engine's own host and build facts (R2's #300 review: seven hide-list routes were still open). `/api/doctor` names
    # the GPU, driver, docker path and missing repo files; `/api/workspaces` returns the server-side workspace path;
    # `/api/update*` returns the build's git describe and the update channel; notifications are the operator's record.
    "/api/doctor", "/api/workspaces",
    "/api/notifications", "/api/notifications/read",
    "/api/update", "/api/update/config", "/api/update/last-check",
})

ADMIN_IN_BOTH_EDITIONS: frozenset[str] = _ADMIN_IN_BOTH_EDITIONS_BASE | LEGAL_MVP_CLOSED

# A top-level route (not one inside an included router) cannot have its dependant rebuilt after the fact, so the
# one such route in `ADMIN_IN_BOTH_EDITIONS` declares the dependency where it is registered.
ADMIN_GATED_AT_REGISTRATION: frozenset[str] = frozenset({"/api/app-token"})

# Routes whose READ is the product's but whose WRITE is engine-wide: the same path under two methods. Only the
# methods below the safe set are operator-only; `GET /api/update/config` stays user-facing.
ADMIN_WRITES_IN_BOTH_EDITIONS: frozenset[str] = frozenset()
SAFE_METHODS: frozenset[str] = frozenset({"GET", "HEAD", "OPTIONS"})

# What the product is. A user's own goals, workspaces, conversation, memory, keys and models, plus the plain
# facts about the engine they are running and whether an update is waiting for them.
USER_FACING: frozenset[str] = frozenset({
    # prabhasa-nyaya: the statute corpus the engine reads (the legal surface the product keeps). The multi-vendor Ask,
    # the audit, the vendor list and the manual contract audit moved to ADMIN_IN_BOTH_EDITIONS above (#525, mode B).
    "/api/nyaya/corpus",
    # The registry's contract LIST stays open: Matters (the product's main surface) lists its contracts from it, and it is
    # one of the two routes a deployment may open to anonymous demo callers (identity.DEMO_ANON_CAPABLE).
    "/api/nyaya/registry/contracts",
    # L4 partner API (LEG-PLAN-2026-09-23): the agentic loop over the same registry contracts, facts in.
    "/api/v1/analyse-facts", "/api/v1/status", "/api/v1/verify-citations",
    "/api/v1/analyse-facts/jobs", "/api/v1/analyse-facts/jobs/{job_id}", "/api/v1/audit",
    # L4 tenancy (application/tenancy.py): org and API-key provisioning. Not admin-only in the ADMIN_ONLY
    # sense above -- these are not surfaces about Pravrudhi improving itself, they are how a partner account
    # is set up -- so each route gates itself internally (partner.py) rather than disappearing entirely on a
    # product install the way ADMIN_ONLY routes do. The gate is `tenancy.require_tenancy_admin`, deliberately
    # NOT this module's `require_admin`/`is_admin`: those resolve an anonymous caller to ADMIN whenever
    # PRAVRUDHI_AUTH is left at its `disabled` default, which is correct for engine-improvement surfaces on a
    # local single-operator machine and would otherwise let anyone reach a self-hosted deployment's demo
    # config and mint a partner's first live API key.
    "/api/v1/orgs", "/api/v1/orgs/{org_id}/keys", "/api/v1/orgs/{org_id}/keys/{key_id}/revoke",
    "/api/v1/orgs/{org_id}/usage", "/api/v1/orgs/{org_id}/usage/summary",
    "/api/health", "/api/status",
    # RunPod serverless load-balancer liveness (outside /api; no identity asked, carries no state).
    "/ping",
    "/api/me",
    "/api/messaging/telegram",
})


def gate(app: object) -> list[str]:
    """Attach the operator check to every admin-only route on a built application.

    Doing it here, over the finished route table, rather than at each declaration means the classification lives
    in one readable list instead of scattered across sixty decorators, and a route that nobody classified is
    caught by a test rather than shipped open.
    """
    from fastapi import Depends
    from fastapi.routing import APIRoute, APIRouter

    def walk(routes: object) -> list[APIRoute]:
        out: list[APIRoute] = []
        for route in routes:  # type: ignore[attr-defined]
            if isinstance(route, APIRoute):
                out.append(route)
            else:
                sub = getattr(route, "original_router", None)
                if isinstance(sub, APIRouter):
                    out.extend(walk(sub.routes))
        return out

    from pravrudhi.api.edition import is_studio_engine

    # A product install does not merely hide these surfaces from the wrong caller — it does not have them.
    # Role alone could not express that: with authentication off a local caller is the operator by
    # construction, so the operator's own product install served every surface belonging to Pravrudhi
    # improving itself. The edition is a property of the install, not of who is asking, which is why it can
    # answer a question role cannot.
    studio = is_studio_engine()
    gated: list[str] = []
    for route in walk(app.routes):  # type: ignore[attr-defined]
        if route.path in ADMIN_ONLY:
            route.dependencies.append(Depends(admin_dependency if studio else _not_in_this_edition))
            route.dependant = None  # type: ignore[assignment]
            gated.append(route.path)
        elif route.path in ADMIN_GATED_AT_REGISTRATION:
            gated.append(route.path)  # its own registration already carries the dependency (see server.py)
        elif route.path in ADMIN_IN_BOTH_EDITIONS or (
            route.path in ADMIN_WRITES_IN_BOTH_EDITIONS and not (route.methods and route.methods <= SAFE_METHODS)
        ):
            route.dependencies.append(Depends(admin_dependency))
            route.dependant = None  # type: ignore[assignment]
            gated.append(route.path)
    return sorted(set(gated))


async def _not_in_this_edition() -> None:
    """404 rather than 403: on a product install this surface does not exist, and saying "forbidden" would
    advertise an engine-improvement surface to someone whose product simply does not have one."""
    raise HTTPException(status_code=404, detail="Not Found")


async def admin_dependency(request: Request) -> None:
    """Resolve the caller the same way every other route does, then apply the allowlist."""
    from pravrudhi.api.identity import current_user

    require_admin(await current_user(request))


__all__ = [
    "ACCESS_VALUES", "ADMIN", "ADMIN_ENV", "ADMIN_IN_BOTH_EDITIONS", "LEGAL_MVP_CLOSED", "ADMIN_ONLY",
    "ADMIN_WRITES_IN_BOTH_EDITIONS",
    "USER", "USER_FACING", "Role",
    "access_for", "admin_ids", "gate", "is_admin", "require_admin", "role_of",
]


STUDIO_SCHEMA_PATHS: frozenset[str] = frozenset({"/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"})
"""The generated API schema and its docs pages: they describe every route, so they are the operator's in every edition
(an anonymous caller is refused 401 and a signed-in non-admin 403 whenever authentication is on)."""


class RequireStudioAdmin:
    """ASGI middleware: in the Studio edition, no `/api` route outside `PUBLIC_PATHS` answers a non-admin.

    The product and Studio share one identity provider, so any signed-in product account can reach a Studio engine
    through its tunnel. Per-route role guards only cover the routes that remembered to ask; this is the whole-surface
    gate, evaluated before any route code runs (no vendor call, no ledger write, nothing a handler does).

    * Edition: the one resolved edition (`pravrudhi.deployment.resolved_edition`, shared with `engine_edition` and
      `tenant_vendors`) must be `studio`. An unset or any other
      value is not a Studio gate, so a development checkout and the product keep their behaviour. Loopback does
      not matter here: a loopback Studio is gated the same.
    * Authentication off (`PRAVRUDHI_AUTH` disabled, the local operator's own machine): nobody to identify, the local
      caller is the operator by construction (`role_of`), so it passes, exactly as before.
    * Otherwise the caller is resolved from the same headers every route uses (`PRAVRUDHI_IDENTITY_HEADER` and the
      `?access_token=` query fallback included, and websockets), and must be on `PRAVRUDHI_ADMINS` by id or email.
      No identity is 401 (websocket close 4401); a signed-in non-admin is 403 (close 4403). An empty or unset
      allowlist names nobody, so every signed-in caller is refused.
    * A partner-key caller (`X-Pravrudhi-Api-Key`) is not an operator: 403 even with no bearer token.
    * The schema and docs pages (`STUDIO_SCHEMA_PATHS`: `/openapi.json`, `/docs`, `/redoc`) are gated like `/api`.
    * Preflight (OPTIONS), `PUBLIC_PATHS` (the liveness check) and every other non-`/api` path (the static
      interface and `/ping`, which carry no state) stay open.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] in ("http", "websocket") and auth_mode() is not AuthMode.DISABLED:
            path: str = scope.get("path", "")
            # The schema and docs pages describe every route, so they are the operator's in EVERY edition (#525 mode B,
            # C11): a product install used to serve them to anyone. The rest of /api is gated only on Studio.
            gated = path in STUDIO_SCHEMA_PATHS or (_studio_edition() and path.startswith("/api/"))
            if gated and path not in PUBLIC_PATHS and scope.get("method") != "OPTIONS":
                refusal = _studio_refusal(HTTPConnection(scope))
                if refusal is not None:
                    status, detail = refusal
                    if scope["type"] == "http":
                        await JSONResponse({"detail": detail}, status_code=status)(scope, receive, send)
                    else:
                        await send({"type": "websocket.close", "code": 4400 + status % 100, "reason": detail})
                    return
        await self.app(scope, receive, send)


def _studio_edition() -> bool:
    from pravrudhi.deployment import resolved_edition

    return resolved_edition() == "studio"


def _studio_refusal(conn: HTTPConnection) -> tuple[int, str] | None:
    from pravrudhi.application.tenancy import API_KEY_HEADER

    if API_KEY_HEADER in conn.headers:
        return 403, "This surface belongs to the engine's operator."
    try:
        user = user_from_headers(_with_query_token(conn))
    except HTTPException as exc:
        return exc.status_code, str(exc.detail)
    if user is None:
        return 401, "Sign in as the engine's operator."
    if not is_admin(user):
        return 403, "This surface belongs to the engine's operator."
    return None
