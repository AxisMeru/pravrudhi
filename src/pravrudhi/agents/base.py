"""The coding-agent extension point: one protocol, many providers.

Pravrudhi's `Target` protocol is how a *benchmark* plugs in. This is the other side: how an external coding agent
(Claude Code, Codex, an Orca-managed agent, a local model) plugs in as a proposer of code changes to the harness and
the engine. The controller stays provider-neutral, so no single vendor or orchestrator is load-bearing.

Two boundaries are enforced here rather than left to good intentions.

Protected paths: an external agent works in its own git worktree and may not touch the kernel, the ledger, the sealed
pools or the pre-registration thresholds. Those are the tamper surfaces of a self-grading system, and an agent that
edits its own evaluator is not improving, it is cheating. `Diff.violations` names any protected path a run touched.

Distillation: these agents write code. Weight-level distillation teachers are open-weight local models (Qwen today).
A hosted assistant's outputs must never become training data for a trainee, which is both a licence question and a
scientific one, since a distilled trainee would no longer be measuring the loop.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

PROTECTED = (
    "pravrudhi_kernel/",
    "research/ledger.jsonl",
    "research/prereg/",
    ".pravrudhi/kernel/",
    "gates/",
)


@dataclass(frozen=True)
class AgentRun:
    agent: str
    ok: bool
    exit_code: int
    wall_s: float
    text: str
    workspace: Path
    session_id: str | None = None
    cost_usd: float | None = None
    tokens: int | None = None
    """What the turn consumed, when the adapter can tell, cache included. `None` means the adapter could not
    tell; zero means it told us the turn was free.

    This was `int = 0` with a docstring reading "Zero means unknown, never free" — a distinction the type could
    not carry and three `int(... or 0)` call sites then discarded. Counted across all 157 outcomes in
    `.pravrudhi/routing.jsonl` on 2026-09-09, 155 read zero and only `alibaba_agent.py` ever assigned the field,
    so `routing.spend` was measuring a weekly allowance against 1.3% of the dispatches while `over_budget` could
    essentially never trip. A budget that cannot see what it is spending is not a control."""

    cache_read_tokens: int | None = None
    """Tokens served from the prompt cache. Kept apart from `cache_write_tokens` rather than summed into
    `tokens`, because the whole point of a cache target is the ratio between them."""

    cache_write_tokens: int | None = None
    """Tokens written into the prompt cache: billed above the base rate, so a low hit ratio costs more than not
    caching at all."""

    stderr_tail: str = ""


@dataclass(frozen=True)
class Diff:
    files: list[str] = field(default_factory=list)
    insertions: int = 0
    deletions: int = 0
    patch: str = ""

    @property
    def violations(self) -> list[str]:
        """Protected paths this diff touched: the kernel, the ledger, sealed state, prereg thresholds, gates."""
        return sorted({f for f in self.files if any(f.startswith(p) for p in PROTECTED)})

    @property
    def empty(self) -> bool:
        return not self.files


@runtime_checkable
class CodingAgent(Protocol):
    name: str

    def available(self) -> bool: ...
    def create_workspace(self, task_id: str, base_ref: str = "HEAD") -> Path: ...
    def run(self, prompt: str, workspace: Path, timeout_s: int = 1800) -> AgentRun: ...
    def collect_changes(self, workspace: Path) -> Diff: ...
    def stop(self, workspace: Path) -> None: ...


def git(args: list[str], cwd: Path, timeout_s: int = 120) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=timeout_s)


class GitWorktreeMixin:
    """Worktree lifecycle shared by every adapter that isolates an agent in its own branch.

    Isolation is the point: two agents on one working tree cannot be compared, and a failed run must be discardable
    without touching main. Each task gets `.worktrees/agent-<task_id>` on branch `agent/<task_id>`.
    """

    root: Path

    @staticmethod
    def ref_safe(task_id: str) -> str:
        """A task id as git will accept it in a branch name.

        The first time the engine dispatched its own plan, every step failed in zero seconds: the task ids were
        `<objective>:<step>`, and a colon is not valid in a ref. Nothing had told the swarm that a task id is also
        a branch name. Any run of characters git refuses becomes one hyphen, so the mapping is readable and stable."""
        return re.sub(r"[^A-Za-z0-9._-]+", "-", task_id).strip("-.") or "task"

    def _worktree_path(self, task_id: str) -> Path:
        return self.root / ".worktrees" / f"agent-{self.ref_safe(task_id)}"

    def create_workspace(self, task_id: str, base_ref: str = "HEAD") -> Path:
        """A fresh worktree on a fresh branch, every time -- never whatever an earlier attempt at this same
        task id happened to leave behind.

        Used to return an existing directory unchanged (`if wt.exists(): return wt`), on the assumption that
        one task id is dispatched once. It is not: a criterion refused on one beat is retried on a later one
        under the same id, and a worktree nothing had cleaned up became the next attempt's starting state --
        an untracked file, or even a whole commit, from a rejected attempt read as this attempt's own leftover
        (2026-09-12, r-3981d7e0 c3: `git worktree list` in the product install showed six branches for one
        criterion, one of them holding a committed change from an attempt the collector had called empty).
        A retry must start exactly at `base_ref`, so any prior worktree and branch for this task id are torn
        down first rather than reused.
        """
        wt = self._worktree_path(task_id)
        branch = f"agent/{self.ref_safe(task_id)}"
        if wt.exists():
            r = git(["worktree", "remove", "--force", str(wt)], self.root)
            if r.returncode != 0:
                # The directory exists but git no longer recognises it as a worktree (its registration was lost
                # or it was deleted by hand outside git) -- clear both by hand rather than leave stale state
                # `worktree add` would then refuse to reuse.
                shutil.rmtree(wt, ignore_errors=True)
                git(["worktree", "prune"], self.root)
        git(["branch", "-D", branch], self.root)  # no such branch is not an error worth checking for
        wt.parent.mkdir(parents=True, exist_ok=True)
        r = git(["worktree", "add", "-b", branch, str(wt), base_ref], self.root)
        if r.returncode != 0:
            raise RuntimeError(f"git worktree add failed: {r.stderr.strip()[:400]}")
        return wt

    def collect_changes(self, workspace: Path) -> Diff:
        """Everything the dispatch has done: committed on its own branch since it forked from the main
        checkout, or still sitting uncommitted or untracked in the working tree.

        Used to diff only against the worktree's own `HEAD`, which shows nothing once an agent commits its
        own work -- the commit moves `HEAD` to match the working tree. An agent that committed was then
        reported as having produced no change at all, the same failure `application/diffs.py`'s
        `worktree_diff` had already solved for the diff-viewer page by diffing against the merge-base with the
        main checkout instead; this mirrors that fix here, where the verdict itself is decided.
        """
        root_head_p = git(["rev-parse", "HEAD"], self.root)
        root_head = root_head_p.stdout.strip() if root_head_p.returncode == 0 else "HEAD"
        base_p = git(["merge-base", "HEAD", root_head], workspace)
        base = base_p.stdout.strip() if base_p.returncode == 0 and base_p.stdout.strip() else root_head
        stat = git(["diff", "--numstat", base], workspace)
        files, ins, dele = [], 0, 0
        for line in stat.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) == 3:
                a, d, f = parts
                files.append(f)
                ins += int(a) if a.isdigit() else 0
                dele += int(d) if d.isdigit() else 0
        untracked = git(["ls-files", "--others", "--exclude-standard"], workspace).stdout.split()
        files.extend(untracked)
        patch = git(["diff", base], workspace).stdout
        return Diff(files=sorted(set(files)), insertions=ins, deletions=dele, patch=patch)

    def stop(self, workspace: Path) -> None:
        git(["worktree", "remove", "--force", str(workspace)], self.root)


def timed(fn: Callable[..., Any]) -> Callable[..., tuple[Any, float]]:
    def wrapper(*a: Any, **k: Any) -> tuple[Any, float]:
        t0 = time.monotonic()
        out = fn(*a, **k)
        return out, time.monotonic() - t0

    return wrapper
