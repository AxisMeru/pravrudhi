"""Whose project is a request about?

Every user-facing route reads from a single directory: the one the engine was started in. That is right for the
operator, whose project *is* Pravrudhi. It is wrong for everyone else, because a signed-in user asking for their
objectives is currently shown the operator's, and a user creating one would write into the engine's own project.

The workspace machinery already solves the hard half. `ensure_workspace` runs `init_project` in each workspace, so
a workspace is not a folder inside a project — it is a complete project root with its own objectives, its own
ledger and its own configuration. What was missing is the small decision in front of it: given who is asking and
which workspace they named, which root does this request read and write?

Two rules, and the second matters more than it looks. An operator with no workspace named gets the engine's own
root, because that is their project and it is how the machine has always run. A user must name a workspace, and
gets theirs; refusing rather than falling back to the engine root is the whole point, since falling back is
exactly how one user would end up reading another's work, or the operator's.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pravrudhi.api.identity import User
from pravrudhi.api.workspace_root import RootError, root_for


def _user(uid: str = "u-2") -> User:
    return User(id=uid, email="someone@example.com", role="authenticated")


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PRAVRUDHI_WORKSPACES", str(tmp_path / "workspaces"))
    monkeypatch.delenv("PRAVRUDHI_ADMINS", raising=False)


class TestTheOperator:
    def test_gets_the_engines_own_project_when_no_workspace_is_named(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("PRAVRUDHI_ADMINS", "u-1")
        engine = tmp_path / "engine"
        assert root_for(_user("u-1"), None, engine_root=engine) == engine

    def test_may_still_open_a_workspace_of_their_own(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An operator is also a user of their own product, and should be able to try it as one."""
        monkeypatch.setenv("PRAVRUDHI_ADMINS", "u-1")
        got = root_for(_user("u-1"), "trial", engine_root=tmp_path / "engine")
        assert got != tmp_path / "engine"
        assert got.is_dir()

    def test_with_authentication_off_the_local_caller_is_the_operator(self, tmp_path: Path) -> None:
        engine = tmp_path / "engine"
        assert root_for(None, None, engine_root=engine) == engine


class TestAUser:
    def test_gets_their_own_project_root(self, tmp_path: Path) -> None:
        got = root_for(_user("u-2"), "mine", engine_root=tmp_path / "engine")
        assert got.is_dir()
        assert "u-2" in str(got) and got.name == "mine"

    def test_is_refused_rather_than_shown_the_engines_project(self, tmp_path: Path) -> None:
        """Falling back to the engine root is how a user would end up reading the operator's work."""
        with pytest.raises(RootError):
            root_for(_user("u-2"), None, engine_root=tmp_path / "engine")

    def test_two_users_naming_the_same_workspace_get_different_projects(self, tmp_path: Path) -> None:
        a = root_for(_user("u-2"), "mine", engine_root=tmp_path / "engine")
        b = root_for(_user("u-3"), "mine", engine_root=tmp_path / "engine")
        assert a != b

    def test_a_workspace_is_a_whole_project_not_a_folder(self, tmp_path: Path) -> None:
        """The reason this works at all: each workspace is initialised as its own project root."""
        got = root_for(_user("u-2"), "mine", engine_root=tmp_path / "engine")
        assert (got / ".pravrudhi").is_dir()

    def test_a_hostile_slug_cannot_escape_the_workspace_area(self, tmp_path: Path) -> None:
        for slug in ("../../etc", "..", "/absolute", "a/b"):
            with pytest.raises(RootError):
                root_for(_user("u-2"), slug, engine_root=tmp_path / "engine")

    def test_an_anonymous_caller_is_refused_when_authentication_is_on(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("PRAVRUDHI_AUTH", "required")
        with pytest.raises(RootError):
            root_for(None, "mine", engine_root=tmp_path / "engine")
