"""Qwen's tool loop through OpenCode, using the operator's Singapore credential.

No credential is persisted or passed in argv. Configuration contains an environment
reference only. Availability is a local check, not a claim that quota remains.
"""
from __future__ import annotations

import json
import os
import shlex
import shutil
import stat
from pathlib import Path

from pravrudhi.agents.base import AgentRun, GitWorktreeMixin
from pravrudhi.agents.cli_agents import _run
from pravrudhi.application.credentials import PROVIDERS, Secret, redact

PROVIDER = "pravrudhi-alibaba"


def credential() -> Secret:
    path = Path.home() / ".config/llm/dashscope.env"
    # Do not source shell code, follow symlinks, or accept permissive files.
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd) as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600 or info.st_uid != os.getuid():
            raise ValueError("DashScope credential must be an owner-owned regular file with mode 0600")
        for line in stream:
            parts = shlex.split(line, comments=True)
            if parts and parts[0] == "export":
                parts = parts[1:]
            if len(parts) == 1 and parts[0].startswith("DASHSCOPE_API_KEY="):
                value = parts[0].partition("=")[2]
                if value:
                    return Secret(provider="alibaba", value=value)
    raise ValueError("DashScope credential file has no DASHSCOPE_API_KEY")


def configuration(model: str) -> dict:
    return {
        "$schema": "https://opencode.ai/config.json",
        "enabled_providers": [PROVIDER],
        "model": f"{PROVIDER}/{model}",
        "small_model": f"{PROVIDER}/{model}",
        "share": "disabled",
        "autoupdate": False,
        "provider": {PROVIDER: {
            "npm": "@ai-sdk/openai-compatible",
            "name": "Alibaba (Singapore)",
            "options": {"baseURL": PROVIDERS["alibaba"].base_url,
                        "apiKey": "{env:DASHSCOPE_API_KEY}"},
            "models": {model: {"name": model, "tool_call": True}},
        }},
    }


class AlibabaAgent(GitWorktreeMixin):
    name = "opencode:alibaba"

    def __init__(self, root: Path, model: str = "qwen3-coder-plus") -> None:
        self.root, self.model = Path(root), model

    def status(self) -> tuple[bool, str]:
        if not shutil.which("opencode"):
            return False, "opencode CLI not installed"
        try:
            credential()
        except (OSError, ValueError):
            return False, "DashScope credential missing, malformed, or not owner-only (0600)"
        return True, "ready (quota and network not probed)"

    def available(self) -> bool:
        return self.status()[0]

    def run(self, prompt: str, workspace: Path, timeout_s: int = 1800) -> AgentRun:
        key = None
        try:
            key = credential()
            code, out, err, wall = _run(
                ["opencode", "run", "--format", "json", "--agent", "build", "-m", f"{PROVIDER}/{self.model}", prompt],
                workspace, timeout_s,
                env={"DASHSCOPE_API_KEY": key.reveal(),
                     "OPENCODE_CONFIG_CONTENT": json.dumps(configuration(self.model))},
            )
        except (OSError, ValueError):
            return AgentRun(self.name, False, 1, 0, "Alibaba loop could not start; check CLI and credential permissions",
                            workspace)
        # Exact replacement also protects credentials not matched by the generic redactor.
        out = redact(out.replace(key.reveal(), "[REDACTED]"))
        err = redact(err.replace(key.reveal(), "[REDACTED]"))
        finished, failed, session = False, False, None
        for line in out.splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if not isinstance(event, dict):
                continue
            session = event.get("sessionID") or session
            failed |= event.get("type") == "error"
            part = event.get("part") or {}
            if isinstance(part, dict):
                finished |= event.get("type") == "step_finish" and part.get("reason") == "stop"
        ok = code == 0 and finished and not failed
        if not ok and not err:
            err = "OpenCode reported an error or exited without a completed turn"
        return AgentRun(self.name, ok, code if code else (0 if ok else 1), wall, out, workspace,
                        session_id=session, stderr_tail=err[-2000:])
