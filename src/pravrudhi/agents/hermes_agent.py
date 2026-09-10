"""Nous Research's hermes-agent as a seat, driven one-shot and accounted per dispatch.

Added on the operator's instruction of 2026-09-10. Installed isolated with `uv tool install hermes-agent`
rather than into the project venv, because it pins exact versions (`openai==2.24.0`, `pydantic==2.13.4`) that
would fight pravrudhi's own resolution; the seat needs the CLI on PATH, not the library importable.

**It is a MEASURED seat, and that was the condition for adding it at all.** `hermes -z <prompt> --usage-file
<path>` writes a JSON report carrying `total_tokens`, the input/output/cache breakdown and
`estimated_cost_usd`. Its own help says the report "is written even when the run fails, so pipelines can
always account for spend", which is the property that makes a failed dispatch accountable instead of
invisible. A survey reported hermes as unable to report usage and as unable to satisfy `CodingAgent`; both
were wrong, and the second doubly so -- `GitWorktreeMixin` already supplies `create_workspace` and
`collect_changes`, so a CLI seat implements `run()` and nothing else.

The None-versus-zero discipline is the same one `alibaba_agent` was found breaking hours earlier: a report
that never landed, or landed without a total, is UNMEASURED and reports `None`. A report saying zero is a
measured zero. Collapsing either into the other hides a real seat behind a plausible number.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from pravrudhi.agents.base import AgentRun, GitWorktreeMixin
from pravrudhi.agents.cli_agents import _run


@dataclass(frozen=True)
class Usage:
    """What one hermes dispatch cost, or `None` for each figure the report did not carry."""

    tokens: int | None = None
    cache_read: int | None = None
    cache_write: int | None = None
    cost_usd: float | None = None
    model: str | None = None
    session_id: str | None = None
    failed: bool = False
    failure: str | None = None


def _int_or_none(value: object) -> int | None:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def usage_from_report(path: Path) -> Usage:
    """Read a `--usage-file` report, or return an all-`None` `Usage` when there is nothing to read.

    `total_tokens` is what hermes normally writes. When it is absent the parts are summed instead, because a
    seat reporting its input and output but no total is measured rather than unmeasured; when neither exists
    the answer is `None`, which means nobody knows what the dispatch cost -- not that it was free.
    """
    try:
        report = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return Usage()
    if not isinstance(report, dict):
        return Usage()

    total = _int_or_none(report.get("total_tokens"))
    if total is None:
        parts = [
            _int_or_none(report.get(k))
            for k in ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens")
        ]
        present = [p for p in parts if p is not None]
        total = sum(present) if present else None

    cost = report.get("estimated_cost_usd")
    return Usage(
        tokens=total,
        cache_read=_int_or_none(report.get("cache_read_tokens")),
        cache_write=_int_or_none(report.get("cache_write_tokens")),
        cost_usd=float(cost) if isinstance(cost, (int, float)) else None,
        model=str(report["model"]) if report.get("model") else None,
        session_id=str(report["session_id"]) if report.get("session_id") else None,
        failed=bool(report.get("failed")),
        failure=str(report["failure"]) if report.get("failure") else None,
    )


class HermesAgent(GitWorktreeMixin):
    """One hermes turn per dispatch, confined to the task's worktree and accounted from its usage report."""

    def __init__(self, root: Path, model: str | None = None, provider: str | None = None) -> None:
        self.root, self.model, self.provider = Path(root), model, provider
        self.name = "hermes"

    def status(self) -> tuple[bool, str]:
        """Installed AND configured, because a seat that reports ready and fails every dispatch is worse than
        one that reports why it cannot run.

        Hermes keeps its model and provider in `~/.hermes/config.yaml` (`HERMES_HOME` moves it), and both
        `hermes model` and `hermes setup` refuse a non-interactive terminal -- so a fresh install has the CLI
        and no provider, and a one-shot run against it fails. Reporting that as "ready" would let the router
        pick a seat guaranteed to lose, and the loss would be recorded against the seat's success rate as if
        it were evidence about its quality.
        """
        if not shutil.which("hermes"):
            return False, "hermes CLI not installed (uv tool install hermes-agent)"
        home = Path(os.environ.get("HERMES_HOME") or (Path.home() / ".hermes"))
        if not (home / "config.yaml").is_file():
            return False, f"hermes installed but no provider configured ({home}/config.yaml absent; run `hermes setup`)"
        return True, "ready (quota and provider reachability not probed)"

    def available(self) -> bool:
        return self.status()[0]

    def run(self, prompt: str, workspace: Path, timeout_s: int = 1800) -> AgentRun:
        workspace = Path(workspace).resolve()
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / "usage.json"
            # `--no-restore-cwd` keeps the run in the worktree it was given: hermes otherwise restores the
            # session's recorded directory, which for a resumed session is somebody else's workspace.
            cmd = ["hermes", "-z", prompt, "--usage-file", str(report), "--no-restore-cwd"]
            if self.model:
                cmd += ["-m", self.model]
            if self.provider:
                cmd += ["--provider", self.provider]
            code, out, err, wall = _run(cmd, workspace, timeout_s)
            usage = usage_from_report(report)
        ok = code == 0 and not usage.failed
        if not ok and not err and usage.failure:
            err = usage.failure
        return AgentRun(
            self.name, ok, code if code else (0 if ok else 1), wall, out, workspace,
            session_id=usage.session_id, tokens=usage.tokens, cost_usd=usage.cost_usd,
            cache_read_tokens=usage.cache_read, cache_write_tokens=usage.cache_write,
            stderr_tail=err[-2000:],
        )
