"""A tool-calling agent loop that does not depend on any vendor's CLI.

`HostedAgent` asks a hosted free-tier model for one whole-file JSON reply, because Qwen, GLM and DeepSeek have no
CLI of their own and cannot drive a read-edit-run loop the way Claude Code and Codex do. The operator's decision on
2026-09-06 was not to bend the `claude` CLI to point at a non-Anthropic endpoint instead: Pravrudhi gets its own
first-class tool-calling loop, so an open or other cloud model can do real agentic work - reading files, searching,
running checks, iterating - rather than answering once and hoping the whole change fit in a single shot.

`LoopAgent` drives that loop itself against any OpenAI-compatible chat endpoint: the operator's hosted free-tier
client by default, or a local llama-server reached through `base_url`. Each turn is parsed two ways because not
every model speaks the same dialect - native `tool_calls` when the endpoint returns them, and a strict JSON object
in the message content when it does not. Which shape a given turn used is detected from the response itself, never
assumed from the model's name.

The same two boundaries `HostedAgent` enforces apply here, extended to a whole session rather than one reply: a
tool call may not write outside the paths a dispatch brief named, may not touch anything outside the worktree, and
`run_command` may only invoke a prefix declared in `loop_tools.yaml`. Every step - the tool called, its arguments,
its result - is appended to `<worktree>/.pravrudhi/loop.jsonl` so a run can be read back afterwards.
"""

from __future__ import annotations

import json
import re
import shlex
import shutil
import subprocess
import time
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path
from typing import Any, TextIO

import yaml

from pravrudhi.agents.base import AgentRun, GitWorktreeMixin
from pravrudhi.models import hosted

PACKAGED_TOOLS_CONFIG = Path(__file__).resolve().parents[1] / "assets" / "configs" / "loop_tools.yaml"

ALLOWED_PATHS_LINE = re.compile(r"You may create or modify ONLY these paths:\s*(.+)")

ChatFn = Callable[[list[dict[str, Any]], list[dict[str, Any]]], dict[str, Any]]

TOOLS_SCHEMA: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read the full text content of one file in the worktree.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write full text content to one file in the worktree, creating it if absent.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_dir",
            "description": "List the entries of one directory in the worktree.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search",
            "description": "Search files under a directory in the worktree for a text pattern.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}, "path": {"type": "string"}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": "Run one allow-listed shell command inside the worktree.",
            "parameters": {
                "type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "finish",
            "description": "End the session, reporting what was done.",
            "parameters": {
                "type": "object",
                "properties": {"summary": {"type": "string"}},
                "required": ["summary"],
            },
        },
    },
]


@dataclass(frozen=True)
class ToolsConfig:
    """Settings for the loop agent's command tool: the allowed command prefixes and a timeout."""
    allow_prefixes: tuple[str, ...]
    command_timeout_s: int


def load_tools_config(path: Path | None = None) -> ToolsConfig:
    """Read the tools configuration from a file, or the packaged default."""
    raw: dict[str, Any] = yaml.safe_load((path or PACKAGED_TOOLS_CONFIG).read_text()) or {}
    return ToolsConfig(
        allow_prefixes=tuple(str(p) for p in (raw.get("allow_prefixes") or ())),
        command_timeout_s=int(raw.get("command_timeout_s") or 120),
    )


def _allowed_patterns(prompt: str) -> tuple[str, ...]:
    """The globs a dispatch brief permits, parsed from the line `dispatch` appends to every task prompt."""
    m = ALLOWED_PATHS_LINE.search(prompt)
    if not m:
        return ()
    line = m.group(1).splitlines()[0].rstrip(". ")
    return tuple(p.strip() for p in line.split(",") if p.strip())


def _is_safe_relative(path: str) -> bool:
    if not path or path.startswith("/") or path.startswith("\\"):
        return False
    return ".." not in Path(path).parts


def _resolve_in_worktree(path: str, workspace: Path) -> Path | None:
    """`path` resolved inside `workspace`, or None when it is absolute, traverses with `..`, or resolves outside."""
    if not _is_safe_relative(path):
        return None
    root = workspace.resolve()
    resolved = (root / path).resolve()
    if resolved != root and root not in resolved.parents:
        return None
    return resolved


def _read_file(path: str, workspace: Path) -> dict[str, Any]:
    resolved = _resolve_in_worktree(path, workspace)
    if resolved is None:
        return {"error": f"refused: path is outside the worktree or contains '..': {path}"}
    if not resolved.exists() or not resolved.is_file():
        return {"error": f"no such file: {path}"}
    return {"content": resolved.read_text()}


def _write_file(path: str, content: str, workspace: Path, patterns: tuple[str, ...]) -> dict[str, Any]:
    resolved = _resolve_in_worktree(path, workspace)
    if resolved is None:
        return {"error": f"refused: path is outside the worktree or contains '..': {path}"}
    if not any(fnmatch(path, pat) for pat in patterns):
        return {"error": f"refused: path is not in the allowed paths: {path}"}
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(content)
    return {"ok": True, "path": path}


def _list_dir(path: str, workspace: Path) -> dict[str, Any]:
    resolved = _resolve_in_worktree(path or ".", workspace)
    if resolved is None:
        return {"error": f"refused: path is outside the worktree or contains '..': {path}"}
    if not resolved.exists() or not resolved.is_dir():
        return {"error": f"no such directory: {path}"}
    return {"entries": sorted(p.name + ("/" if p.is_dir() else "") for p in resolved.iterdir())}


def _search(query: str, path: str, workspace: Path) -> dict[str, Any]:
    resolved = _resolve_in_worktree(path or ".", workspace)
    if resolved is None:
        return {"error": f"refused: path is outside the worktree or contains '..': {path}"}
    if not query:
        return {"error": "search requires a non-empty query"}
    if shutil.which("rg"):
        try:
            r = subprocess.run(
                ["rg", "-n", "--max-count", "200", "--", query, str(resolved)],
                cwd=workspace, capture_output=True, text=True, timeout=30,
            )
        except (subprocess.TimeoutExpired, OSError) as e:
            return {"error": f"search failed: {e}"}
        return {"matches": r.stdout.splitlines()[:200]}
    pattern = re.compile(re.escape(query))
    files = [resolved] if resolved.is_file() else sorted(p for p in resolved.rglob("*") if p.is_file())
    matches: list[str] = []
    root = workspace.resolve()
    for f in files:
        try:
            text = f.read_text(errors="ignore")
        except OSError:
            continue
        for i, line in enumerate(text.splitlines(), start=1):
            if pattern.search(line):
                matches.append(f"{f.relative_to(root)}:{i}:{line}")
                if len(matches) >= 200:
                    return {"matches": matches}
    return {"matches": matches}


def _allowed_command(cmd: str, prefixes: tuple[str, ...]) -> bool:
    return any(cmd == p or cmd.startswith(p + " ") for p in prefixes)


def _run_command(cmd: str, workspace: Path, prefixes: tuple[str, ...], timeout_s: int) -> dict[str, Any]:
    if not cmd or not _allowed_command(cmd, prefixes):
        return {"error": f"refused: command is not on the allow-list: {cmd}"}
    try:
        argv = shlex.split(cmd)
    except ValueError as e:
        return {"error": f"refused: could not parse command: {e}"}
    try:
        r = subprocess.run(argv, cwd=workspace, capture_output=True, text=True, timeout=timeout_s)
    except (subprocess.TimeoutExpired, OSError) as e:
        return {"error": f"command failed: {e}"}
    return {"exit_code": r.returncode, "stdout": r.stdout[-4000:], "stderr": r.stderr[-2000:]}


def _message_from_response(response: dict[str, Any]) -> dict[str, Any]:
    choices = response.get("choices") or [{}]
    message = choices[0].get("message") if choices else {}
    return dict(message or {})


def _parse_tool_call(message: dict[str, Any]) -> tuple[str, dict[str, Any], bool] | None:
    """The tool name, its arguments, and whether native tool-calling was used - detected from the response's own
    shape rather than assumed from the model. None when neither shape parses."""
    tool_calls = message.get("tool_calls") or []
    if tool_calls:
        call = tool_calls[0]
        fn = call.get("function") or {}
        name = str(fn.get("name") or "")
        raw_args = fn.get("arguments")
        try:
            args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args or {})
        except (ValueError, TypeError):
            args = {}
        return (name, args, True) if name else None
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        return None
    try:
        payload = json.loads(content)
    except (ValueError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None
    name = str(payload.get("tool") or "")
    args = payload.get("args")
    args = args if isinstance(args, dict) else {}
    return (name, args, False) if name else None


def _system_brief(patterns: tuple[str, ...]) -> str:
    writable = ", ".join(patterns) or "nothing - every write_file call will be refused"
    return (
        "You are an autonomous coding agent working inside your own git worktree. Use the read_file, write_file, "
        "list_dir, search and run_command tools to make the requested change, then call finish with a short "
        "summary of what you did. Call tools with native function-calling if you support it. If you do not, "
        "reply with exactly one JSON object and nothing else, shaped like "
        '{"tool": "<name>", "args": {...}}. '
        f"You may create or modify only these paths: {writable}. Any other path, any path containing '..', and "
        "any command not on the allow-list is refused rather than run."
    )


def _call_openai_compatible(base_url: str, model: str, messages: list[dict[str, Any]],
                             tools: list[dict[str, Any]]) -> dict[str, Any]:
    """One chat completion against a local OpenAI-compatible endpoint (e.g. llama-server). No key, no ledger: a
    local endpoint has no quota to protect."""
    body: dict[str, Any] = {"model": model, "messages": messages}
    if tools:
        body["tools"] = tools
    req = urllib.request.Request(
        base_url.rstrip("/") + "/chat/completions",
        data=json.dumps(body).encode(),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=120) as resp:  # noqa: S310 - operator-configured local endpoint
        return dict(json.loads(resp.read().decode()))


class LoopAgent(GitWorktreeMixin):
    """A real tool-calling loop against any OpenAI-compatible chat endpoint.

    Every gate `hosted.chat()` enforces still applies when no `base_url` is given: the hosted surface stays
    refused unless the operator opted in. What differs from `HostedAgent` is that this class talks to the
    endpoint directly rather than through `hosted.chat()`'s text-only wrapper, because a tool-calling loop needs
    the structured `tool_calls` a plain reply string throws away.
    """

    name = "loop"

    def __init__(
        self,
        root: Path,
        model: str = "qwen3-coder-plus",
        max_steps: int = 20,
        base_url: str | None = None,
        chat: ChatFn | None = None,
        tools_config_path: Path | None = None,
    ) -> None:
        self.root = Path(root)
        self.model = model
        self.max_steps = max_steps
        self.base_url = base_url
        self._chat: ChatFn = chat if chat is not None else self._default_chat
        self._tools_config = load_tools_config(tools_config_path)

    def available(self) -> bool:
        if self.base_url:
            return True
        return hosted.available()[0]

    def _default_chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
        if self.base_url:
            return _call_openai_compatible(self.base_url, self.model, messages, tools)
        hosted.assert_admin(None)
        freellm = hosted._load()
        return dict(freellm.FreeTierClient().chat(self.model, messages, tools=tools))

    def _execute(self, name: str, args: dict[str, Any], workspace: Path, patterns: tuple[str, ...]) -> dict[str, Any]:
        if name == "read_file":
            return _read_file(str(args.get("path", "")), workspace)
        if name == "write_file":
            return _write_file(str(args.get("path", "")), str(args.get("content", "")), workspace, patterns)
        if name == "list_dir":
            return _list_dir(str(args.get("path", ".")), workspace)
        if name == "search":
            return _search(str(args.get("query", "")), str(args.get("path", ".")), workspace)
        if name == "run_command":
            return _run_command(
                str(args.get("command", "")), workspace,
                self._tools_config.allow_prefixes, self._tools_config.command_timeout_s,
            )
        return {"error": f"unknown tool: {name}"}

    @staticmethod
    def _log(fh: TextIO, entry: dict[str, Any]) -> None:
        fh.write(json.dumps(entry, sort_keys=True, default=str) + "\n")
        fh.flush()

    @staticmethod
    def _append_turn(
        messages: list[dict[str, Any]], message: dict[str, Any], name: str, result: dict[str, Any], native: bool,
    ) -> None:
        if native:
            messages.append({
                "role": "assistant", "content": message.get("content"), "tool_calls": message.get("tool_calls"),
            })
            call_id = str((message.get("tool_calls") or [{}])[0].get("id") or "call_0")
            messages.append({"role": "tool", "tool_call_id": call_id, "name": name, "content": json.dumps(result)})
        else:
            messages.append({"role": "assistant", "content": message.get("content")})
            messages.append({"role": "user", "content": f"tool result for {name}: {json.dumps(result)}"})

    def run(self, prompt: str, workspace: Path, timeout_s: int = 1800) -> AgentRun:
        t0 = time.monotonic()
        patterns = _allowed_patterns(prompt)
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": _system_brief(patterns)},
            {"role": "user", "content": prompt},
        ]
        transcript = workspace / ".pravrudhi" / "loop.jsonl"
        transcript.parent.mkdir(parents=True, exist_ok=True)
        with transcript.open("a") as log:
            for step in range(1, self.max_steps + 1):
                if time.monotonic() - t0 > timeout_s:
                    self._log(log, {"step": step, "event": "timeout"})
                    return AgentRun(
                        agent=self.name, ok=False, exit_code=1, wall_s=time.monotonic() - t0,
                        text=f"wall-clock budget of {timeout_s}s exceeded", workspace=workspace,
                    )
                try:
                    response = self._chat(messages, TOOLS_SCHEMA)
                except Exception as e:  # the endpoint can refuse (opt-in off, quota spent) at call time
                    self._log(log, {"step": step, "event": "chat_error", "error": str(e)})
                    return AgentRun(
                        agent=self.name, ok=False, exit_code=1, wall_s=time.monotonic() - t0,
                        text=f"chat call failed: {e}", workspace=workspace,
                    )
                message = _message_from_response(response)
                parsed = _parse_tool_call(message)
                if parsed is None:
                    self._log(log, {"step": step, "event": "unparseable", "content": message.get("content")})
                    return AgentRun(
                        agent=self.name, ok=False, exit_code=1, wall_s=time.monotonic() - t0,
                        text=f"step {step}: could not parse a tool call from the model's reply", workspace=workspace,
                    )
                name, args, native = parsed
                if name == "finish":
                    summary = str(args.get("summary") or "")
                    self._log(log, {"step": step, "event": "finish", "summary": summary, "native": native})
                    return AgentRun(
                        agent=self.name, ok=True, exit_code=0, wall_s=time.monotonic() - t0,
                        text=summary, workspace=workspace,
                    )
                result = self._execute(name, args, workspace, patterns)
                self._log(log, {"step": step, "event": "tool", "tool": name, "args": args, "result": result,
                                 "native": native})
                self._append_turn(messages, message, name, result, native)
        return AgentRun(
            agent=self.name, ok=False, exit_code=1, wall_s=time.monotonic() - t0,
            text=f"reached max_steps ({self.max_steps}) without calling finish", workspace=workspace,
        )


__all__ = ["LoopAgent", "ToolsConfig", "load_tools_config"]
