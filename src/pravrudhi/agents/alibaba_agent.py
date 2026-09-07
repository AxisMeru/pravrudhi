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
from typing import Any

from pravrudhi.agents.base import AgentRun, GitWorktreeMixin
from pravrudhi.agents.cli_agents import _run
from pravrudhi.application.credentials import PROVIDERS, Secret, redact

PROVIDER = "pravrudhi-alibaba"

# The same vendor reached two ways. The free tier and the Lite Plan have different endpoints, different keys and
# different models, so they are two providers here rather than one with a flag: a plan key sent to the free-tier
# endpoint fails in a way that reads like a bad key, which is a confusing hour to spend.
CREDENTIAL_NAMES = {"alibaba": "dashscope.env", "alibaba-plan": "dashscope-plan.env"}
KEY_NAMES = {"alibaba": "DASHSCOPE_API_KEY", "alibaba-plan": "DASHSCOPE_PLAN_API_KEY"}


def credential_path(provider_id: str) -> Path:
    """Where this provider's key file lives, resolved when asked rather than when this module was imported.

    Freezing it at import binds the home directory of whichever process happened to load the module first, which
    is wrong for a service that changes user and impossible to point elsewhere in a test.
    """
    return Path.home() / ".config/llm" / CREDENTIAL_NAMES[provider_id]


def credential(provider_id: str = "alibaba") -> Secret:
    path = credential_path(provider_id)
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
            wanted = KEY_NAMES[provider_id] + "="
            if len(parts) == 1 and parts[0].startswith(wanted):
                value = parts[0].partition("=")[2]
                if value:
                    return Secret(provider=provider_id, value=value)
    raise ValueError(f"credential file has no {KEY_NAMES[provider_id]}")


def configuration(model: str, provider_id: str = "alibaba") -> dict[str, Any]:
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
            "options": {"baseURL": PROVIDERS[provider_id].base_url,
                        "apiKey": "{env:" + KEY_NAMES[provider_id] + "}"},
            "models": {model: {"name": model, "tool_call": True}},
        }},
    }


class AlibabaAgent(GitWorktreeMixin):
    """Qwen driving a tool loop through OpenCode, against either the free tier or the Lite Plan."""

    def __init__(
        self, root: Path, model: str = "qwen3-coder-plus", provider_id: str = "alibaba"
    ) -> None:
        self.root, self.model, self.provider_id = Path(root), model, provider_id
        self.name = f"opencode:{provider_id}"

    def status(self) -> tuple[bool, str]:
        if not shutil.which("opencode"):
            return False, "opencode CLI not installed"
        try:
            credential(self.provider_id)
        except (OSError, ValueError):
            return False, f"{self.provider_id} credential missing, malformed, or not owner-only (0600)"
        return True, "ready (quota and network not probed)"

    def available(self) -> bool:
        return self.status()[0]

    def run(self, prompt: str, workspace: Path, timeout_s: int = 1800) -> AgentRun:
        key = None
        try:
            key = credential(self.provider_id)
            code, out, err, wall = _run(
                ["opencode", "run", "--format", "json", "--agent", "build", "-m", f"{PROVIDER}/{self.model}", prompt],
                workspace, timeout_s,
                env={KEY_NAMES[self.provider_id]: key.reveal(),
                     "OPENCODE_CONFIG_CONTENT": json.dumps(configuration(self.model, self.provider_id))},
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
