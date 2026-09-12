"""Bring a wave's accepted work back into the main tree without one agent erasing another.

Every task in a wave writes in its own worktree, and merging them was a copy per worktree. That is last-writer
wins: when two agents both touched a shared file — `api/server.py`, `cli/app.py`, the sidebar — the second copy
silently reverted the first, and the loss only surfaced when a test failed or a page stopped working. It happened
five times in one day, and each time the agents were blameless: the fault was in how their work was collected.

So integration is a three-way merge per file, from the base commit each worktree branched at, and a file two
worktrees both changed is either merged cleanly or reported as a conflict by name. Nothing is overwritten quietly.

Two rules make it safe to run unattended. A file only one worktree touched is taken as it stands. A file several
touched is merged against the common base, and if git cannot reconcile them the integration stops and says which
file and which tasks disagree, rather than picking a winner and hoping.
"""

from __future__ import annotations

import contextlib
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# CI runs mypy as well; the loop's first commit that passed ruff and pytest and failed mypy (`60f5a49`, an untyped
# lambda) went red on main, so the loop proves what CI proves before it commits.
BUILD_VALIDATE = "uv run ruff check src tests && uv run mypy src && uv run pytest -q tests"
"""What a build-mode agent's worktree must pass before its change is judged, and what the main tree must pass
after it is integrated. One command for both, so "it passed there" and "it passes here" mean one thing."""

# Build products and local state: an agent may write them, but they are never carried back into the main tree.
NEVER_INTEGRATE = (
    "node_modules/", "test-results/", "__pycache__/", ".smoke/", ".smoke-dist/", "dist/",
    ".pravrudhi/", ".worktrees/", "app/frontend/out/", "app/frontend/.next/",
)


@dataclass
class FileChange:
    path: str
    tasks: tuple[str, ...]

    @property
    def contested(self) -> bool:
        return len(self.tasks) > 1


@dataclass
class Integration:
    applied: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    contested: list[FileChange] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.conflicts

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok, "applied": self.applied, "conflicts": self.conflicts, "skipped": self.skipped,
            "contested": [{"path": c.path, "tasks": list(c.tasks)} for c in self.contested],
        }


def _git(root: Path, *args: str, check: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, timeout=300, check=check)


def _ignored(path: str) -> bool:
    return any(path.startswith(p) or f"/{p}" in f"/{path}" for p in NEVER_INTEGRATE)


def changed_files(worktree: Path) -> list[str]:
    """Every path this worktree changed against its base, build products excluded."""
    _git(worktree, "add", "-A")
    diff = _git(worktree, "diff", "--cached", "--name-only")
    _git(worktree, "reset")
    return sorted({ln.strip() for ln in diff.stdout.splitlines() if ln.strip() and not _ignored(ln.strip())})


def survey(root: Path, worktrees: dict[str, Path]) -> list[FileChange]:
    """Which task touched which file, so a contested file is known before anything is applied."""
    owners: dict[str, list[str]] = {}
    for task_id, wt in worktrees.items():
        if not wt.exists():
            continue
        for path in changed_files(wt):
            owners.setdefault(path, []).append(task_id)
    return [FileChange(path, tuple(tasks)) for path, tasks in sorted(owners.items())]


def integrate(root: Path, worktrees: dict[str, Path], *, dry_run: bool = False) -> Integration:
    """Merge every worktree's changes into `root`, three-way per file.

    A file one task touched is taken from that task. A file several tasks touched is merged from the base each
    branched at; git resolves what it can and names what it cannot. Nothing is silently overwritten, which is
    the whole point: the previous approach copied whole trees in sequence and the last copy won.
    """
    root = Path(root)
    result = Integration(contested=[c for c in survey(root, worktrees) if c.contested])

    for task_id, wt in worktrees.items():
        if not wt.exists():
            result.skipped.append(f"{task_id}: no worktree")
            continue
        _git(wt, "add", "-A")
        patch = _git(wt, "diff", "--cached", "--binary")
        _git(wt, "reset")
        if not patch.stdout.strip():
            result.skipped.append(f"{task_id}: no change")
            continue
        if dry_run:
            result.applied.append(task_id)
            continue
        applied = subprocess.run(
            ["git", "apply", "--3way", "--exclude=node_modules/*", "--exclude=*/test-results/*",
             "--exclude=*/__pycache__/*", "--exclude=*/.smoke/*", "--exclude=*/dist/*", "-"],
            cwd=root, input=patch.stdout, capture_output=True, text=True, timeout=300,
        )
        if applied.returncode != 0:
            result.conflicts.append(f"{task_id}: {applied.stderr.strip()[:300]}")
            continue
        result.applied.append(task_id)

    markers = _git(root, "diff", "--check")
    if markers.returncode != 0 and markers.stdout.strip():
        result.conflicts.append(f"conflict markers left in the tree: {markers.stdout.strip()[:300]}")
    return result


@dataclass
class BuildOutcome:
    """What integrating one build-mode criterion did to the main tree."""

    ok: bool
    why: str
    commit: str = ""
    files: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "why": self.why, "commit": self.commit, "files": self.files}


COMMIT_IDENTITY = {"GIT_AUTHOR_NAME": "SharathSPhD", "GIT_AUTHOR_EMAIL": "admin@axismeru.com",
                   "GIT_COMMITTER_NAME": "SharathSPhD", "GIT_COMMITTER_EMAIL": "admin@axismeru.com"}
"""The house identity (CLAUDE.md). The message names the loop and the criterion, so a reader can always tell a
loop-built commit from one a person wrote; no trailer, which .githooks/commit-msg enforces."""


def _restore(root: Path, files: list[str]) -> None:
    """Put ONLY the integrated files back. Never `reset --hard`: the main checkout may carry a person's
    uncommitted work in other files, and a loop that wiped it to undo its own change would be worse than the
    failure it was undoing."""
    # `git apply --3way` writes the index as well as the tree (conflict stages included), so a plain
    # `checkout --` would restore the NEW content from the index. Unstage first, then take HEAD's bytes.
    _git(root, "reset", "-q", "--", *files)
    tracked = [f for f in files if _git(root, "cat-file", "-e", f"HEAD:{f}").returncode == 0]
    if tracked:
        _git(root, "checkout", "HEAD", "--", *tracked)
    for f in files:
        if f not in tracked:
            with contextlib.suppress(OSError):
                (root / f).unlink()


def integrate_build_criterion(
    root: Path, task_id_to_worktree: dict[str, Path], request_id: str, criterion_index: int,
    *, validate: str = BUILD_VALIDATE,
) -> BuildOutcome:
    """Bring a build-mode criterion's worktree into the main tree, prove it there, and commit it as evidence.

    Three-way merge (a conflict stops everything and is noted), then `validate` in the main tree (a failure
    restores exactly the integrated files and is noted), then a commit of exactly those files whose sha becomes
    the criterion's evidence. Nothing is pushed: pushing stays a milestone act, so a loop-built commit that
    turns out wrong is reversible locally.
    """
    from pravrudhi.application import delegate, requests

    root = Path(root)
    files = sorted({f for wt in task_id_to_worktree.values() if wt.exists() for f in changed_files(wt)})
    if not files:
        return BuildOutcome(False, "the worktree changed nothing")
    result = integrate(root, task_id_to_worktree)
    if not result.ok:
        why = "integration conflict: " + "; ".join(result.conflicts[:3])
        _restore(root, files)
        requests.note(root, request_id, why)
        return BuildOutcome(False, why, files=files)
    ok, output = delegate.validate_in(root, validate)
    if not ok:
        _restore(root, files)
        why = "validate failed in the main tree after integration: " + " ".join(output.split())[-500:]
        requests.note(root, request_id, why)
        return BuildOutcome(False, why, files=files)
    req = requests.get(root, request_id)
    if req is None or criterion_index >= len(req.criteria):
        _restore(root, files)
        return BuildOutcome(False, f"no criterion {criterion_index} on {request_id}", files=files)
    head = req.criteria[criterion_index].text[:60].replace("\n", " ")
    message = f"{head} (request {request_id} criterion {criterion_index}, built by the loop under ADR-0040)"
    _git(root, "add", "--", *files)
    committed = subprocess.run(
        ["git", "commit", "-q", "-m", message, "--", *files],
        cwd=root, capture_output=True, text=True, env={**os.environ, **COMMIT_IDENTITY},
    )
    if committed.returncode != 0:
        _git(root, "reset", "-q", "--", *files)
        _restore(root, files)
        why = "commit refused: " + committed.stderr.strip()[-300:]
        requests.note(root, request_id, why)
        return BuildOutcome(False, why, files=files)
    sha = _git(root, "rev-parse", "HEAD").stdout.strip()
    requests.meet(root, request_id, criterion_index,
                  [requests.Evidence(kind="commit", ref=sha, note=f"built by the loop; files: {', '.join(files[:8])}")])
    return BuildOutcome(True, "integrated, validated and committed", commit=sha, files=files)


__all__ = [
    "BUILD_VALIDATE", "BuildOutcome", "COMMIT_IDENTITY", "FileChange", "Integration", "NEVER_INTEGRATE",
    "changed_files", "integrate", "integrate_build_criterion", "survey",
]
