"""Adapters for first-party coding-agent CLIs: Claude Code and Codex.

Each agent gets its own git worktree, is invoked non-interactively, and returns a diff. Nothing about Pravrudhi's
controller is specific to either vendor: both satisfy the same `CodingAgent` protocol, so an experiment can compare
them the way it compares two recipes, and neither is load-bearing.

Authentication is the operator's, not ours. These adapters never read, write or log a credential; they invoke a CLI
that is already signed in and report `available()` as false when it is not.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import signal
import subprocess
import time
from pathlib import Path

from pravrudhi.agents.base import AgentRun, Diff, GitWorktreeMixin


def _reap(proc: subprocess.Popen[str]) -> None:
    """Kill the whole process group, not just the child we started.

    A coding-agent CLI is a launcher: it spawns a sandbox helper, which spawns the work. `subprocess.run(timeout=)`
    kills only the direct child, so the grandchildren survive, keep talking to the provider and keep billing. Eight
    such orphans were found alive on this machine at once, the oldest three hours after its task had already
    returned a verdict and had its work merged. Nothing in the logs showed it: a finished dispatch and a still-
    running agent look identical from outside.
    """
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        return
    try:
        proc.wait(timeout=5)
        return
    except subprocess.TimeoutExpired:
        pass
    with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    with contextlib.suppress(subprocess.TimeoutExpired):
        proc.wait(timeout=5)


def _run(cmd: list[str], cwd: Path, timeout_s: int, env: dict[str, str] | None = None) -> tuple[int, str, str, float]:
    t0 = time.monotonic()
    proc = subprocess.Popen(
        cmd,
        cwd=cwd,
        # The child inherits this process's stdin otherwise. When the parent is itself an agent, that is a pipe
        # that never delivers, so the CLI warns "no stdin data received in 3s" and exits non-zero — three agents
        # that had finished their work correctly were rejected for it.
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={**os.environ, **(env or {})},
        start_new_session=True,  # its own process group, so the whole tree can be reaped together
    )
    try:
        out, err = proc.communicate(timeout=timeout_s)
        return proc.returncode, out, err, time.monotonic() - t0
    except subprocess.TimeoutExpired:
        _reap(proc)
        out, err = proc.communicate()
        return 124, out or "", (err or "") + f"\ntimeout after {timeout_s}s", time.monotonic() - t0
    except BaseException:
        _reap(proc)  # an interrupt must not leave an agent running either
        raise


class ClaudeCodeAgent(GitWorktreeMixin):
    """Claude Code driven through its documented headless mode.

    `claude -p <prompt> --output-format json` runs one non-interactive turn and prints a JSON envelope carrying the
    result text, the session id and the cost. Tools are restricted to what a code change needs; the worktree is the
    only directory the agent is given.
    """

    name = "claude-code"

    def __init__(self, root: Path, model: str | None = None, allowed_tools: str = "Read,Edit,Write,Grep,Glob,Bash") -> None:
        self.root, self.model, self.allowed_tools = Path(root), model, allowed_tools

    def available(self) -> bool:
        return shutil.which("claude") is not None

    def run(self, prompt: str, workspace: Path, timeout_s: int = 1800) -> AgentRun:
        cmd = ["claude", "-p", prompt, "--output-format", "json", "--allowed-tools", self.allowed_tools]
        if self.model:
            cmd += ["--model", self.model]
        code, out, err, wall = _run(cmd, workspace, timeout_s)
        text, session, cost = out, None, None
        try:
            env = json.loads(out)
            if isinstance(env, dict):
                text = str(env.get("result", out))
                session = env.get("session_id")
                cost = env.get("total_cost_usd")
                if env.get("is_error"):
                    code = code or 1
        except ValueError:
            pass
        return AgentRun(
            agent=self.name, ok=code == 0, exit_code=code, wall_s=wall, text=text,
            workspace=workspace, session_id=session, cost_usd=cost, stderr_tail=err[-2000:],
        )


class CodexAgent(GitWorktreeMixin):
    """Codex driven through `codex exec`, its documented non-interactive subcommand.

    The sandbox mode is passed through rather than defaulted to anything permissive: an agent editing this repository
    should not be able to reach the network or write outside its worktree unless the operator says so.
    """

    name = "codex"

    def __init__(
        self, root: Path, model: str | None = None, sandbox: str = "workspace-write", *, effort: str | None = None
    ) -> None:
        # `effort` maps to Codex's model_reasoning_effort. On this account only gpt-6-astra is available, and it is
        # expensive; running it at "low" is the one lever left for spend on the tasks that do not need deep
        # reasoning, so the default here is low and a caller raises it deliberately for hard work.
        self.root, self.model, self.sandbox = Path(root), model, sandbox
        self.effort = effort or os.environ.get("PRAVRUDHI_CODEX_EFFORT", "low")

    def available(self) -> bool:
        return shutil.which("codex") is not None

    def logged_in(self) -> bool:
        """True when Codex has stored credentials. Distinguishes 'not installed' from 'installed, not signed in'."""
        if not self.available():
            return False
        code, out, err, _ = _run(["codex", "login", "status"], self.root, 60)
        return code == 0 and "not logged in" not in (out + err).lower()

    def run(self, prompt: str, workspace: Path, timeout_s: int = 1800) -> AgentRun:
        # `--cd` must be absolute. The process already runs with the workspace as its working directory, so a
        # relative path is resolved a second time against the directory it has just moved into: a workspace of
        # `.worktrees/agent-x` becomes `.worktrees/agent-x/.worktrees/agent-x`, which does not exist, and codex
        # exits immediately with "No such file or directory (os error 2)". Every dispatch to this agent failed
        # that way, in under a second, and read as the agent refusing the work rather than never starting it.
        cmd = [
            "codex", "exec", "--cd", str(Path(workspace).resolve()),
            "--sandbox", self.sandbox, "--skip-git-repo-check",
        ]
        if self.model:
            cmd += ["--model", self.model]
        if self.effort:
            cmd += ["-c", f"model_reasoning_effort={self.effort}"]
        cmd.append(prompt)
        code, out, err, wall = _run(cmd, workspace, timeout_s)
        return AgentRun(
            agent=self.name, ok=code == 0, exit_code=code, wall_s=wall, text=out,
            workspace=workspace, stderr_tail=err[-2000:],
        )


def unified_agents(root: Path) -> dict[str, GitWorktreeMixin]:
    return {a.name: a for a in (ClaudeCodeAgent(root), CodexAgent(root))}


__all__ = ["ClaudeCodeAgent", "CodexAgent", "AgentRun", "Diff", "unified_agents"]
