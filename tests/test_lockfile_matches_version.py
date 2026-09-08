"""The lockfile records the version the project actually declares.

This is not tidiness. `uv` re-locks whenever `uv.lock` disagrees with `pyproject.toml`, and it does so as a side
effect of any ordinary `uv run`. The dev update channel refuses to pull onto a dirty tree, so a stale lock turns
every machine on that channel into one that quietly stops updating and reports no error: the drift was found on a
real agent worktree whose only "change" was a lock rewritten by the validation command.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _declared_version() -> str:
    with (ROOT / "pyproject.toml").open("rb") as stream:
        return str(tomllib.load(stream)["project"]["version"])


def _locked_version() -> str | None:
    text = (ROOT / "uv.lock").read_text(encoding="utf-8")
    match = re.search(r'name = "pravrudhi"\nversion = "([^"]+)"', text)
    return match.group(1) if match else None


def test_the_lockfile_agrees_with_pyproject() -> None:
    declared, locked = _declared_version(), _locked_version()
    assert locked is not None, "uv.lock does not record a version for this project at all"
    assert locked == declared, (
        f"uv.lock records {locked} but pyproject declares {declared}; run `uv lock` and commit it, "
        "or the dev update channel will refuse to pull onto the tree uv dirties"
    )
