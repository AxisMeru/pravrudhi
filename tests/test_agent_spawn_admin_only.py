"""#257: every route that starts host coding agents (codex, claude-code, orca, opencode, hosted) is the operator's.

`POST /api/objectives/{oid}/subagents` was user-facing: any signed-in account with a workspace could start agents under
the engine process's own CLI logins. It is now operator-only in BOTH editions; the dispatch-board job routes were already
operator-only (no such route on a product install, admin-only on Studio) and are pinned here too. A refused call
spawns nothing."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from pravrudhi.api import identity
from pravrudhi.api.localguard import TOKEN_HEADER, app_token
from pravrudhi.api.server import create_app
from tests.test_objectives_workspace_scoping import _objective_body

TOKENS = {
    "admin": {"sub": "op-1", "email": "op@example.com", "role": "authenticated"},
    "plain": {"sub": "u-2", "email": "someone@example.com", "role": "authenticated"},
}
EDITIONS = ["product", "studio"]
JOB = {"title": "t", "brief": "do a thing", "allowed_paths": ["src/x.py"]}


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    for v in ("PRAVRUDHI_EDITION", "PRAVRUDHI_STUDIO_LOOPBACK_ONLY", "PRAVRUDHI_IDENTITY_HEADER"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("PRAVRUDHI_AUTH", "required")
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("PRAVRUDHI_ADMINS", "op-1")
    monkeypatch.setattr(identity, "verify_token", lambda token, **_k: TOKENS[token])


@pytest.fixture
def spawned(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Replace every place that would start an agent with a recorder: reaching one is the failure."""
    from pravrudhi.agents import registry
    from pravrudhi.application import dispatchboard, subagents

    seen: list[str] = []
    monkeypatch.setattr(registry, "build_agent", lambda *a, **k: seen.append("build_agent") or None)
    monkeypatch.setattr(subagents, "dispatch_plan", lambda *a, **k: seen.append("dispatch_plan") or [])
    monkeypatch.setattr(dispatchboard, "run_next", lambda *a, **k: seen.append("run_next"))
    return seen


def _client(root: Path, edition: str, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("PRAVRUDHI_EDITION", edition)
    return TestClient(create_app(root), base_url="http://localhost", raise_server_exceptions=False)


def _call(c: TestClient, root: Path, method: str, path: str, body: Any, who: str | None) -> Any:
    headers = {TOKEN_HEADER: app_token(root)}
    if who:
        headers["Authorization"] = f"Bearer {who}"
    return c.request(method, path, headers=headers, json=body)


@pytest.mark.parametrize("edition", EDITIONS)
def test_dispatching_a_plan_is_401_anonymous_403_non_admin_and_200_for_the_admin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, spawned: list[str], edition: str
) -> None:
    c = _client(tmp_path, edition, monkeypatch)
    assert _call(c, tmp_path, "POST", "/api/objectives", _objective_body("obj-1"), "admin").status_code == 200
    path = "/api/objectives/obj-1/subagents"
    assert _call(c, tmp_path, "POST", path, None, None).status_code == 401
    assert _call(c, tmp_path, "POST", path, None, "plain").status_code == 403
    assert spawned == []  # the refused calls reached no agent code at all
    r = _call(c, tmp_path, "POST", path, None, "admin")
    assert r.status_code == 200 and r.json()["objective"] == "obj-1"


@pytest.mark.parametrize("edition", EDITIONS)
def test_the_dispatch_preview_and_past_runs_are_the_operators_reads_too(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, spawned: list[str], edition: str
) -> None:
    # Was a user-facing read until the legal MVP closed every objectives route to members (#525 mode B).
    c = _client(tmp_path, edition, monkeypatch)
    assert _call(c, tmp_path, "POST", "/api/objectives", _objective_body("obj-1"), "admin").status_code == 200
    assert _call(c, tmp_path, "GET", "/api/objectives/obj-1/subagents", None, "plain").status_code == 403
    assert _call(c, tmp_path, "GET", "/api/objectives/obj-1/subagents", None, "admin").status_code == 200
    assert spawned == []


def test_a_non_admin_cannot_dispatch_into_their_own_workspace_either(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, spawned: list[str]
) -> None:
    c = _client(tmp_path, "product", monkeypatch)
    r = _call(c, tmp_path, "POST", "/api/objectives/obj-1/subagents?workspace=w1", None, "plain")
    assert r.status_code == 403 and spawned == []


@pytest.mark.parametrize("path", ["/api/jobs", "/api/jobs/abc/cancel"])
def test_the_dispatch_board_routes_are_not_reachable_by_a_non_admin_in_any_edition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, spawned: list[str], path: str
) -> None:
    body = JOB if path == "/api/jobs" else None
    product = _client(tmp_path, "product", monkeypatch)
    assert _call(product, tmp_path, "POST", path, body, "plain").status_code in (403, 404)  # no such surface on a product
    assert _call(product, tmp_path, "POST", path, body, None).status_code in (401, 404)
    studio = _client(tmp_path, "studio", monkeypatch)
    assert _call(studio, tmp_path, "POST", path, body, None).status_code == 401
    assert _call(studio, tmp_path, "POST", path, body, "plain").status_code == 403
    assert spawned == []


def test_the_request_cannot_name_the_agent_for_a_plan_dispatch() -> None:
    import inspect

    from pravrudhi.api import server

    src = inspect.getsource(server)
    start = src.index("def objective_dispatch(")
    body = src[start:start + 1200]
    assert "req" not in body.split("def objective_dispatch(")[1].split(")")[0]  # no request body parameter at all


def test_every_host_agent_spawn_path_in_the_api_is_gated() -> None:
    """Every route handler under `src/pravrudhi/api/` (sync or `async def`, in any module) that reaches
    `build_agent`/`dispatch_plan`/`run_next` is one of the two gated routes, so a new spawning route, in any API
    module, fails here until it is reviewed and classified."""
    import ast
    from pathlib import Path

    from pravrudhi.api import roles

    api_dir = Path(roles.__file__).parent
    spawning: set[tuple[str, str]] = set()
    scanned = 0
    for path in sorted(api_dir.glob("*.py")):
        scanned += 1
        for fn in ast.walk(ast.parse(path.read_text())):
            if isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef) and fn.decorator_list:  # a route handler
                names = {n.id for n in ast.walk(fn) if isinstance(n, ast.Name)} | {
                    n.attr for n in ast.walk(fn) if isinstance(n, ast.Attribute)
                }
                if names & {"build_agent", "dispatch_plan", "run_next"}:
                    spawning.add((path.name, fn.name))
    assert scanned >= 10, "the scan should cover every module under api/"
    assert spawning == {("server.py", "submit_job"), ("server.py", "objective_dispatch")}, (
        f"a new host-agent spawning route: {sorted(spawning)}"
    )
    assert "/api/jobs" in roles.ADMIN_ONLY
    assert "/api/objectives/{oid}/subagents" in roles.ADMIN_IN_BOTH_EDITIONS


def test_the_tripwire_itself_sees_async_routes_and_other_modules(tmp_path: Path) -> None:
    """The scan's own logic, on a synthetic module: an `async def` route in another file is found."""
    import ast

    src = (
        "@router.post('/x')\nasync def sneaky():\n"
        "    from pravrudhi.agents.registry import build_agent\n    build_agent(1, 'codex')\n"
    )
    fn = next(n for n in ast.walk(ast.parse(src)) if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef))
    assert isinstance(fn, ast.AsyncFunctionDef) and fn.decorator_list
    names = {n.id for n in ast.walk(fn) if isinstance(n, ast.Name)}
    assert "build_agent" in names
