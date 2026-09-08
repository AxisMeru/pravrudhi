"""Delegating development work to the agent fleet, safely.

Several agents improving one repository is useful only if their work cannot collide or slip in unvalidated. Three
guarantees are enforced here rather than trusted to the agents or to whoever wrote the prompt.

Disjoint ownership: a task declares the paths it may touch, and two tasks that could touch the same path are never
dispatched together. Agents cannot overwrite each other because they are never given overlapping ground, and each
works in its own worktree besides.

Declared scope: after the run the diff is checked against the declaration. An agent that edited something it was
not given is rejected whole, not partially merged, because a diff that wandered is evidence the agent misunderstood
the task rather than a diff with one stray file in it.

Validation before merge: the task's own check runs inside the worktree, and only a passing, in-scope,
protected-path-clean diff is eligible. Nothing reaches the working tree because an agent said it was finished.
"""

from __future__ import annotations

import fnmatch
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pravrudhi.agents.base import Diff
from pravrudhi.application import availability


@dataclass(frozen=True)
class TaskSpec:
    """One unit of delegated work: what to do, where it may write, and how it is checked."""

    task_id: str
    prompt: str
    allowed_paths: tuple[str, ...]
    validate: str = "uv run pytest -q"
    timeout_s: int = 1800

    def owns(self, path: str) -> bool:
        """A declared path is a glob, or a directory when it ends in `/`.

        `fnmatch` alone treated `app/frontend/src/app/progress/` as a literal name, so a task handed a directory
        wrote every file inside it and was rejected for escaping its own scope; the work was correct and the
        detector was wrong.
        """
        for pat in self.allowed_paths:
            if pat.endswith("/") and path.startswith(pat):
                return True
            if fnmatch.fnmatch(path, pat):
                return True
        return False

    def out_of_scope(self, diff: Diff) -> list[str]:
        return sorted(f for f in diff.files if not self.owns(f))


@dataclass(frozen=True)
class Verdict:
    task_id: str
    agent: str
    accepted: bool
    reasons: list[str] = field(default_factory=list)
    files: list[str] = field(default_factory=list)
    validation_output: str = ""
    wall_s: float = 0.0
    tokens: int = 0
    """What the dispatch consumed, carried up from the agent so the router can budget. Zero means the adapter
    could not tell, never that the work was free."""

    limited: bool = False
    resets_at: str = ""
    """When the vendor said the account comes back, ISO-8601 in UTC, empty when it did not say. Parsed here for
    the same reason `limited` is: this is where the agent's whole output is still in hand."""
    """Set when the agent failed because its account is spent rather than because the work was wrong.

    It is decided here, where the agent's whole output is still in hand. The swarm used to infer it from the
    rejection reason, which carries the first 200 characters of a 2000-character tail — and a vendor prints
    "you've hit your usage limit" at the *end* of a run, after echoing the prompt. So the one message that
    should have moved the task to another route was the one guaranteed to be truncated away, and the fallback
    never fired on the failure it exists for."""

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id, "agent": self.agent, "accepted": self.accepted,
            "reasons": self.reasons, "files": self.files, "wall_s": round(self.wall_s, 1),
        }


def overlapping(tasks: list[TaskSpec]) -> list[tuple[str, str]]:
    """Pairs of tasks whose declared paths could collide.

    Compared pattern against pattern, not file against file: two tasks that both claim `src/pravrudhi/cli/*` are in
    conflict even if today they would happen to edit different files there.
    """
    bad: list[tuple[str, str]] = []
    for i, a in enumerate(tasks):
        for b in tasks[i + 1 :]:
            collide = any(
                fnmatch.fnmatch(pa, pb) or fnmatch.fnmatch(pb, pa) or pa == pb
                for pa in a.allowed_paths
                for pb in b.allowed_paths
            )
            if collide:
                bad.append((a.task_id, b.task_id))
    return bad


def validate_in(workspace: Path, command: str, timeout_s: int = 1800) -> tuple[bool, str]:
    try:
        p = subprocess.run(["bash", "-lc", command], cwd=workspace, capture_output=True, text=True, timeout=timeout_s)
        return p.returncode == 0, (p.stdout + p.stderr)[-4000:]
    except subprocess.TimeoutExpired:
        return False, f"validation timed out after {timeout_s}s"


def _tree_state(root: Path) -> set[str]:
    """Uncommitted paths in a checkout, so a change made there during a dispatch can be told from one made before.

    `-uall` is load-bearing. By default `git status --porcelain` collapses an untracked directory to one entry, so
    a deliverable written into the main checkout appears as `proposals/probe/` rather than as the file itself. A
    task declares its scope as globs over files, which no directory name can match, so the escape was invisible
    and the verdict read "no change produced" - the same sentence as the incident this check was added for. Every
    real escape creates a new directory, because a proposal's first file always does.
    """
    try:
        p = subprocess.run(["git", "status", "--porcelain", "-uall"], cwd=root, capture_output=True, text=True,
                           timeout=60)
    except (OSError, subprocess.SubprocessError):
        return set()
    return {ln[3:] for ln in p.stdout.splitlines() if ln.strip()}


def dispatch(agent: Any, task: TaskSpec, *, log: Any = print) -> Verdict:
    """Run one task with one agent and judge the result. The worktree is left in place when accepted, so the
    change can be inspected and merged deliberately; a rejected worktree is also kept, because a rejected diff is
    the most interesting thing to read."""
    brief = (
        f"{task.prompt}\n\nYou may create or modify ONLY these paths: {', '.join(task.allowed_paths)}.\n"
        "Do not modify any other file. Do not touch pravrudhi_kernel/, research/, gates/ or .pravrudhi/.\n"
        "Write every file relative to your current working directory, which is your own worktree; never write to "
        "an absolute path in the main checkout, even when the task quotes one to tell you what to read.\n"
        f"Your work is accepted only if `{task.validate}` passes."
    )
    ws = agent.create_workspace(task.task_id)
    root = Path(getattr(agent, "root", ws))
    before = _tree_state(root) if root != ws else set()
    log(f"{task.task_id}: {agent.name} working in {ws}")
    run = agent.run(brief, ws, timeout_s=task.timeout_s)
    diff = agent.collect_changes(ws)
    reasons: list[str] = []
    appeared = sorted(_tree_state(root) - before) if root != ws else []
    # Only a path the task itself owns counts as an escape: that is the observed failure mode (an agent writing
    # its deliverable into the main checkout). The operator edits main while waves run, and the first form of this
    # check blamed two agents for files the operator had changed in unrelated modules.
    escaped = [f for f in appeared if task.owns(f)]
    if escaped:
        # Two agents once wrote their whole deliverable into the main checkout instead of their worktree, because
        # the task quoted absolute paths under it. Their worktrees were empty, so they were recorded as having
        # produced nothing, and the files were swept into the next commit unreviewed. The worktree diff cannot see
        # this; only the main tree can.
        reasons.append(f"wrote outside its worktree, into the main checkout: {', '.join(escaped[:8])}")
    limited, resets_at = False, ""
    if not run.ok:
        # The end of the tail, not the beginning: a run that fails ends with its reason and starts with an echo
        # of the prompt it was given.
        tail = (run.stderr_tail or "").strip()
        reasons.append(f"agent exited non-zero: {tail[-200:] or 'no detail'}")
        whole = f"{tail}\n{run.text or ''}"
        limited = availability.classify(agent.name, whole, 1) == "limited"
        when = availability.reset_at(whole) if limited else None
        resets_at = when.strftime("%Y-%m-%dT%H:%M:%SZ") if when else ""
    if diff.empty:
        reasons.append("no change produced")
    if diff.violations:
        reasons.append(f"touched protected paths: {', '.join(diff.violations)}")
    stray = task.out_of_scope(diff)
    if stray:
        reasons.append(f"wrote outside its declared scope: {', '.join(stray[:8])}")
    output = ""
    if not reasons:
        ok, output = validate_in(ws, task.validate, task.timeout_s)
        if not ok:
            reasons.append("validation failed")
    verdict = Verdict(
        task_id=task.task_id, agent=agent.name, accepted=not reasons, reasons=reasons,
        files=diff.files, validation_output=output[-2000:], wall_s=run.wall_s,
        tokens=int(getattr(run, "tokens", 0) or 0), limited=limited, resets_at=resets_at,
    )
    log(f"{task.task_id}: {'ACCEPTED' if verdict.accepted else 'REJECTED'} ({'; '.join(reasons) or 'all checks passed'})")
    return verdict
