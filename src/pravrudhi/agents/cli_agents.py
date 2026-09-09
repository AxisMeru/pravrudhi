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
from typing import Any

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


def _usage(envelope: dict[str, Any]) -> tuple[int | None, int | None, int | None]:
    """`(tokens, cache_read, cache_write)` from a claude result envelope, or `(None, None, None)`.

    The measurement was always here. `claude -p --output-format json` returns a `usage` object beside the
    `total_cost_usd` this adapter already read, and it was discarded: every claude-code dispatch recorded its
    cost as zero, which `routing.spend` then skipped. A real envelope on 2026-09-09 carried input 2, output 4,
    cache write 47,852 and cache read 23,101 — so the cache counters dwarf the visible turn, and summing them
    away would hide the only number the prompt-cache target is about.

    `None` rather than zero when the envelope has no usage: a seat that cannot report must not be indistinguishable
    from a seat that cost nothing.
    """
    usage = envelope.get("usage")
    if not isinstance(usage, dict):
        return None, None, None

    def count(key: str) -> int:
        try:
            return int(usage.get(key) or 0)
        except (TypeError, ValueError):
            return 0

    read, write = count("cache_read_input_tokens"), count("cache_creation_input_tokens")
    return count("input_tokens") + count("output_tokens") + read + write, read, write


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
        tokens = read = write = None
        try:
            env = json.loads(out)
            if isinstance(env, dict):
                text = str(env.get("result", out))
                session = env.get("session_id")
                cost = env.get("total_cost_usd")
                tokens, read, write = _usage(env)
                if env.get("is_error"):
                    code = code or 1
        except ValueError:
            pass
        return AgentRun(
            agent=self.name, ok=code == 0, exit_code=code, wall_s=wall, text=text,
            workspace=workspace, session_id=session, cost_usd=cost, stderr_tail=err[-2000:],
            tokens=tokens, cache_read_tokens=read, cache_write_tokens=write,
        )


def _codex_usage(out: str) -> tuple[int | None, int | None, int | None]:
    """`(tokens, cache_read, cache_write)` from a `codex exec --json` event stream, or `(None, None, None)`.

    The last `turn.completed` event wins: a run can report several turns and the final one is the cumulative
    account of it.

    The arithmetic is NOT the same as Anthropic's, and conflating them would misreport this seat badly. A real
    trivial call returned input_tokens 15,296 with cached_input_tokens 12,160 — a four-word prompt cannot have
    15k of fresh input, so `cached_input_tokens` is a SUBSET of `input_tokens` here. Anthropic instead reports a
    small `input_tokens` beside a separate `cache_read_input_tokens`. So this sums input + output only, and
    carries the cached figure separately for the ratio; `_usage` adds its cache counters in because there they
    are genuinely disjoint.

    `reasoning_output_tokens` is recorded by the vendor and deliberately NOT added here. On the one call
    observed it was 0, so whether it is a subset of `output_tokens` or additional to it could not be
    determined, and guessing would inflate or understate every astra dispatch. It is left out rather than
    assumed; if a reasoning-heavy call ever shows output_tokens smaller than reasoning_output_tokens, that
    settles it the other way and this should change.
    """
    tokens = read = write = None
    for line in (out or "").splitlines():
        line = line.strip()
        if not line or not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict) or event.get("type") != "turn.completed":
            continue
        usage = event.get("usage")
        if not isinstance(usage, dict):
            continue

        def count(key: str, u: dict[str, Any] = usage) -> int:
            try:
                return int(u.get(key) or 0)
            except (TypeError, ValueError):
                return 0

        tokens = count("input_tokens") + count("output_tokens")
        read, write = count("cached_input_tokens"), count("cache_write_input_tokens")
    return tokens, read, write


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
        # `--json` makes codex emit JSONL events, which is the only way it reports token usage. Of astra's 32
        # recorded dispatches none carried a cost, on the dearest route in the table, purely because this ran
        # without it.
        cmd = [
            "codex", "exec", "--cd", str(Path(workspace).resolve()),
            "--sandbox", self.sandbox, "--skip-git-repo-check", "--json",
        ]
        if self.model:
            cmd += ["--model", self.model]
        if self.effort:
            cmd += ["-c", f"model_reasoning_effort={self.effort}"]
        cmd.append(prompt)
        code, out, err, wall = _run(cmd, workspace, timeout_s)
        tokens, read, write = _codex_usage(out)
        # `text` stays the WHOLE stream rather than the final message. `delegate.dispatch` classifies usage
        # limits over `run.text`, and a vendor announces a limit at the end of its output, so trimming this to
        # the assistant's last words would have hidden the limit sentence and silently broken the fallback
        # chain for the most expensive seat in the table.
        return AgentRun(
            agent=self.name, ok=code == 0, exit_code=code, wall_s=wall, text=out,
            workspace=workspace, stderr_tail=err[-2000:],
            tokens=tokens, cache_read_tokens=read, cache_write_tokens=write,
        )


def unified_agents(root: Path) -> dict[str, GitWorktreeMixin]:
    return {a.name: a for a in (ClaudeCodeAgent(root), CodexAgent(root))}


__all__ = ["ClaudeCodeAgent", "CodexAgent", "AgentRun", "Diff", "unified_agents"]
