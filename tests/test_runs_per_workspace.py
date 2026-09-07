"""A run belongs to the project it was started in.

`RunManager` was always per-project: it passes `--root` to the CLI, resolves the next night from that project's
ledger, and runs with it as the working directory. It was simply constructed once, with the engine's own root,
and closed over by every route. So starting work meant starting it on the operator's project, with the
operator's hardware and the operator's keys, whoever asked.

That is why the run routes were held back from the product even after they were mounted. A user could not be
allowed to reach them, so the desktop application could sign in, choose a workspace and set a band, and still
had nothing that could begin anything.

One manager per project root fixes it, and the refusal that already governs everything else governs this: the
operator with no workspace named gets the engine's project, a user must name theirs, and nobody falls back to
somebody else's.

The concurrency rule is deliberately per project rather than per machine. One engine refusing a second run
protects one GPU; two users each running in their own workspace is the product working as intended, and the
hardware limit belongs to the band's spend ceiling rather than to a lock in the run manager.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pravrudhi.api.identity import User
from pravrudhi.api.runs import manager_for, managers_for_testing


def _user(uid: str = "u-2") -> User:
    return User(id=uid, email="someone@example.com", role="authenticated")


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PRAVRUDHI_WORKSPACES", str(tmp_path / "workspaces"))
    monkeypatch.delenv("PRAVRUDHI_ADMINS", raising=False)
    managers_for_testing().clear()


class TestOneManagerPerProject:
    def test_the_operator_gets_the_engines_own_project(self, tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        monkeypatch.setenv("PRAVRUDHI_ADMINS", "u-1")
        engine = tmp_path / "engine"
        engine.mkdir()
        assert manager_for(_user("u-1"), None, engine_root=engine).root == engine

    def test_a_user_gets_their_own(self, tmp_path: Path) -> None:
        engine = tmp_path / "engine"
        engine.mkdir()
        got = manager_for(_user("u-2"), "mine", engine_root=engine).root
        assert got != engine and "u-2" in str(got)

    def test_two_users_do_not_share_a_manager(self, tmp_path: Path) -> None:
        engine = tmp_path / "engine"
        engine.mkdir()
        a = manager_for(_user("u-2"), "mine", engine_root=engine)
        b = manager_for(_user("u-3"), "mine", engine_root=engine)
        assert a is not b and a.root != b.root

    def test_the_same_project_gets_the_same_manager(self, tmp_path: Path) -> None:
        """It has to be the same object, or its record of what is running is not one record."""
        engine = tmp_path / "engine"
        engine.mkdir()
        first = manager_for(_user("u-2"), "mine", engine_root=engine)
        first.runs["fake"] = object()  # type: ignore[assignment]
        again = manager_for(_user("u-2"), "mine", engine_root=engine)
        assert again is first and "fake" in again.runs

    def test_a_user_naming_no_workspace_is_refused(self, tmp_path: Path) -> None:
        """Falling back to the engine's project is how a user would start work on the operator's hardware."""
        from pravrudhi.api.workspace_root import RootError

        engine = tmp_path / "engine"
        engine.mkdir()
        with pytest.raises(RootError):
            manager_for(_user("u-2"), None, engine_root=engine)


class TestConcurrency:
    def test_one_project_still_refuses_a_second_run(self, tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        from fastapi import HTTPException

        from pravrudhi.api.runs import Run, RunRequest

        monkeypatch.setenv("PRAVRUDHI_ADMINS", "u-1")
        engine = tmp_path / "engine"
        engine.mkdir()
        mgr = manager_for(_user("u-1"), None, engine_root=engine)
        mgr.runs["live"] = Run(id="live", target="model", night=1, request={}, started_at=0.0, status="running")
        with pytest.raises(HTTPException) as caught:
            mgr.start(RunRequest(target="model"))
        assert caught.value.status_code == 409

    def test_but_another_project_is_not_blocked_by_it(self, tmp_path: Path) -> None:
        """Two users each running in their own workspace is the product working, not a collision."""
        from pravrudhi.api.runs import Run

        engine = tmp_path / "engine"
        engine.mkdir()
        mine = manager_for(_user("u-2"), "mine", engine_root=engine)
        mine.runs["live"] = Run(id="live", target="model", night=1, request={}, started_at=0.0, status="running")
        theirs = manager_for(_user("u-3"), "mine", engine_root=engine)
        assert not any(r.status == "running" for r in theirs.runs.values())
