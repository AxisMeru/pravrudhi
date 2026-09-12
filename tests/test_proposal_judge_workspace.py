"""S1: the proposal-mode judge must read the dispatch's own worktree, not a fresh worktree of HEAD.

Studio request r-4b5cdaf1 criteria 1 and 2, and the product install's r-3981d7e0 criteria 3 and 5, were judged
"not met" three times over with the judge saying the proposal directory is empty. It is not empty: a proposal
dispatch writes its README (and any scripts) under `proposals/requests/<id>/<idx>/` inside its OWN worktree
(`agents/base.py::GitWorktreeMixin.create_workspace` branches every dispatch from HEAD into
`.worktrees/agent-<task_id>`, and `application/delegate.py::dispatch` never merges or copies that worktree back
into the main tree - only a build criterion gets that integration step, via `application/integrate.py`).

`heartbeat._beat_obligations` already fixed this for build-mode criteria on 2026-09-11: the judge is pointed at
`root / ".worktrees" / f"agent-{task_id}"`, the dispatch's own worktree, instead of a fresh one
(`TestBuildJudgeReadsTheWorktree` in test_heartbeat_build_mode.py). Proposal mode never got the same fix - it
still hardcodes `workspace=None, where="the repository root"`, so `_default_judge` builds its OWN fresh worktree
off HEAD (via `agent.create_workspace("judge")`) and reads it, one directory over from every file the proposal
dispatch actually wrote. The judge is not wrong that the directory is empty in the tree it read; it read the
wrong tree.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from pravrudhi.application import heartbeat, requests


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A real repository with one commit and one request carrying a plain proposal-mode criterion."""
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test User")
    (root / "README.md").write_text("hello\n")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "initial")
    requests.capture(
        root, "assess whether the approach works", request_id="r-1",
        criteria=[requests.Criterion(text="a design note states the approach and how to verify it", source="operator")],
    )
    return root


def _worktree(root: Path, task_id: str) -> Path:
    from pravrudhi.agents.base import GitWorktreeMixin

    wt = root / ".worktrees" / f"agent-{GitWorktreeMixin.ref_safe(task_id)}"
    wt.parent.mkdir(exist_ok=True)
    _git(root, "worktree", "add", "-q", "-b", f"agent/{GitWorktreeMixin.ref_safe(task_id)}", str(wt), "HEAD")
    return wt


class TestProposalJudgeReadsTheWorktree:
    def test_the_default_judge_for_a_proposal_criterion_runs_in_the_dispatch_worktree(
        self, repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        assert heartbeat.dispatch_mode(requests.get(repo, "r-1").criteria[0]) == "proposal"

        from pravrudhi.application.delegate import Verdict

        def fake_run_wave(build_agent: Any, wave: list[Any], **kw: Any) -> list[Any]:
            task = wave[0]
            wt = _worktree(repo, task.spec.task_id)
            scratch = wt / "proposals" / "requests" / "r-1" / "0"
            scratch.mkdir(parents=True)
            (scratch / "README.md").write_text("the approach: ...\nevidence: ...\n")
            return [Verdict(
                task_id=task.spec.task_id, agent="fake", accepted=True,
                files=["proposals/requests/r-1/0/README.md"],
            )]

        seen: dict[str, Any] = {}

        def fake_default_judge(root: Path, *, workspace: Path | None = None) -> Any:
            seen["workspace"] = workspace

            def ask(*, prompt: str) -> str:
                seen["prompt"] = prompt
                return "verdict: not met\nthe proposal directory is empty"

            return ask

        monkeypatch.setattr(heartbeat.swarm, "run_wave", fake_run_wave)
        monkeypatch.setattr(heartbeat, "_default_judge", fake_default_judge)
        heartbeat._beat_obligations(repo, lambda _n, _m=None: object())

        assert seen["workspace"] == repo / ".worktrees" / "agent-request-r-1-0", (
            "the judge read a fresh worktree of HEAD instead of the dispatch's own worktree, "
            "which is exactly why it always finds the proposal directory empty"
        )
        assert "relative to the repository root:" not in seen["prompt"], (
            "the judge must be told to read its own worktree, not the bare repository root"
        )
        assert "agent's worktree" in seen["prompt"]

    def test_a_proposal_actually_produced_is_judged_met_when_the_judge_reads_the_real_worktree(
        self, repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """End to end with the REAL `_default_judge` (only the underlying coding-agent build is faked): a
        proposal dispatch writes its README into its own worktree, and the judge - reading that same worktree -
        must be able to see it and say met. Before the fix this reproduces the reported failure: the judge (given
        `workspace=None`) creates its own fresh worktree off HEAD, finds `proposals/requests/r-1/0/` missing or
        empty there, and refuses."""
        from pravrudhi.application.delegate import Verdict

        def fake_run_wave(build_agent: Any, wave: list[Any], **kw: Any) -> list[Any]:
            task = wave[0]
            wt = _worktree(repo, task.spec.task_id)
            scratch = wt / "proposals" / "requests" / "r-1" / "0"
            scratch.mkdir(parents=True)
            (scratch / "README.md").write_text("the approach: read the ledger; evidence: a parity row\n")
            return [Verdict(
                task_id=task.spec.task_id, agent="fake", accepted=True,
                files=["proposals/requests/r-1/0/README.md"],
            )]

        class JudgeAgent:
            name = "judge-agent"

            def create_workspace(self, task_id: str) -> Path:
                # The bug: given no workspace hint, the judge makes its OWN fresh worktree off HEAD - which
                # never has the file the dispatch above wrote into ITS worktree.
                return _worktree(repo, "judge-" + task_id)

            def run(self, prompt: str, workspace: Path, timeout_s: int = 0) -> Any:
                readme = workspace / "proposals" / "requests" / "r-1" / "0" / "README.md"
                text = "VERDICT: met\nfound it" if readme.exists() else "VERDICT: not met\nthe proposal directory is empty"
                return type("R", (), {"text": text})()

            def stop(self, workspace: Path) -> None:
                return None

        monkeypatch.setattr(heartbeat.swarm, "run_wave", fake_run_wave)
        monkeypatch.setattr(heartbeat, "_registry_build_agent", lambda *_a, **_k: JudgeAgent())

        _chose, reason, result = heartbeat._beat_obligations(repo, lambda _n, _m=None: object())

        assert result is not None and result["judged"] == "met", reason
        criterion = requests.get(repo, "r-1").criteria[0]
        assert criterion.met is True, reason
