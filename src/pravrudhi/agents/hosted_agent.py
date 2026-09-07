"""A hosted free-tier model, wired in as a single-shot file writer rather than a tool-calling agent.

This legacy adapter does not provide a tool loop. Qwen now has a separate OpenCode-backed adapter
in `alibaba_agent`; this class remains available for explicitly requested single-shot work. `run()` does not hand them a
sandbox: it asks for the whole answer - every file's full content - in
one JSON reply, then writes only the files the dispatch brief actually allowed. A path outside that list, or one
that tries to leave the worktree, is refused and named rather than written.
"""

from __future__ import annotations

import json
import re
import time
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

from pravrudhi.agents.base import AgentRun, GitWorktreeMixin
from pravrudhi.models import hosted

ALLOWED_PATHS_LINE = re.compile(r"You may create or modify ONLY these paths:\s*(.+)")

RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "files": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        },
        "note": {"type": "string"},
    },
    "required": ["files", "note"],
}


def _allowed_patterns(prompt: str) -> list[str]:
    """The globs a dispatch brief permits, parsed from the line `dispatch` appends to every task prompt."""
    m = ALLOWED_PATHS_LINE.search(prompt)
    if not m:
        return []
    line = m.group(1).splitlines()[0].rstrip(". ")
    return [p.strip() for p in line.split(",") if p.strip()]


def _is_safe_relative(path: str) -> bool:
    if not path or path.startswith("/") or path.startswith("\\"):
        return False
    return ".." not in Path(path).parts


def _parse_reply(reply: str) -> tuple[list[dict[str, Any]], str, str]:
    """The files list, the note, and an error string (empty when parsing succeeded)."""
    try:
        payload = json.loads(reply)
    except (ValueError, TypeError) as e:
        return [], "", f"hosted model's reply was not valid JSON: {e}"
    if not isinstance(payload, dict):
        return [], "", "hosted model's reply was not a JSON object"
    files = payload.get("files")
    files = files if isinstance(files, list) else []
    note = str(payload.get("note", ""))
    return files, note, ""


def _write_files(files: list[dict[str, Any]], workspace: Path, patterns: list[str]) -> tuple[list[str], list[str]]:
    root = workspace.resolve()
    written: list[str] = []
    refused: list[str] = []
    for entry in files:
        if not isinstance(entry, dict):
            continue
        path = str(entry.get("path") or "")
        content = entry.get("content")
        if not path or not isinstance(content, str):
            refused.append(path or "<missing path>")
            continue
        if not _is_safe_relative(path) or not any(fnmatch(path, pat) for pat in patterns):
            refused.append(path)
            continue
        dest = (root / path).resolve()
        if dest != root and root not in dest.parents:
            refused.append(path)
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content)
        written.append(path)
    return written, refused


class HostedAgent(GitWorktreeMixin):
    """One of the operator's own hosted free-tier models, driven for single-shot file-writing tasks.

    Every gate a caller needs already lives in `hosted.available()`/`hosted.chat()`; this class adds only the
    contract a `CodingAgent` must satisfy and the write-side sandboxing that a tool-calling agent gets for free by
    virtue of never leaving its own worktree.
    """

    name = "hosted"

    def __init__(self, root: Path, model: str = "qwen3-coder-plus") -> None:
        self.root, self.model = Path(root), model

    def available(self) -> bool:
        return hosted.available()[0]

    def run(self, prompt: str, workspace: Path, timeout_s: int = 1800) -> AgentRun:
        t0 = time.monotonic()
        patterns = _allowed_patterns(prompt)
        messages = [
            {
                "role": "system",
                "content": (
                    "You write code changes as a single JSON object and nothing else - no prose, no markdown "
                    "fences. Match this schema exactly: " + json.dumps(RESPONSE_SCHEMA)
                ),
            },
            {"role": "user", "content": prompt},
        ]
        response_format = {"type": "json_schema", "json_schema": {"name": "file_edits", "schema": RESPONSE_SCHEMA}}
        try:
            reply = hosted.chat(self.model, messages, response_format=response_format)
        except Exception as e:  # the hosted surface can refuse (opt-in off, quota spent, no admin) at call time
            return AgentRun(
                agent=self.name, ok=False, exit_code=1, wall_s=time.monotonic() - t0,
                text=f"hosted call failed: {e}", workspace=workspace,
            )
        files, note, error = _parse_reply(reply)
        written, refused = _write_files(files, workspace, patterns)
        parts = [p for p in (note, error) if p]
        if refused:
            parts.append("refused: " + ", ".join(refused))
        text = "\n".join(parts) if parts else reply
        return AgentRun(
            agent=self.name, ok=bool(written), exit_code=0 if written else 1,
            wall_s=time.monotonic() - t0, text=text, workspace=workspace,
        )


__all__ = ["HostedAgent"]
