"""A task id is also a git branch name, and the first real dispatch of a plan proved nobody had said so."""

from __future__ import annotations

import subprocess
from pathlib import Path

from pravrudhi.agents.base import GitWorktreeMixin


class _Agent(GitWorktreeMixin):
    def __init__(self, root: Path) -> None:
        self.root = root


def _repo(path: Path) -> Path:
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    (path / "a").write_text("a\n")
    subprocess.run(["git", "-C", str(path), "add", "."], check=True)
    subprocess.run(["git", "-C", str(path), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "i"], check=True)
    return path


def test_ref_safe_replaces_what_git_refuses() -> None:
    assert GitWorktreeMixin.ref_safe("prabhasa-nyaya:baseline-evaluation") == "prabhasa-nyaya-baseline-evaluation"
    assert GitWorktreeMixin.ref_safe("a b/c~d^e?f") == "a-b-c-d-e-f"
    assert GitWorktreeMixin.ref_safe("plain-id_1.2") == "plain-id_1.2"
    assert GitWorktreeMixin.ref_safe(":::") == "task"


def test_a_task_id_with_a_colon_gets_a_worktree(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    ws = _Agent(root).create_workspace("obj:step")
    assert ws.exists() and ws.name == "agent-obj-step"
    branches = subprocess.run(["git", "-C", str(root), "branch", "--list", "agent/*"], capture_output=True, text=True).stdout
    assert "agent/obj-step" in branches


def _commit(ws: Path, name: str, text: str) -> None:
    (ws / name).write_text(text)
    subprocess.run(["git", "-C", str(ws), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(ws), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", name], check=True
    )


def test_collect_changes_sees_a_commit_the_agent_made_on_its_own_branch(tmp_path: Path) -> None:
    """The actual defect behind r-3981d7e0 c3 (2026-09-12, product install): an agent that committed its own
    work, rather than leaving it staged or untracked, was reported as having produced nothing at all --
    `dispatch_failures` even -- because `collect_changes` diffed only against the worktree's own `HEAD`, which
    the agent's own commit had just moved. `application/diffs.py`'s `worktree_diff` already gets this right
    (diffs against the merge-base with the main checkout); this mirrors it."""
    root = _repo(tmp_path)
    agent = _Agent(root)
    ws = agent.create_workspace("commits-its-work")
    _commit(ws, "new-file.txt", "produced\n")
    diff = agent.collect_changes(ws)
    assert diff.files == ["new-file.txt"]
    assert not diff.empty


def test_collect_changes_still_sees_uncommitted_and_untracked_changes(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    agent = _Agent(root)
    ws = agent.create_workspace("still-working")
    (ws / "a").write_text("changed\n")  # tracked, modified, never staged
    (ws / "untracked.txt").write_text("new\n")  # never added at all
    diff = agent.collect_changes(ws)
    assert diff.files == sorted(["a", "untracked.txt"])


def test_collect_changes_sees_both_a_commit_and_further_uncommitted_work(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    agent = _Agent(root)
    ws = agent.create_workspace("mixed")
    _commit(ws, "committed.txt", "first\n")
    (ws / "uncommitted.txt").write_text("second\n")
    diff = agent.collect_changes(ws)
    assert diff.files == sorted(["committed.txt", "uncommitted.txt"])


def test_a_fresh_attempt_does_not_inherit_a_stale_attempts_leftovers(tmp_path: Path) -> None:
    """A worktree left behind by an earlier attempt at the same task id must never become the next attempt's
    starting point: an untracked file, or even a commit, from attempt N-1 is not evidence attempt N produced
    anything. Before this fix `create_workspace` returned the existing directory unchanged whenever one was
    already there (`if wt.exists(): return wt`) -- which is exactly how, in the product install on 2026-09-12,
    a rejected attempt's committed files reappeared as an "already there" leftover in the next attempt at the
    same criterion."""
    root = _repo(tmp_path)
    agent = _Agent(root)
    ws = agent.create_workspace("retry-me")
    _commit(ws, "leftover.txt", "from a prior attempt\n")
    ws2 = agent.create_workspace("retry-me")  # a fresh attempt at the very same task id
    assert ws2 == ws
    assert not (ws2 / "leftover.txt").exists(), "a fresh attempt inherited a prior attempt's committed file"
    assert agent.collect_changes(ws2).empty, "a fresh attempt must start with nothing already produced"


def test_a_fresh_attempt_gets_a_fresh_branch_too(tmp_path: Path) -> None:
    """Not just the checkout: the branch itself must not carry a prior attempt's commits forward, or a diff
    taken against it (see `application/diffs.py`) would still show the old work even after the checkout looks
    clean."""
    root = _repo(tmp_path)
    agent = _Agent(root)
    ws = agent.create_workspace("retry-branch")
    _commit(ws, "leftover.txt", "from a prior attempt\n")
    agent.create_workspace("retry-branch")
    count = subprocess.run(
        ["git", "-C", str(root), "rev-list", "--count", "agent/retry-branch"], capture_output=True, text=True
    ).stdout.strip()
    assert count == "1", f"the recreated branch should hold only the base commit, not {count}"
