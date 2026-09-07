"""Decide whose project a request is about.

Every user-facing route reads from the directory the engine was started in. That is correct for the operator,
whose project *is* Pravrudhi, and wrong for everyone else: a signed-in user asking for their objectives is shown
the operator's, and creating one would write into the engine's own project.

The workspace machinery already does the hard half. `ensure_workspace` runs `init_project` inside each workspace,
so a workspace is not a folder within a project — it is a complete project root, with its own objectives, ledger
and configuration. What was missing is the small decision in front of it.

Two rules. An operator who names no workspace gets the engine's own root, because that is their project and it is
how this has always run locally. A user must name a workspace and gets theirs.

The second rule refuses rather than falling back, and that is the load-bearing part. A fallback to the engine root
for a caller who named nothing is exactly how one user ends up reading another's work, or the operator's, and it
would fail open and silently. Refusing is noisy and safe.
"""

from __future__ import annotations

from pathlib import Path

from pravrudhi.api.identity import User
from pravrudhi.api.roles import is_admin
from pravrudhi.application.workspaces import WorkspaceError, ensure_workspace


class RootError(ValueError):
    """The caller did not name a project this request could be about."""


def root_for(user: User | None, workspace: str | None, *, engine_root: Path) -> Path:
    """The project root this request reads and writes.

    `engine_root` is the directory the engine runs in. It is returned only for an operator who named no
    workspace; every other caller is resolved into their own, which is created on first use.
    """
    if workspace is None:
        if is_admin(user):
            return engine_root
        raise RootError("Name a workspace: a signed-in user's work lives in their own, not in the engine's.")

    if user is None:
        # An anonymous caller reaching this point has authentication switched on, since `is_admin` already
        # returned False. There is no user directory to resolve them into.
        raise RootError("Sign in to open a workspace.")

    try:
        return ensure_workspace(user.id, workspace)
    except WorkspaceError as e:
        raise RootError(str(e)) from e


__all__ = ["RootError", "root_for"]
