"""What happened when the work had to move -- between agents, between sessions, or off a model that hit its limit.

A heartbeat (`application/heartbeat.py`) records what it did on its own schedule; `application/subagents.py`
records what one dispatched step produced; `application/routing.py` records which route a tier chose. None of
those logs answers "who was doing this, what did they get through, and what is still open" when a session ends
mid-task and a different agent -- or the same one, later -- has to pick it back up. This module is that record: a
running log of continuity events, and a brief compiled from it plus the engine's own declared state, addressed to
whoever reads it next rather than to a gate or a ledger reviewer.

Nothing here writes to the ledger, `research/`, `gates/` or `pravrudhi_kernel/`. The continuity log is operational
state beside the workspace's other logs, not evidence.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pravrudhi import __version__ as ENGINE_VERSION
from pravrudhi.application import objectives, recipes, subagents
from pravrudhi.application.intent import compile_intent

# `accepted` and `rejected` are outcomes of a dispatch rather than states of the loop, and they are here
# because the trace reads a wave by them: what was taken, what was refused, and where a route gave out.
KINDS: tuple[str, ...] = (
    "dispatch", "accepted", "rejected", "limited", "fallback", "handback", "blocked", "milestone",
)


class ContinuityError(ValueError):
    """A continuity note whose kind nothing downstream would know how to act on."""


@dataclass(frozen=True)
class ContinuityEntry:
    """One line of the continuity log."""

    at: str
    kind: str
    summary: str
    detail: str | None = None
    agent: str | None = None
    objective: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def log_path(root: Path) -> Path:
    return Path(root) / ".pravrudhi" / "continuity.jsonl"


def _at(moment: datetime) -> str:
    aware = moment if moment.tzinfo else moment.replace(tzinfo=UTC)
    return aware.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def note(
    root: Path,
    *,
    kind: str,
    summary: str,
    detail: str | None = None,
    agent: str | None = None,
    objective: str | None = None,
    at: datetime | None = None,
) -> ContinuityEntry:
    """Append one continuity entry and return it."""
    if kind not in KINDS:
        raise ContinuityError(f"kind {kind!r} must be one of {KINDS}")
    entry = ContinuityEntry(
        at=_at(at) if at is not None else _at(datetime.now(UTC)),
        kind=kind,
        summary=summary,
        detail=detail,
        agent=agent,
        objective=objective,
    )
    path = log_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as fh:
        fh.write(json.dumps(entry.to_dict(), sort_keys=True) + "\n")
    return entry


def entries(root: Path, n: int = 100) -> list[ContinuityEntry]:
    """The last `n` continuity entries, oldest first. A corrupt line is skipped, not fatal -- same as
    `heartbeat.history` and `subagents.runs`."""
    path = log_path(root)
    if not path.exists():
        return []
    out: list[ContinuityEntry] = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            d = json.loads(line)
            out.append(
                ContinuityEntry(
                    at=str(d["at"]),
                    kind=str(d["kind"]),
                    summary=str(d["summary"]),
                    detail=d.get("detail"),
                    agent=d.get("agent"),
                    objective=d.get("objective"),
                )
            )
        except (json.JSONDecodeError, KeyError, TypeError):
            continue
    return out[-n:] if n > 0 else out


def _cooling(root: Path) -> dict[str, str]:
    """Which agents `application.availability` currently has cooling down, and until when. That module may not
    exist yet in a given checkout, so its absence degrades to "nothing is cooling" rather than failing the brief."""
    try:
        from pravrudhi.application.availability import cooling
    except ImportError:
        return {}
    return dict(cooling(root))


def _open_steps(root: Path, objs: list[objectives.Objective]) -> list[tuple[str, str]]:
    """Every (objective id, step id) whose plan step has no accepted run yet -- the same candidate set
    `heartbeat.beat` computes before choosing the single most-neglected one to dispatch."""
    catalogue = tuple(recipes.library())
    installed = frozenset(recipes.installed())
    open_work: list[tuple[str, str]] = []
    for obj in objs:
        plan = compile_intent(obj, catalogue, installed_skills=installed)
        accepted = {r.step for r in subagents.runs(root, obj.id) if r.accepted}
        for step in plan.steps:
            if step.id not in accepted:
                open_work.append((obj.id, step.id))
    return open_work


def _progress_line(row: dict[str, Any]) -> str:
    state = row["state"]
    if state == "measured":
        return f"    - {row['benchmark']}: measured, delta {row['delta']:+.4f} (significant={row['significant']})"
    if state == "baseline_only":
        return f"    - {row['benchmark']}: baseline only, nothing compared against it yet"
    return f"    - {row['benchmark']}: unmeasured ({row['reason']})"


def handover(root: Path) -> str:
    """A plain brief for whoever picks this work up next: what the engine is running, what happened most
    recently, who is unavailable, and what is still undone. Addressed to a reader who does not know this
    project's internal vocabulary -- every fact in it comes from the objectives, the continuity log, the
    routing/availability state or the recorded runs, never invented here."""
    root = Path(root)
    lines: list[str] = ["# Handover", "", f"Engine version: {ENGINE_VERSION}", ""]

    objs = objectives.load_all(root)
    lines.append("## Objectives")
    if not objs:
        lines.append("No objectives are declared in this workspace.")
    for obj in objs:
        summary = objectives.summary(root, obj)
        lines.append(f"- {obj.id}: {obj.intent}")
        lines.extend(_progress_line(row) for row in summary["progress"])
    lines.append("")

    lines.append("## Recent activity")
    recent = entries(root, 20)
    if not recent:
        lines.append("No continuity entries yet.")
    for entry in reversed(recent):
        who = f" ({entry.agent})" if entry.agent else ""
        where = f" [{entry.objective}]" if entry.objective else ""
        lines.append(f"- {entry.at} {entry.kind}{who}{where}: {entry.summary}")
        if entry.detail:
            lines.append(f"    {entry.detail}")
    lines.append("")

    cooling_map = _cooling(root)
    lines.append("## Agents cooling")
    if not cooling_map:
        lines.append("No agent is recorded as cooling.")
    for agent_name, until in sorted(cooling_map.items()):
        lines.append(f"- {agent_name} until {until}")
    lines.append("")

    lines.append("## Open work")
    open_work = _open_steps(root, objs)
    if not open_work:
        lines.append("No open steps: every declared objective's plan is fully accepted.")
    for obj_id, step_id in open_work:
        lines.append(f"- {obj_id}: {step_id}")

    return "\n".join(lines) + "\n"


def write_handover(root: Path, dest: Path = Path("HANDOVER.md")) -> Path:
    """Write `handover(root)` to `root / dest` and return the path written."""
    root = Path(root)
    path = root / dest
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(handover(root), encoding="utf-8")
    return path


__all__ = [
    "KINDS",
    "ContinuityEntry",
    "ContinuityError",
    "entries",
    "handover",
    "log_path",
    "note",
    "write_handover",
]
