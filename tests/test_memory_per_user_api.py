"""Two signed-in non-admin users' memory over the real API, on a deployment with no Supabase service key -
this project's standing rule, since the operator forbids issuing one. Before this fix every one of them fell
through to `FileMemoryStore(root)`: one shared memory file across every product user, on the shared engine root.

Every assertion here reads a real file on disk or a real API response; nothing is mocked.
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


def _client(tmp_path: Path, monkeypatch) -> tuple[TestClient, Path]:
    monkeypatch.setenv("PRAVRUDHI_AUTH", "optional")
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_JWT_SECRET", _SECRET)
    monkeypatch.delenv("SUPABASE_SERVICE_KEY", raising=False)  # the standing rule: never issued
    monkeypatch.delenv("VERCEL", raising=False)
    monkeypatch.delenv("RENDER", raising=False)
    root = tmp_path / "engine-root"
    init_project(root)
    return TestClient(build_app(root), headers=H), root


def test_two_signed_in_non_admin_users_notes_are_invisible_to_each_other_and_absent_from_root(
    tmp_path: Path, monkeypatch
) -> None:
    c, root = _client(tmp_path, monkeypatch)
    tok = {TOKEN_HEADER: app_token(root)}
    alice = {"authorization": f"Bearer {_token('user-alice', 'alice@example.com')}"}
    bob = {"authorization": f"Bearer {_token('user-bob', 'bob@example.com')}"}

    created = c.post("/api/memory/notes", json={"text": "alice's own note", "source": "user"}, headers={**alice, **tok})
    assert created.status_code == 200, created.text
    c.post("/api/memory/notes", json={"text": "bob's own note", "source": "user"}, headers={**bob, **tok})

    alice_notes = [n["text"] for n in c.get("/api/memory", headers=alice).json()["notes"]]
    bob_notes = [n["text"] for n in c.get("/api/memory", headers=bob).json()["notes"]]
    assert alice_notes == ["alice's own note"]
    assert bob_notes == ["bob's own note"]

    root_notes_dir = root / ".pravrudhi" / "memory"
    assert not root_notes_dir.exists() or not (root_notes_dir / "notes.jsonl").exists(), (
        "neither user's note may land in the shared engine root"
    )


def test_a_non_admin_user_cannot_revise_or_delete_the_others_note(tmp_path: Path, monkeypatch) -> None:
    c, root = _client(tmp_path, monkeypatch)
    tok = {TOKEN_HEADER: app_token(root)}
    alice = {"authorization": f"Bearer {_token('user-alice', 'alice@example.com')}"}
    bob = {"authorization": f"Bearer {_token('user-bob', 'bob@example.com')}"}

    note = c.post(
        "/api/memory/notes", json={"text": "alice's secret", "source": "user"}, headers={**alice, **tok}
    ).json()

    refused = c.patch(f"/api/memory/notes/{note['id']}", json={"text": "hijacked"}, headers={**bob, **tok})
    assert refused.status_code == 404, "another user's note id must read as not found, not as forbidden"
    assert c.delete(f"/api/memory/notes/{note['id']}", headers={**bob, **tok}).status_code == 404
    assert c.get("/api/memory", headers=alice).json()["notes"][0]["text"] == "alice's secret"


def test_an_anonymous_caller_is_refused_not_given_the_shared_root(tmp_path: Path, monkeypatch) -> None:
    c, _root = _client(tmp_path, monkeypatch)
    refused = c.get("/api/memory")
    assert refused.status_code == 400, refused.text


def test_an_admin_account_still_reads_the_engine_root(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PRAVRUDHI_ADMINS", "admin@example.com")
    c, root = _client(tmp_path, monkeypatch)
    tok = {TOKEN_HEADER: app_token(root)}
    admin = {"authorization": f"Bearer {_token('admin-1', 'admin@example.com')}"}

    c.post("/api/memory/notes", json={"text": "operator's own note", "source": "user"}, headers={**admin, **tok})

    root_notes = (root / ".pravrudhi" / "memory" / "notes.jsonl").read_text()
    assert "operator's own note" in root_notes


def test_every_memory_route_is_present_and_none_answers_an_anonymous_caller_from_root(
    tmp_path: Path, monkeypatch
) -> None:
    """Memory's own route-walk guard: unlike `/api/objectives*` (tests/test_objectives_workspace_scoping.py),
    memory carries no `workspace` query parameter at all to check for in the OpenAPI schema - it is addressed
    per signed-in user, not per project, and `user: User | None = CurrentUserDep` is a FastAPI dependency, so it
    never shows up as a schema parameter either way. The guard that actually matters here is behavioural: every
    route in the family answers an anonymous caller with a refusal, never with the engine's own root, which is
    exactly what happened before this fix and cannot be seen from the schema alone."""
    c, root = _client(tmp_path, monkeypatch)
    tok = {TOKEN_HEADER: app_token(root)}
    paths = c.app.openapi()["paths"]  # type: ignore[attr-defined]
    memory_paths = {p: ops for p, ops in paths.items() if p.startswith("/api/memory")}
    assert set(memory_paths) == {"/api/memory", "/api/memory/notes", "/api/memory/notes/{note_id}"}

    for path, ops in memory_paths.items():
        for method in ops:
            request = getattr(c, method)
            kwargs: dict = {"headers": tok} if method != "get" else {}
            if method in ("post", "patch"):
                kwargs["json"] = {"text": "x", "source": "user"}
            response = request(path.replace("{note_id}", "does-not-exist"), **kwargs)
            assert response.status_code == 400, f"{method.upper()} {path} answered an anonymous caller: {response.text}"
