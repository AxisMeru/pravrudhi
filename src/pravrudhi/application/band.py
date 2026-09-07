"""Per-artifact permission decisions; no execution, measurement, or persistence."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from pravrudhi.application.kshudha import Appetite
from pravrudhi.application.svasthya import Health, can_dispatch_new_work

PACKAGED_CONFIG = Path(__file__).resolve().parents[1] / "assets" / "configs" / "band.yaml"


def _integer(value: object, name: str, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")
    return value


@dataclass(frozen=True)
class Level:
    id: str
    name: str
    automatic: frozenset[str]
    ask_first: frozenset[str]
    spend_ceiling_cents: int
    min_interval_seconds: int
    max_runs: int | None


def _signature(level: Level) -> tuple[Any, ...]:
    """Everything that makes a level different from its neighbour.

    Two levels with the same signature are one level wearing two names, and offering a user a choice that is not
    a choice is worse than offering fewer. The loader refuses them.
    """
    return (level.automatic, level.ask_first, level.spend_ceiling_cents,
            level.min_interval_seconds, level.max_runs)


@dataclass(frozen=True)
class Policy:
    version: str
    currency: str
    levels: tuple[Level, ...]
    evidence: tuple[tuple[str, tuple[str, ...]], ...]

    def level(self, level_id: str) -> Level:
        for level in self.levels:
            if level.id == level_id:
                return level
        raise ValueError(f"unknown band level: {level_id!r}")


def load_config(path: Path | None = None) -> Policy:
    raw = yaml.safe_load((path or PACKAGED_CONFIG).read_text(encoding="utf-8"))
    actions = raw["actions"]
    if not isinstance(actions, dict) or not actions:
        raise ValueError("actions must be a nonempty mapping")
    evidence = []
    for action, fields in actions.items():
        if not isinstance(action, str) or not isinstance(fields, list) or any(
            field not in Evidence.__dataclass_fields__ for field in fields
        ):
            raise ValueError("invalid action evidence")
        evidence.append((action, tuple(fields)))
    levels: list[Level] = []
    for row in raw["levels"]:
        automatic, ask = row["automatic"], row["ask_first"]
        if not isinstance(automatic, list) or not isinstance(ask, list):
            raise ValueError("permissions must be lists")
        automatic, ask = frozenset(automatic), frozenset(ask)
        if automatic & ask or not (automatic | ask) <= actions.keys():
            raise ValueError("overlapping or unknown permissions")
        level = Level(
            row["id"], row["name"], automatic, ask,
            _integer(row["spend_ceiling_cents"], "spend ceiling"),
            _integer(row["min_interval_seconds"], "interval"),
            None if row["max_runs"] is None else _integer(row["max_runs"], "max runs", 1),
        )
        if not level.id or not level.name or any(x.id == level.id for x in levels):
            raise ValueError("level needs a unique id and a name")
        if levels:
            previous = levels[-1]
            if (not previous.automatic <= level.automatic
                or not (previous.automatic | previous.ask_first) <= (automatic | ask)
                or level.spend_ceiling_cents < previous.spend_ceiling_cents
                or level.min_interval_seconds > previous.min_interval_seconds
                or (previous.max_runs is None and level.max_runs is not None)
                or (previous.max_runs is not None and level.max_runs is not None
                    and level.max_runs < previous.max_runs)):
                raise ValueError("levels must increase autonomy, budget, and frequency")
        if any(_signature(x) == _signature(level) for x in levels):
            raise ValueError("indistinguishable levels")
        levels.append(level)
    if not levels:
        raise ValueError("at least one level is required")
    return Policy(str(raw["version"]), raw["currency"], tuple(levels), tuple(evidence))


@dataclass(frozen=True)
class Artifact:
    """Caller-owned selection and durable lifetime usage, including in-flight reservations."""
    id: str
    level_id: str
    spent_cents: int = 0
    reserved_cents: int = 0
    runs_started: int = 0
    last_started_at: datetime | None = None
    spend_limit_cents: int | None = None


@dataclass(frozen=True)
class Evidence:
    """Artifact-scoped evidence references supplied by the external observer."""
    broken_report: str | None = None
    unsafe_dependency_report: str | None = None
    baseline_record: str | None = None
    regression_report: str | None = None


@dataclass(frozen=True)
class Decision:
    artifact_id: str
    level_id: str
    action: str
    status: str
    reason: str
    spend_limit_cents: int
    remaining_cents: int
    min_interval_seconds: int
    max_runs: int | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


NO_EVIDENCE = Evidence()
"""The absence of evidence, as a value. Frozen, so one shared instance is safe as a default."""


def decide(
    artifact: Artifact, *, action: str, cost_cents: int, appetite: Appetite,
    health: Health, now: datetime, evidence: Evidence = NO_EVIDENCE,
    approved: bool = False, policy: Policy | None = None,
) -> Decision:
    """Approval covers this action only; it never overrides a ceiling or a stop."""
    policy = policy or load_config()
    level = policy.level(artifact.level_id)
    limit = level.spend_ceiling_cents
    if artifact.spend_limit_cents is not None:
        limit = _integer(artifact.spend_limit_cents, "artifact spend limit")
        if limit > level.spend_ceiling_cents:
            raise ValueError("artifact spend limit exceeds level ceiling; explicitly choose another level")
    spent = _integer(artifact.spent_cents, "spent")
    reserved = _integer(artifact.reserved_cents, "reserved")
    runs = _integer(artifact.runs_started, "runs started")
    cost = _integer(cost_cents, "cost")
    if not artifact.id.strip():
        raise ValueError("artifact id is required")
    if now.utcoffset() is None or (artifact.last_started_at is not None
                                   and artifact.last_started_at.utcoffset() is None):
        raise ValueError("timestamps must be timezone-aware")
    if (runs == 0) != (artifact.last_started_at is None):
        raise ValueError("run count and last start must agree")
    remaining = max(0, limit - spent - reserved)

    def report(status: str, reason: str) -> Decision:
        return Decision(artifact.id, level.id, action, status, reason, limit, remaining,
                        level.min_interval_seconds, level.max_runs)

    if not can_dispatch_new_work(health):
        return report("denied", f"engine health: {health.state}")
    if appetite.selected is None or appetite.action is None:
        return report("denied", "appetite has selected no action")
    if action not in level.automatic | level.ask_first:
        return report("denied", "action outside selected level")
    if spent + reserved + cost > limit:
        return report("denied", "lifetime spend ceiling exceeded")
    if level.max_runs is not None and runs >= level.max_runs:
        return report("denied", "run allowance exhausted")
    if artifact.last_started_at is not None:
        elapsed = (now - artifact.last_started_at).total_seconds()
        if elapsed < level.min_interval_seconds:
            return report("denied", "run interval has not elapsed")
    for field in dict(policy.evidence)[action]:
        value = getattr(evidence, field)
        if not isinstance(value, str) or not value.strip():
            return report("denied", f"missing artifact evidence: {field}")
    if action in level.ask_first and not approved:
        return report("ask_first", "explicit approval required for this action")
    return report("allowed", "within selected artifact permissions")
