"""An agent that writes into the main checkout instead of its worktree is rejected, not recorded as idle.

Two agents once did exactly this because their task quoted absolute paths under the main tree. Their worktrees
were empty, so they were recorded as having produced nothing, and the files they wrote were swept into the next
commit unreviewed. The worktree diff cannot see an escape; only the main tree can.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from pravrudhi.agents.base import AgentRun, Diff
from pravrudhi.application.delegate import TaskSpec, dispatch


def _repo(path: Path) -> Path:
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    (path / "a.txt").write_text("a\n")
    subprocess.run(["git", "-C", str(path), "add", "."], check=True)
    subprocess.run(["git", "-C", str(path), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "i"], check=True)
    return path


class EscapingAgent:
    """Writes its deliverable into the main checkout, leaving its worktree untouched."""

    name = "escaper"

    def __init__(self, root: Path, ws: Path) -> None:
        self.root, self.ws = root, ws

    def create_workspace(self, task_id: str) -> Path:
        return self.ws

    def run(self, prompt: str, workspace: Path, timeout_s: int = 0) -> AgentRun:
        (self.root / "escaped.py").write_text("x = 1\n")
        return AgentRun(agent=self.name, ok=True, exit_code=0, wall_s=0.1, text="", workspace=workspace)

    def collect_changes(self, workspace: Path) -> Diff:
        return Diff()


def test_a_write_into_the_main_checkout_is_named_as_an_escape(tmp_path: Path) -> None:
    root = _repo(tmp_path / "main")
    ws = tmp_path / "ws"
    ws.mkdir()
    v = dispatch(EscapingAgent(root, ws), TaskSpec("t", "do it", ("escaped.py",), validate="true"), log=lambda *a: None)
    assert not v.accepted
    assert any("outside its worktree" in r and "escaped.py" in r for r in v.reasons), v.reasons


def test_pre_existing_uncommitted_work_in_main_is_not_blamed_on_the_agent(tmp_path: Path) -> None:
    root = _repo(tmp_path / "main")
    (root / "mine.txt").write_text("the operator's own uncommitted work\n")
    ws = tmp_path / "ws"
    ws.mkdir()

    class Idle(EscapingAgent):
        def run(self, prompt: str, workspace: Path, timeout_s: int = 0) -> AgentRun:
            return AgentRun(agent=self.name, ok=True, exit_code=0, wall_s=0.1, text="", workspace=workspace)

    v = dispatch(Idle(root, ws), TaskSpec("t", "do it", ("x",), validate="true"), log=lambda *a: None)
    assert not any("outside its worktree" in r for r in v.reasons), v.reasons


def test_the_brief_tells_the_agent_to_write_relative_paths(tmp_path: Path) -> None:
    root = _repo(tmp_path / "main")
    ws = tmp_path / "ws"
    ws.mkdir()
    seen: dict[str, str] = {}

    class Recorder(EscapingAgent):
        def run(self, prompt: str, workspace: Path, timeout_s: int = 0) -> AgentRun:
            seen["prompt"] = prompt
            return AgentRun(agent=self.name, ok=True, exit_code=0, wall_s=0.1, text="", workspace=workspace)

    dispatch(Recorder(root, ws), TaskSpec("t", "do it", ("x",), validate="true"), log=lambda *a: None)
    assert "never write to an absolute path in the main checkout" in seen["prompt"]


def test_the_operators_own_concurrent_edits_in_main_are_not_blamed_on_the_agent(tmp_path: Path) -> None:
    """While a wave runs the operator keeps editing main. Two agents were once rejected for files the operator had
    changed in unrelated modules; only a change inside the task's own allowed paths is an escape."""
    root = _repo(tmp_path / "main")
    ws = tmp_path / "ws"
    ws.mkdir()

    class OperatorEditsMeanwhile(EscapingAgent):
        def run(self, prompt: str, workspace: Path, timeout_s: int = 0) -> AgentRun:
            (self.root / "unrelated_module.py").write_text("# the operator's edit, not the agent's\n")
            return AgentRun(agent=self.name, ok=True, exit_code=0, wall_s=0.1, text="", workspace=workspace)

    v = dispatch(OperatorEditsMeanwhile(root, ws), TaskSpec("t", "do it", ("deliverable.py",), validate="true"),
                 log=lambda *a: None)
    assert not any("outside its worktree" in r for r in v.reasons), v.reasons


def test_an_escape_into_a_new_directory_is_seen_even_though_git_collapses_it(tmp_path: Path) -> None:
    """The real case, and the one the first form of this check missed.

    A deliverable is almost always the first file in a new directory, and `git status --porcelain` collapses an
    untracked directory to a single entry — `proposals/probe/` rather than `proposals/probe/alpha/README.md`.
    A declared path is a glob over files, so the directory entry matches nothing, the escape goes unnamed, and
    the verdict reads "no change produced" while the agent's work sits unreviewed in the main checkout. That is
    the exact wording of the incident this module was written after, reproduced with a real agent.
    """
    root = _repo(tmp_path / "main")
    ws = tmp_path / "ws"
    ws.mkdir()

    class EscapesIntoANewDirectory(EscapingAgent):
        def run(self, prompt: str, workspace: Path, timeout_s: int = 0) -> AgentRun:
            out = self.root / "proposals" / "probe" / "alpha"
            out.mkdir(parents=True)
            (out / "README.md").write_text("the deliverable, written into the wrong tree\n")
            return AgentRun(agent=self.name, ok=True, exit_code=0, wall_s=0.1, text="", workspace=workspace)

    v = dispatch(
        EscapesIntoANewDirectory(root, ws),
        TaskSpec("t", "do it", ("proposals/probe/alpha/*",), validate="true"),
        log=lambda *a: None,
    )
    assert not v.accepted
    assert any("outside its worktree" in r and "proposals/probe/alpha/README.md" in r for r in v.reasons), v.reasons
