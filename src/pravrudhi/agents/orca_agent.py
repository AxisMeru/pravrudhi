"""Orca as the scaffolding; the agents are driven directly inside it.

Orca already solves worktree lifecycle, terminal sessions, diff review and having several agents visible side by
side. Rebuilding any of that would be waste, so Pravrudhi does not: `OrcaWorkspace` is a thin wrapper over Orca's
CLI, and the controller decides only *what* to run and *how the result is scored*.

The agents are not delegated to Orca's own orchestration. Orca's CLI documents the pattern this module uses -- "use
this, not worktree create, for a fresh agent in the current checkout" -- so each agent is launched as an explicit
command in an Orca-managed terminal and this controller sends the prompt, waits for exit and reads the output. That
keeps three different agents (a hosted assistant, a second hosted assistant, and an open-weight model on the local
GPU) under one uniform interface without Pravrudhi owning any session machinery.

The open-weight agents reuse scaffolding too. A raw model is not a coding agent; rather than hand-roll a tool loop
for Qwen or GLM, they are driven through OpenCode pointed at the llama.cpp server on this box, so the local models
get a real agent loop from a tool built for it.
"""

from __future__ import annotations

import json
import shlex
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from pravrudhi.agents.base import AgentRun, Diff, GitWorktreeMixin, git

LOCAL_PROVIDER = "pravrudhi-local"


class OrcaUnavailable(RuntimeError):
    pass


def _orca(args: list[str], timeout_s: int = 120) -> tuple[int, str, str]:
    p = subprocess.run(["orca-ide", *args], capture_output=True, text=True, timeout=timeout_s)
    return p.returncode, p.stdout, p.stderr


def _envelope(text: str) -> dict[str, Any] | None:
    """Orca replies with {id, ok, result, _meta}; return `result` when the call succeeded."""
    try:
        v = json.loads(text)
    except ValueError:
        return None
    if not isinstance(v, dict):
        return None
    if v.get("ok") is False:
        return {"_error": (v.get("error") or {}).get("message", "orca call failed")}
    r = v.get("result")
    return r if isinstance(r, dict) else v


def _dig(d: dict[str, Any] | None, *path: str) -> Any:
    cur: object = d or {}
    for key in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


class OrcaWorkspace:
    """The scaffolding: repositories, worktrees and terminals, all owned by Orca."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def ready(self) -> bool:
        if shutil.which("orca-ide") is None:
            return False
        try:
            _, out, _ = _orca(["status"], timeout_s=60)
        except (subprocess.SubprocessError, OSError):
            return False
        return "runtimeReachable: true" in out or '"runtimeReachable": true' in out

    def register_repo(self) -> None:
        _orca(["repo", "add", "--path", str(self.root), "--json"])

    def create_worktree(self, name: str, base_ref: str = "HEAD") -> Path:
        if not self.ready():
            raise OrcaUnavailable("orca-ide runtime is not reachable (it needs a display server; see `orca-ide serve`)")
        self.register_repo()
        code, out, err = _orca(
            ["worktree", "create", "--name", name, "--repo", f"path:{self.root}", "--base-branch", base_ref, "--json"],
            timeout_s=300,
        )
        info = _envelope(out)
        path = _dig(info, "worktree", "path") or (info or {}).get("path")
        if not path:
            raise OrcaUnavailable(f"orca worktree create returned no path (exit {code}): {(err or out)[:300]}")
        return Path(path)

    def remove_worktree(self, workspace: Path) -> None:
        _orca(["worktree", "rm", "--worktree", f"path:{workspace}", "--json"], timeout_s=300)

    def run_command(self, workspace: Path, command: list[str], title: str, timeout_s: int) -> tuple[bool, str, str]:
        """Run one command in an Orca terminal in this worktree and return (ok, output, handle)."""
        cmd = " ".join(shlex.quote(c) for c in command)
        code, out, err = _orca(
            ["terminal", "create", "--worktree", f"path:{workspace}", "--title", title, "--command", cmd, "--json"],
            timeout_s=180,
        )
        info = _envelope(out) or {}
        handle = _dig(info, "terminal", "handle") or info.get("handle")
        if not handle:
            return False, str(info.get("_error") or (err or out)[:400]), ""
        _orca(
            ["terminal", "wait", "--terminal", str(handle), "--for", "exit", "--timeout-ms", str(int(timeout_s * 1000)),
                "--json"],
            timeout_s=timeout_s + 120,
        )
        rcode, rout, rerr = _orca(["terminal", "read", "--terminal", str(handle), "--limit", "4000", "--json"], timeout_s=180)
        payload = _envelope(rout) or {}
        rows = payload.get("lines") or payload.get("rows") or payload.get("output")
        text = "\n".join(r if isinstance(r, str) else json.dumps(r) for r in rows) if isinstance(rows, list) else rout
        return rcode == 0, text, str(handle)

    def close_terminal(self, handle: str) -> None:
        if handle:
            _orca(["terminal", "close", "--terminal", handle, "--json"])


def headless_command(agent_id: str, prompt: str, model: str | None = None) -> list[str]:
    """The non-interactive invocation for each agent, run inside an Orca terminal.

    Open-weight models go through OpenCode against the local llama.cpp endpoint, so they get a genuine agent loop
    rather than a bespoke one written here.
    """
    if agent_id == "claude":
        # Orca runs this through a shell string it builds itself, so there is no environment dict to pass and
        # the credential has to ride inside the command. `env` is a real binary and each item is quoted
        # individually by `run_command`, so this survives the join intact. Operator instruction of 2026-09-10:
        # this project does not use the personal Claude account, and an Orca terminal inherits the desktop
        # session's environment, which is exactly where that account lives.
        from pravrudhi.agents.account import claude_env

        # `require=False`: this function BUILDS argv, it does not run it. The property that matters here is
        # that CLAUDE_CONFIG_DIR is set, which makes the personal account unreachable whether or not the
        # project's own credential is present yet -- and an unprovisioned directory then fails as the CLI's
        # own login error rather than ours. Refusing to build a string was over-eager and broke five tests
        # that are about command shape, not credentials; the refusal belongs in `run()`, where it is.
        prefix = [f"{k}={v}" for k, v in sorted(claude_env(require=False).items())]
        return ["env", *prefix, "claude", "-p", prompt, "--output-format", "json",
                "--allowed-tools", "Read,Edit,Write,Grep,Glob,Bash"]
    if agent_id == "codex":
        return ["codex", "exec", "--sandbox", "workspace-write", "--skip-git-repo-check", prompt]
    if agent_id == "local":
        return ["opencode", "run", "--format", "json", "-m", f"{LOCAL_PROVIDER}/{model or 'glm-4.7-flash'}", prompt]
    raise OrcaUnavailable(f"no headless invocation known for agent {agent_id!r}")


class OrcaAgent(GitWorktreeMixin):
    """One agent, hosted in Orca's scaffolding. `agent_id` is claude, codex or local."""

    def __init__(self, root: Path, agent_id: str = "claude", model: str | None = None, timeout_s: int = 1800) -> None:
        self.root, self.agent_id, self.model, self.timeout_s = Path(root), agent_id, model, timeout_s
        self.name = f"orca:{agent_id}" + (f":{model}" if model else "")
        self.ws = OrcaWorkspace(self.root)
        self._terminals: dict[str, str] = {}

    def runtime_ready(self) -> bool:
        return self.ws.ready()

    def available(self) -> bool:
        if not self.ws.ready():
            return False
        binary = {"claude": "claude", "codex": "codex", "local": "opencode"}.get(self.agent_id)
        return bool(binary and shutil.which(binary))

    def create_workspace(self, task_id: str, base_ref: str = "HEAD") -> Path:
        return self.ws.create_worktree(f"pravrudhi-{task_id}", base_ref)

    def run(self, prompt: str, workspace: Path, timeout_s: int | None = None) -> AgentRun:
        timeout_s = timeout_s or self.timeout_s
        t0 = time.monotonic()
        ok, text, handle = self.ws.run_command(
            workspace, headless_command(self.agent_id, prompt, self.model), f"pravrudhi-{self.agent_id}", timeout_s
        )
        if handle:
            self._terminals[str(workspace)] = handle
        return AgentRun(
            agent=self.name, ok=ok, exit_code=0 if ok else 1, wall_s=time.monotonic() - t0,
            text=text, workspace=workspace, session_id=handle or None, stderr_tail="" if ok else text[-2000:],
        )

    def collect_changes(self, workspace: Path) -> Diff:
        """Read the diff with git from the worktree Orca created, so scoring never depends on Orca."""
        return GitWorktreeMixin.collect_changes(self, workspace)

    def stop(self, workspace: Path) -> None:
        self.ws.close_terminal(self._terminals.pop(str(workspace), ""))
        self.ws.remove_worktree(workspace)


__all__ = ["OrcaAgent", "OrcaWorkspace", "OrcaUnavailable", "headless_command", "LOCAL_PROVIDER", "AgentRun", "Diff", "git"]
