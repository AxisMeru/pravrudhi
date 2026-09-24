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

import contextlib
import json
import os
import shlex
import shutil
import subprocess
import tempfile
import time
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

from pravrudhi.agents.base import AgentRun, Diff, GitWorktreeMixin, git

LOCAL_PROVIDER = "pravrudhi-local"

PROMPT_DIR_ENV = "PRAVRUDHI_ORCA_PROMPT_DIR"
"""Overrides where prompt files are written. Default: `<root>/.pravrudhi/orca-prompts` (ignored run state).
Never the system temp dir: on this host /tmp is a 16 GB tmpfs that large benchmark prompts have filled."""


def default_prompt_dir(root: Path) -> Path:
    raw = os.environ.get(PROMPT_DIR_ENV)
    return Path(raw).expanduser() if raw else Path(root) / ".pravrudhi" / "orca-prompts"


def write_prompt_file(directory: Path, prompt: str) -> Path:
    """Write `prompt` (UTF-8) to a fresh private file under `directory` and return its path.

    The prompt cannot go on argv: an Orca terminal execs the CLI with each argument as one string, and Linux caps
    a single argv string at MAX_ARG_STRLEN (128 KiB), so a large prompt fails with E2BIG before the CLI starts --
    the bug 97606bf fixed for `panel.ask_vendor` and `cli_agents`. A terminal has no stdin pipe to hand over, so
    the shell redirects stdin from this file instead. `mkstemp` creates it 0600 with O_EXCL, so concurrent runs
    never collide and no other user can read a prompt; the directory is created 0700.
    """
    directory = Path(directory)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix="prompt-", suffix=".txt", dir=directory)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(prompt.encode("utf-8"))
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(name)
        raise
    return Path(name)


def terminal_command(argv: list[str], stdin_file: Path | None = None) -> str:
    """The shell string an Orca terminal runs: every argument quoted, stdin redirected from `stdin_file`.

    `< path` is the only unquoted shell syntax emitted, and the path itself is `shlex.quote`d, so a run directory
    containing spaces, quotes, `$` or backticks is passed through literally.
    """
    cmd = " ".join(shlex.quote(c) for c in argv)
    return f"{cmd} < {shlex.quote(str(stdin_file))}" if stdin_file is not None else cmd


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

    def run_command(
        self, workspace: Path, command: list[str], title: str, timeout_s: int, *, stdin_file: Path | None = None
    ) -> tuple[bool, str, str]:
        """Run one command in an Orca terminal in this worktree and return (ok, output, handle).

        `stdin_file`, when given, is redirected onto the command's stdin (see `terminal_command`).
        """
        cmd = terminal_command(command, stdin_file)
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


def headless_command(
    agent_id: str, *, model: str | None = None, seat_env: Mapping[str, str] | None = None
) -> list[str]:
    """The non-interactive invocation for each agent, run inside an Orca terminal, WITHOUT the prompt.

    The prompt is read from stdin, which `OrcaAgent.run` redirects from a private file (`write_prompt_file`,
    `terminal_command`): a positional prompt over 128 KiB fails with E2BIG. Each CLI reads stdin when no prompt
    is given -- `claude -p` and `codex exec` per their `--help`, and `opencode run` (1.18.x) reads
    `Bun.stdin.text()` whenever stdin is not a TTY. `model` is keyword-only so a caller still passing a prompt
    positionally gets a TypeError rather than having its prompt read as a model name.

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
        #
        # `seat_env` names the seat explicitly. `OrcaAgent.run` passes it so a usage limit can move to the next
        # seat; without it the command rides whichever seat `claude_env` would pick right now.
        chosen = dict(seat_env) if seat_env is not None else claude_env(require=False)
        prefix = [f"{k}={v}" for k, v in sorted(chosen.items())]
        return ["env", *prefix, "claude", "-p", "--output-format", "json",
                "--allowed-tools", "Read,Edit,Write,Grep,Glob,Bash"]
    if agent_id == "codex":
        return ["codex", "exec", "--sandbox", "workspace-write", "--skip-git-repo-check"]
    if agent_id == "local":
        return ["opencode", "run", "--format", "json", "-m", f"{LOCAL_PROVIDER}/{model or 'glm-4.7-flash'}"]
    raise OrcaUnavailable(f"no headless invocation known for agent {agent_id!r}")


LIMITS_ID = {"claude": "orca:claude", "codex": "orca:codex", "local": "orca:local"}
"""The `limits.yaml` key for each hosted agent. Not `OrcaAgent.name`, which carries the model
(`orca:claude:<model>`) and so would match no entry."""


class OrcaAgent(GitWorktreeMixin):
    """One agent, hosted in Orca's scaffolding. `agent_id` is claude, codex or local."""

    def __init__(
        self, root: Path, agent_id: str = "claude", model: str | None = None, timeout_s: int = 1800,
        *, prompt_dir: Path | None = None,
    ) -> None:
        self.root, self.agent_id, self.model, self.timeout_s = Path(root), agent_id, model, timeout_s
        # Outside the worktree on purpose: a prompt file inside it would show up in `collect_changes`.
        self.prompt_dir = Path(prompt_dir) if prompt_dir is not None else default_prompt_dir(self.root)
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
        """One turn in an Orca terminal; for `claude`, on the highest-precedence seat, moving down on a limit.

        The Claude path mirrors `ClaudeCodeAgent.run` step for step, because it is the same CLI spending the
        same seats: the seat is chosen by `select_seat`, a usage limit marks THAT seat (`claude-code:<id>`, the
        key `ClaudeCodeAgent` uses, so a seat spent here is skipped there too) until the vendor's stated reset,
        and the next seat is tried; an ordinary failure is returned as it stands; with every seat spent the last
        limited run is returned for the router, and with no seat able to serve at all the documented refusal is
        raised. The seat rides inside the terminal command as `env CLAUDE_CONFIG_DIR=...` -- Orca builds its own
        shell, so there is no environment dict to hand over, but a per-seat directory in argv is honoured.

        Codex and the local model have no seats (one login each, or none), so there is nothing to fail over to:
        the run is returned and a limit reaches the router, which cools `orca:codex` from `limits.yaml`.
        """
        timeout_s = timeout_s or self.timeout_s
        if self.agent_id != "claude":
            argv = headless_command(self.agent_id, model=self.model)  # raises for an unknown agent before any file
            return self._attempt(prompt, workspace, timeout_s, argv)

        from pravrudhi.agents.account import claude_env, select_seat
        from pravrudhi.application import availability

        spent: list[str] = []
        last: AgentRun | None = None
        while True:
            seat = select_seat(self.root)
            if seat is None or seat.id in spent:
                break
            spent.append(seat.id)
            argv = headless_command(
                self.agent_id, model=self.model, seat_env={"CLAUDE_CONFIG_DIR": str(seat.config_dir)}
            )
            last = self._attempt(prompt, workspace, timeout_s, argv)
            whole = f"{last.text}\n{last.stderr_tail}"
            if availability.classify(LIMITS_ID["claude"], whole, last.exit_code) != "limited":
                return last
            availability.mark_limited(self.root, seat.cooldown_key, until=availability.reset_at(whole))

        if last is None:
            claude_env(root=self.root)  # no seat can serve: raise the documented refusal rather than guess
        # An Orca terminal's `ok` is whether Orca could READ the terminal, not the CLI's exit status, so a spent
        # seat's refusal can arrive as ok=True. `ClaudeCodeAgent` returns it failed (the CLI's JSON envelope says
        # `is_error`), and a caller such as `delegate.dispatch` only classifies a failed run -- so it is returned
        # failed here too, or the limit would never reach the router.
        assert last is not None
        return replace(last, ok=False, exit_code=last.exit_code or 1, stderr_tail=last.text[-2000:])

    def _attempt(self, prompt: str, workspace: Path, timeout_s: int, argv: list[str]) -> AgentRun:
        """One dispatch in one Orca terminal. Knows nothing about seats beyond the argv it is handed."""
        t0 = time.monotonic()
        prompt_file = write_prompt_file(self.prompt_dir, prompt)
        try:
            ok, text, handle = self.ws.run_command(
                workspace, argv, f"pravrudhi-{self.agent_id}", timeout_s, stdin_file=prompt_file
            )
        finally:
            # `run_command` returns after `terminal wait --for exit`, so the CLI has finished reading. On a wait
            # timeout the command is still running, but the shell opened `< file` before exec, so unlinking does
            # not cut off its read (the open descriptor keeps the inode). The one case that can lose the prompt
            # is Orca returning before its terminal's shell reached the redirect (a wait that errors out at
            # once); the CLI then fails loudly with "No such file" rather than running on a wrong prompt. A hard
            # crash of this process between write and unlink leaves one 0600 file in the prompt directory.
            with contextlib.suppress(OSError):
                prompt_file.unlink()
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
