"""A signed-in non-admin caller's objectives, plans, loom and subagent surfaces must resolve to their own
workspace, never to the engine's own root — the same rule `/api/runs` and `GET /api/objectives` already keep.

Found live on the product engine (2026-09-12): `POST /objectives` had no `workspace` parameter at all and wrote
straight into the engine's own root via the module-level `root` closure, and `GET/POST /objectives/{oid}/plan`,
`/loom` and `/subagents` read the same way. A non-admin user's created objective landed where the operator's own
project lives, did not show up in their own workspace-scoped `GET /objectives`, and sat where any other caller
reading the root could see it. Every assertion here is a real file on disk, not a mock: the bug was exactly that
the write happened, just in the wrong place.
"""

from __future__ import annotations

from pathlib import Path

import jwt
from fastapi.testclient import TestClient

from pravrudhi.api.localguard import TOKEN_HEADER, app_token
from pravrudhi.application.app_serve import build_app
from pravrudhi.application.init import init_project

H = {"host": "127.0.0.1:8008"}
_SECRET = "s3cret-long-enough-for-hs256-testing-purposes"


def _token(sub: str, email: str) -> str:
    return jwt.encode(
        {"sub": sub, "email": email, "role": "authenticated", "aud": "authenticated", "exp": 4102444800},
        _SECRET, algorithm="HS256",
    )


def _client(tmp_path: Path, monkeypatch) -> TestClient:
    monkeypatch.setenv("PRAVRUDHI_AUTH", "optional")
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_JWT_SECRET", _SECRET)
    monkeypatch.setenv("PRAVRUDHI_WORKSPACES", str(tmp_path / "workspaces"))
    monkeypatch.delenv("VERCEL", raising=False)
    monkeypatch.delenv("RENDER", raising=False)
    root = tmp_path / "engine-root"
    init_project(root)
    return TestClient(build_app(root), headers=H)


def _objective_body(oid: str) -> dict:
    return {
        "id": oid, "intent": "improve the thing", "track": oid,
        "benchmarks": [{"id": oid, "tool": "lm-eval", "metric": "accuracy", "direction": "up"}],
    }


def test_a_signed_in_non_admin_creating_an_objective_writes_it_into_their_own_workspace(
    tmp_path: Path, monkeypatch
) -> None:
    c = _client(tmp_path, monkeypatch)
    auth = {"authorization": f"Bearer {_token('user-1', 'u1@example.com')}"}
    tok = {TOKEN_HEADER: app_token(tmp_path / "engine-root")}

    created = c.post(
        "/api/objectives?workspace=mine", json=_objective_body("obj-a"), headers={**auth, **tok},
    )
    assert created.status_code == 200, created.text

    workspace_file = tmp_path / "workspaces" / "user-1" / "mine" / ".pravrudhi" / "objectives" / "obj-a.yaml"
    root_file = tmp_path / "engine-root" / ".pravrudhi" / "objectives" / "obj-a.yaml"
    assert workspace_file.exists(), "the objective must land in the caller's own workspace"
    assert not root_file.exists(), "the objective must never land in the shared engine root"

    listed = c.get("/api/objectives?workspace=mine", headers=auth).json()
    assert [o["id"] for o in listed["objectives"]] == ["obj-a"]


def test_a_signed_in_non_admin_creating_an_objective_with_no_workspace_is_refused_not_written_to_root(
    tmp_path: Path, monkeypatch
) -> None:
    c = _client(tmp_path, monkeypatch)
    auth = {"authorization": f"Bearer {_token('user-2', 'u2@example.com')}"}
    tok = {TOKEN_HEADER: app_token(tmp_path / "engine-root")}

    refused = c.post("/api/objectives", json=_objective_body("obj-b"), headers={**auth, **tok})
    assert refused.status_code == 400, refused.text

    root_file = tmp_path / "engine-root" / ".pravrudhi" / "objectives" / "obj-b.yaml"
    assert not root_file.exists(), "a refused write must leave nothing behind, not fall back to the shared root"


def test_an_admin_creating_an_objective_with_no_workspace_still_uses_the_engine_root(
    tmp_path: Path, monkeypatch
) -> None:
    """Unchanged behaviour for the one caller for whom the engine root always was the project: the operator,
    running locally with no workspace named, exactly as before this fix."""
    monkeypatch.setenv("PRAVRUDHI_ADMINS", "admin@example.com")
    c = _client(tmp_path, monkeypatch)
    auth = {"authorization": f"Bearer {_token('admin-1', 'admin@example.com')}"}
    tok = {TOKEN_HEADER: app_token(tmp_path / "engine-root")}

    created = c.post("/api/objectives", json=_objective_body("obj-admin"), headers={**auth, **tok})
    assert created.status_code == 200, created.text
    root_file = tmp_path / "engine-root" / ".pravrudhi" / "objectives" / "obj-admin.yaml"
    assert root_file.exists()


def test_plan_loom_and_subagents_read_the_callers_own_workspace_not_the_root(tmp_path: Path, monkeypatch) -> None:
    c = _client(tmp_path, monkeypatch)
    auth = {"authorization": f"Bearer {_token('user-3', 'u3@example.com')}"}
    tok = {TOKEN_HEADER: app_token(tmp_path / "engine-root")}
    c.post("/api/objectives?workspace=mine", json=_objective_body("shared-oid"), headers={**auth, **tok})

    for path in (
        "/api/objectives/shared-oid/plan", "/api/objectives/shared-oid/loom", "/api/objectives/shared-oid/subagents",
    ):
        found = c.get(f"{path}?workspace=mine", headers=auth)
        assert found.status_code == 200, f"{path}: {found.text}"
        missing_in_root = c.get(path, headers=auth)  # no workspace: must not fall back to reading root
        assert missing_in_root.status_code in (400, 404), f"{path} answered from root without a workspace"

    started = c.post("/api/objectives/shared-oid/subagents?workspace=mine", headers={**auth, **tok})
    assert started.status_code == 200, started.text


def test_every_objectives_route_that_touches_project_state_takes_a_workspace_parameter(tmp_path: Path, monkeypatch) -> None:
    """The mechanical guard against this exact regression shape: a route added to this family without a
    `workspace` parameter has no way to be resolved into anything but the engine's own root, whatever its
    handler does internally. `plan-preview` is exempt — it compiles a draft that is never persisted and reads
    only the global recipe library, not a caller's project."""
    c = _client(tmp_path, monkeypatch)
    paths = c.app.openapi()["paths"]  # type: ignore[attr-defined]
    exempt = {("/api/objectives", "get"), ("/api/objectives/plan-preview", "post")}
    missing = []
    for path, ops in paths.items():
        if not path.startswith("/api/objectives"):
            continue
        for method, op in ops.items():
            if (path, method) in exempt:
                continue
            params = {p["name"] for p in op.get("parameters", [])}
            if "workspace" not in params:
                missing.append(f"{method.upper()} {path}")
    assert missing == [], f"routes with no workspace parameter, unreachable from any caller's own project: {missing}"
