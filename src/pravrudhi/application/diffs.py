"""Every competitor surveyed leads with a diff viewer. This engine dispatches a swarm whose entire output is
diffs, and until now had no way to look at one.

A dispatched task's work lives in its own git worktree (see `agents/base.py`'s `GitWorktreeMixin`, which
`delegate.py` and `dispatchboard.py` both use): `.worktrees/agent-<task_id>` on branch `agent/<task_id>`,
created off whatever commit was `HEAD` in the main checkout at dispatch time. That commit -- recovered here as
the merge-base between the worktree's `HEAD` and the main checkout's current `HEAD` -- is the worktree's base:
`git diff <base>` against the worktree's working tree shows everything the task has done, committed or not,
without needing to know a branch name or trust that nothing was rebased.

Nothing here writes anything. `worktree_diff` only ever runs `git rev-parse`, `git merge-base`, `git diff` and
`git ls-files`, scoped to a path this module has itself verified sits under `<root>/.worktrees/`, so a hostile
task id can neither escape the worktree directory nor invoke anything but git.
"""

from __future__ import annotations

import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from pravrudhi.agents.base import GitWorktreeMixin

MAX_FILE_LINES = 2000
MAX_DIFF_BYTES = 400_000

_DIFF_GIT_RE = re.compile(r"^diff --git a/(.*) b/(.*)$")


@dataclass(frozen=True)
class DiffLine:
    kind: str  # "context" | "add" | "del"
    text: str


@dataclass(frozen=True)
class Hunk:
    header: str
    lines: list[DiffLine] = field(default_factory=list)


@dataclass(frozen=True)
class FileDiff:
    path: str
    added: int
    removed: int
    hunks: list[Hunk] = field(default_factory=list)
    binary: bool = False
    too_large: bool = False


@dataclass(frozen=True)
class Diff:
    """One worktree's changes against its base commit. `reason` is set, and `files` empty, when the worktree
    could not be read at all; `truncated` marks that the 400 KB overall cap cut later files short, and a
    file's own `too_large` marks that its 2000-line cap cut that file's hunks short. Neither ever cuts silently:
    a capped file still reports its true `added`/`removed` counts."""

    files: list[FileDiff]
    base: str
    head: str
    truncated: bool = False
    reason: str = ""


@dataclass(frozen=True)
class TaskSummary:
    """One dispatched task with a readable worktree, summarised for a diff list."""

    task_id: str
    files: int
    added: int
    removed: int
    truncated: bool


def _git(args: list[str], cwd: Path, timeout_s: int = 30) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=timeout_s)


def _worktree_path(root: Path, task_id: str) -> Path | None:
    """The worktree a task id resolves to, or `None` if it would resolve outside `<root>/.worktrees/`.

    `GitWorktreeMixin.ref_safe` -- the same sanitiser `create_workspace` applies before it ever runs `git
    worktree add` -- collapses every character git would refuse in a ref (slashes included) to a hyphen, so the
    result can never contain a path separator. The `resolve()` check is defence in depth, not the only guard.
    """
    safe_id = GitWorktreeMixin.ref_safe(task_id)
    worktrees_dir = (root / ".worktrees").resolve()
    wt = (root / ".worktrees" / f"agent-{safe_id}").resolve()
    if wt != worktrees_dir and worktrees_dir not in wt.parents:
        return None
    return wt


def _split_files(patch_text: str) -> list[tuple[str, list[str]]]:
    """A multi-file unified diff, split at each `diff --git a/X b/X` header into `(path, body_lines)`."""
    blocks: list[tuple[str, list[str]]] = []
    path: str | None = None
    body: list[str] = []
    for line in patch_text.splitlines():
        m = _DIFF_GIT_RE.match(line)
        if m:
            if path is not None:
                blocks.append((path, body))
            path, body = m.group(2), []
            continue
        if path is not None:
            body.append(line)
    if path is not None:
        blocks.append((path, body))
    return blocks


def _parse_file_block(lines: list[str]) -> tuple[bool, int, int, list[Hunk]]:
    """One file's diff body to `(binary, added, removed, hunks)`. File-header lines (`index`, `---`, `+++`,
    `deleted file mode`, ...) are skipped: they precede the first `@@` hunk marker and are never useful to show."""
    for line in lines:
        if line.startswith("Binary files ") and line.endswith(" differ"):
            return True, 0, 0, []
    hunks: list[Hunk] = []
    added = removed = 0
    current: Hunk | None = None
    for line in lines:
        if line.startswith("@@"):
            current = Hunk(header=line)
            hunks.append(current)
            continue
        if current is None:
            continue  # file-header line, before any hunk has started
        if line.startswith("\\"):
            continue  # "\ No newline at end of file"
        if line.startswith("+"):
            current.lines.append(DiffLine("add", line[1:]))
            added += 1
        elif line.startswith("-"):
            current.lines.append(DiffLine("del", line[1:]))
            removed += 1
        else:
            current.lines.append(DiffLine("context", line[1:] if line else ""))
    return False, added, removed, hunks


def _cap_hunks(hunks: list[Hunk]) -> list[Hunk]:
    kept = 0
    out: list[Hunk] = []
    for h in hunks:
        if kept >= MAX_FILE_LINES:
            break
        take = h.lines[: MAX_FILE_LINES - kept]
        out.append(Hunk(header=h.header, lines=take))
        kept += len(take)
    return out


def _hunk_bytes(hunks: list[Hunk]) -> int:
    return sum(len(h.header) for h in hunks) + sum(len(line.text) for h in hunks for line in h.lines)


def worktree_diff(root: Path, task_id: str) -> Diff:
    """A dispatched task's worktree, diffed against the commit its branch forked from."""
    root = Path(root)
    wt = _worktree_path(root, task_id)
    if wt is None:
        return Diff(files=[], base="", head="", reason=f"refused: {task_id!r} does not resolve under .worktrees/")
    if not wt.is_dir() or not (wt / ".git").exists():
        return Diff(files=[], base="", head="", reason=f"worktree for {task_id!r} no longer exists")

    head_p = _git(["rev-parse", "HEAD"], wt)
    if head_p.returncode != 0:
        return Diff(files=[], base="", head="", reason=f"worktree for {task_id!r} is not a readable git checkout")
    head = head_p.stdout.strip()

    root_head_p = _git(["rev-parse", "HEAD"], root)
    root_head = root_head_p.stdout.strip() if root_head_p.returncode == 0 else head

    base_p = _git(["merge-base", "HEAD", root_head], wt)
    base = base_p.stdout.strip() if base_p.returncode == 0 else root_head

    patch_p = _git(["diff", "--no-color", base], wt)
    blocks = _split_files(patch_p.stdout if patch_p.returncode == 0 else "")

    untracked_p = _git(["ls-files", "--others", "--exclude-standard"], wt)
    for rel in untracked_p.stdout.splitlines():
        if not rel.strip():
            continue
        p = _git(["diff", "--no-color", "--no-index", "--", "/dev/null", rel], wt)
        if p.stdout:
            blocks.extend(_split_files(p.stdout))

    files: list[FileDiff] = []
    total_bytes = 0
    truncated = False
    for path, body in sorted(blocks, key=lambda b: b[0]):
        if total_bytes >= MAX_DIFF_BYTES:
            truncated = True
            files.append(FileDiff(path=path, added=0, removed=0, too_large=True))
            continue
        binary, added, removed, hunks = _parse_file_block(body)
        too_large = False
        if not binary and sum(len(h.lines) for h in hunks) > MAX_FILE_LINES:
            hunks = _cap_hunks(hunks)
            too_large = True
        size = _hunk_bytes(hunks)
        if total_bytes + size > MAX_DIFF_BYTES:
            truncated = True
        total_bytes += size
        files.append(FileDiff(path=path, added=added, removed=removed, hunks=hunks, binary=binary, too_large=too_large))
    return Diff(files=files, base=base, head=head, truncated=truncated)


def recent(root: Path, n: int = 20) -> list[TaskSummary]:
    """Dispatched tasks that still have a readable worktree, newest first."""
    root = Path(root)
    wdir = root / ".worktrees"
    if not wdir.is_dir():
        return []
    candidates = sorted(
        (p for p in wdir.glob("agent-*") if p.is_dir()), key=lambda p: p.stat().st_mtime, reverse=True
    )
    out: list[TaskSummary] = []
    for p in candidates:
        if len(out) >= max(0, n):
            break
        task_id = p.name[len("agent-") :]
        diff = worktree_diff(root, task_id)
        if diff.reason:
            continue
        out.append(
            TaskSummary(
                task_id=task_id,
                files=len(diff.files),
                added=sum(f.added for f in diff.files),
                removed=sum(f.removed for f in diff.files),
                truncated=diff.truncated,
            )
        )
    return out


@dataclass(frozen=True)
class StaleWorktree:
    """One dispatched task's worktree still on disk, with enough to decide by hand whether it is safe to
    remove. `merged` means the branch is already an ancestor of the main checkout's `HEAD` -- a build criterion
    that was integrated, or a branch someone fast-forwarded by hand -- so nothing here is lost by deleting it;
    an unmerged branch may still be the only copy of a rejected attempt worth reading before it goes."""

    task_id: str
    path: str
    branch: str
    age_days: float
    files: int | None
    merged: bool
    unreadable: str = ""


def stale_agent_worktrees(root: Path) -> list[StaleWorktree]:
    """Every `agent-*` worktree under `<root>/.worktrees/`, oldest first. Read-only, by design: `delegate.dispatch`
    has never removed a worktree it created (`git worktree add` and nothing else, so the diff stays inspectable
    after a rejection), and nothing else has either, which is how a Studio checkout accumulated 89 of them. This
    only reports; which of a real fleet's worktrees are actually done is the operator's call, and the caller
    reports the exact `git worktree remove` / `git branch -D` for each rather than run either.
    """
    root = Path(root)
    wdir = root / ".worktrees"
    if not wdir.is_dir():
        return []
    root_head_p = _git(["rev-parse", "HEAD"], root)
    root_head = root_head_p.stdout.strip() if root_head_p.returncode == 0 else "HEAD"
    out: list[StaleWorktree] = []
    for p in sorted((x for x in wdir.glob("agent-*") if x.is_dir()), key=lambda x: x.stat().st_mtime):
        task_id = p.name[len("agent-") :]
        branch = f"agent/{GitWorktreeMixin.ref_safe(task_id)}"
        diff = worktree_diff(root, task_id)
        merged_p = _git(["merge-base", "--is-ancestor", branch, root_head], root)
        out.append(StaleWorktree(
            task_id=task_id, path=str(p), branch=branch,
            age_days=round((time.time() - p.stat().st_mtime) / 86400, 1),
            files=None if diff.reason else len(diff.files),
            merged=merged_p.returncode == 0,
            unreadable=diff.reason,
        ))
    return out


__all__ = [
    "Diff", "DiffLine", "FileDiff", "Hunk", "StaleWorktree", "TaskSummary",
    "recent", "stale_agent_worktrees", "worktree_diff",
]
