"""An unrunnable route must hand its work on, not stop the wave.

The router already falls back when an agent hits a vendor usage limit — four recorded hops on disk, including
between the operator's two Alibaba accounts. It did not fall back when `build_agent` returned None, which is what
happens when a CLI is not installed or simply not on this process's PATH. On 2026-09-08 that gap cost five hours:
`systemd --user` had no nvm bin directory, `opencode` was invisible, and the heartbeat rejected every dispatch
hourly with "no agent available" while `claude-code` sat ready and unused.
"""

from __future__ import annotations

from pathlib import Path

from pravrudhi.application import swarm
from pravrudhi.application.delegate import TaskSpec
from pravrudhi.application.swarm import SwarmTask


class _Agent:
    def __init__(self, name: str) -> None:
        self.name = name
        self.root = Path(".")

    def create_workspace(self, task_id: str) -> Path:
        return Path(".")

    def run(self, prompt: str, workspace: Path, timeout_s: int = 0):  # type: ignore[no-untyped-def]
        from pravrudhi.agents.base import AgentRun

        return AgentRun(agent=self.name, ok=True, exit_code=0, wall_s=0.1, text="", workspace=workspace)

    def collect_changes(self, workspace: Path):  # type: ignore[no-untyped-def]
        from pravrudhi.agents.base import Diff

        return Diff(files=["x.py"])


def test_a_route_whose_agent_cannot_run_hands_the_task_to_one_that_can(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    tried: list[str] = []

    def build(name: str, model: str | None):  # type: ignore[no-untyped-def]
        tried.append(name)
        # The shape of the outage: the preferred seat is invisible, a dearer one is ready.
        return None if "opencode" in name else _Agent(name)

    logs: list[str] = []
    task = SwarmTask(TaskSpec("t1", "do it", ("x.py",), validate="true"), "mechanical", why="")
    from pravrudhi.application import delegate

    monkeypatch.setattr(delegate, "validate_in", lambda *a, **k: (True, ""))
    verdicts = swarm.run_wave(build, [task], log=logs.append, root=tmp_path)

    assert len(verdicts) == 1
    assert verdicts[0].accepted, verdicts[0].reasons
    assert any("opencode" in name for name in tried), tried
    assert verdicts[0].agent != "opencode", verdicts[0].agent
    assert any("cannot run here" in line or "unavailable" in line for line in logs), logs
