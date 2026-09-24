"""The anonymous-demo allowance: a deployment may open a short, code-fixed list of routes to callers with no token.

Operator decision (2026-09-23): every feature is demoable without login on the 5090-hosted deployment, with the admin
login kept. In `required` mode only `/api/health` answered anonymously, so POST /api/v1/analyse-facts could not be
demoed at all. `PRAVRUDHI_DEMO_ANON_PATHS` names which of `DEMO_ANON_CAPABLE` a deployment opens; a name outside that
set refuses to start, so a typo or a widened env cannot expose state.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from pravrudhi.api import identity

_DEP = Depends(identity.current_user)


def _gated_app() -> TestClient:
    app = FastAPI()

    @app.post("/api/v1/analyse-facts")
    def analyse(user: identity.User | None = _DEP) -> dict[str, Any]:
        return {"user": None if user is None else user.id}

    @app.get("/api/nyaya/registry/contracts")
    def contracts(user: identity.User | None = _DEP) -> dict[str, Any]:
        return {"user": None if user is None else user.id}

    @app.get("/api/state")
    def state(user: identity.User | None = _DEP) -> dict[str, Any]:
        return {"user": None if user is None else user.id}

    app.add_middleware(identity.RequireIdentity)
    return TestClient(app)


@pytest.fixture
def required(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    monkeypatch.setenv("PRAVRUDHI_AUTH", "required")
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.delenv("PRAVRUDHI_DEMO_ANON_PATHS", raising=False)
    return monkeypatch


def test_unset_keeps_every_route_but_health_behind_identity(required: pytest.MonkeyPatch) -> None:
    c = _gated_app()
    assert c.post("/api/v1/analyse-facts").status_code == 401
    assert c.get("/api/nyaya/registry/contracts").status_code == 401


def test_an_opened_route_answers_an_anonymous_caller_as_nobody(required: pytest.MonkeyPatch) -> None:
    required.setenv("PRAVRUDHI_DEMO_ANON_PATHS", "/api/v1/analyse-facts")
    c = _gated_app()
    r = c.post("/api/v1/analyse-facts")
    assert r.status_code == 200 and r.json() == {"user": None}
    # only the named route opens
    assert c.get("/api/nyaya/registry/contracts").status_code == 401
    assert c.get("/api/state").status_code == 401


def test_a_token_on_an_opened_route_is_still_verified(required: pytest.MonkeyPatch) -> None:
    required.setenv("PRAVRUDHI_DEMO_ANON_PATHS", "/api/v1/analyse-facts")
    c = _gated_app()
    r = c.post("/api/v1/analyse-facts", headers={"Authorization": "Bearer not-a-real-token"})
    assert r.status_code == 401


def test_a_path_outside_the_capable_set_refuses_to_start(required: pytest.MonkeyPatch) -> None:
    required.setenv("PRAVRUDHI_DEMO_ANON_PATHS", "/api/v1/analyse-facts,/api/state")
    with pytest.raises(RuntimeError, match="/api/state"):
        identity.guard_boot()


def test_the_capable_set_is_exactly_the_two_demo_routes() -> None:
    assert frozenset({"/api/v1/analyse-facts", "/api/nyaya/registry/contracts"}) == identity.DEMO_ANON_CAPABLE


def test_the_opened_set_is_refused_on_a_deployment_platform(required: pytest.MonkeyPatch) -> None:
    """VERCEL/RENDER mean a public cloud surface; the allowance is for the operator's own box only."""
    required.setenv("PRAVRUDHI_DEMO_ANON_PATHS", "/api/v1/analyse-facts")
    required.setenv("VERCEL", "1")
    with pytest.raises(RuntimeError, match="PRAVRUDHI_DEMO_ANON_PATHS"):
        identity.guard_boot()
