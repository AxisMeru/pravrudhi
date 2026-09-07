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

from pravrudhi.api.identity import AuthMode, User, auth_mode


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
    "/api/requests", "/api/requests/{rid}", "/api/requests/{rid}/advance",
    "/api/requests/{rid}/criteria/{index}/evidence",
    "/api/sandboxes",
    "/api/search",
    "/api/swarm", "/api/swarm/live",
    "/api/update/apply", "/api/update/rollback",
})

# What the product is. A user's own goals, workspaces, conversation, memory, keys and models, plus the plain
# facts about the engine they are running and whether an update is waiting for them.
USER_FACING: frozenset[str] = frozenset({
    "/api/app-token",
    "/api/chat", "/api/chat/stream", "/api/chat/threads", "/api/chat/threads/{thread_id}",
    "/api/doctor",
    "/api/health", "/api/status",
    "/api/me",
    "/api/memory", "/api/memory/notes",
    "/api/notifications", "/api/notifications/read",
    "/api/objectives", "/api/objectives/plan-preview", "/api/objectives/{oid}",
    "/api/objectives/{oid}/loom", "/api/objectives/{oid}/plan", "/api/objectives/{oid}/subagents",
    "/api/providers", "/api/providers/{provider_id}/key",
    "/api/recipes",
    "/api/tools",
    "/api/update", "/api/update/config", "/api/update/last-check",
    "/api/workspaces",
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

    gated: list[str] = []
    for route in walk(app.routes):  # type: ignore[attr-defined]
        if route.path in ADMIN_ONLY:
            route.dependencies.append(Depends(_admin_dependency))
            route.dependant = None  # type: ignore[assignment]
            gated.append(route.path)
    return sorted(set(gated))


async def _admin_dependency(request: Request) -> None:
    """Resolve the caller the same way every other route does, then apply the allowlist."""
    from pravrudhi.api.identity import current_user

    require_admin(await current_user(request))


__all__ = [
    "ADMIN", "ADMIN_ENV", "ADMIN_ONLY", "USER", "USER_FACING", "Role",
    "admin_ids", "gate", "is_admin", "require_admin", "role_of",
]
