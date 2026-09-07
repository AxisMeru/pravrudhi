"""A signed-in user must never spend the operator's model quota.

The operator's instruction on 2026-09-07: "the desktop app will not have access to any api/routing for any model
provider, they have to bring theirs, configure it etc... but for admin it uses the one as the core pravrudhi".

That is a spending boundary, not a preference. The engine holds working keys for several providers, and a user
who could reach them could run the operator's account to its limit from a machine the operator does not control.

The shape of the fix follows from a decision already made: a workspace is a complete project root, so a user's
keys live in their workspace exactly as the operator's live in the engine's root. The same file store, the same
0600 files, the same refusal to write inside a git work tree. Nothing new to secure.

What is new is the guard. Handing a signed-in user a store rooted at the engine would hand them the operator's
keys, and the mistake that produces it is a single wrong argument at a call site. So the boundary is asserted in
the one place that can see both roots, and it raises rather than falling back.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pravrudhi.api.identity import User
from pravrudhi.application.credentials import CredentialBoundaryError, store_for_project


def _user(uid: str = "u-2") -> User:
    return User(id=uid, email="someone@example.com", role="authenticated")


class TestTheOperator:
    def test_reads_the_engines_own_keys(self, tmp_path: Path) -> None:
        engine = tmp_path / "engine"
        engine.mkdir()
        store = store_for_project(engine, engine_root=engine, user=None)
        assert store.configured() == []

    def test_may_also_use_a_workspace_of_their_own(self, tmp_path: Path) -> None:
        engine, mine = tmp_path / "engine", tmp_path / "ws"
        for d in (engine, mine):
            d.mkdir()
        assert store_for_project(mine, engine_root=engine, user=None) is not None


class TestAUser:
    def test_gets_a_store_rooted_in_their_own_workspace(self, tmp_path: Path) -> None:
        engine, mine = tmp_path / "engine", tmp_path / "ws"
        for d in (engine, mine):
            d.mkdir()
        store = store_for_project(mine, engine_root=engine, user=_user())
        assert store.configured() == []

    def test_is_refused_a_store_rooted_at_the_engine(self, tmp_path: Path) -> None:
        """The whole point. One wrong argument at a call site would otherwise hand over the operator's keys."""
        engine = tmp_path / "engine"
        engine.mkdir()
        with pytest.raises(CredentialBoundaryError):
            store_for_project(engine, engine_root=engine, user=_user())

    def test_the_refusal_does_not_describe_the_operators_keys(self, tmp_path: Path) -> None:
        engine = tmp_path / "engine"
        engine.mkdir()
        with pytest.raises(CredentialBoundaryError) as caught:
            store_for_project(engine, engine_root=engine, user=_user())
        assert "sk-" not in str(caught.value)

    def test_the_check_survives_a_differently_spelled_path(self, tmp_path: Path) -> None:
        """`engine/../engine` is the engine. A string comparison would have let it through."""
        engine = tmp_path / "engine"
        engine.mkdir()
        disguised = engine / ".." / "engine"
        with pytest.raises(CredentialBoundaryError):
            store_for_project(disguised, engine_root=engine, user=_user())

    def test_two_users_do_not_share_a_store(self, tmp_path: Path) -> None:
        engine = tmp_path / "engine"
        engine.mkdir()
        a, b = tmp_path / "a", tmp_path / "b"
        for d in (a, b):
            d.mkdir()
        store_a = store_for_project(a, engine_root=engine, user=_user("u-2"))
        store_b = store_for_project(b, engine_root=engine, user=_user("u-3"))
        store_a.put("alibaba", "sk-" + "a" * 24)
        assert store_a.configured() == ["alibaba"]
        assert store_b.configured() == [], "one user's key must not appear in another's store"
