"""Kṣudhā: a measurable appetite, so the engine seeks capability rather than waiting to be told.

Seven drives (design doc §5.2, plus `spardha`/rivalry) each answer one question honestly: how far is this from
where it should be, using only numbers this codebase actually keeps. A measurement that cannot be taken is
`unknown`, never a guessed number — an engine that invents a deficit to keep moving is worse than one that says
plainly it does not know.

This module is a pure calculator (`measure`, the per-drive builder functions, `sentence`, `voice`) plus a small
deterministic selector (`select`) that carries hysteresis across calls through an explicit, caller-supplied
`AppetiteState`. The design (§5.1) calls for a sqlite state store at `.pravrudhi/appetite/state.sqlite3`; this
module uses a JSON file at `.pravrudhi/appetite.json` instead, because every other store in this codebase
(`application/requests.py`, `application/availability.py`) is a single JSON file with an atomic tmp-write-replace,
not sqlite, and there is no reason for this one store to be the exception.

`measure(root)` reads the sources this codebase actually has: `doctor.run_doctor` for `sthiti` (continuity),
`tools`/`recipes`/`agents.registry` for `samarthya` (capability), `objectives`/`external` for `unnati_avakasha`
(benchmark headroom), `availability`'s cooling routes for `sadhana` (resources), `requests` for `seva`
(obligations), and `external`'s own admitted rows compared against the `rivals` declared in the appetite config
for `spardha` (rivalry). The design's sixth drive, `pramana_navyata` (evidence freshness), has no source module
in this codebase yet, so it is always reported `unknown` — exactly the "unknown, never fabricated" rule applied
to a whole drive rather than one reading. `spardha` falls back to this workspace's own CAPABILITY coverage --
`parity.py`'s checked-in matrix, whose evidence is a path that must exist or a read-only command that must
pass -- whenever no rival figure names a benchmark this workspace has measured, and to `unknown` when that
matrix is empty too. Parity described itself as "a repeatable source of work for the existing rivalry drive"
and nothing called it, so 25 tracked capabilities with 4 open gaps produced no work at all.

`select` does not dispatch anything; `heartbeat.py` remains the only periodic dispatcher (design §5.1), and
wiring this module into it is a separate task. `select` only turns a list of `Drive` readings, a persisted
`AppetiteState`, and whether an operator ask is currently overdue, into one `Appetite` decision: which drive
wins, what it would do, and why every other drive did not.
"""

from __future__ import annotations

import contextlib
import json
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import yaml

from pravrudhi.agents import registry as agents_registry
from pravrudhi.application import availability, doctor, external, objectives, recipes, requests, tools

PACKAGED_CONFIG = Path(__file__).resolve().parents[1] / "assets" / "configs" / "appetite.yaml"

DRIVE_IDS: tuple[str, ...] = (
    "sthiti", "samarthya", "pramana_navyata", "unnati_avakasha", "sadhana", "seva", "spardha",
)
WIRE_NAMES: dict[str, str] = {
    "sthiti": "continuity",
    "samarthya": "capability",
    "pramana_navyata": "freshness",
    "unnati_avakasha": "benchmark_headroom",
    "sadhana": "resources",
    "seva": "obligations",
    "spardha": "rivalry",
}

Phase = Literal["hungry", "sated"]


def clip(x: float) -> float:
    """Bound a policy value to [0,1] (design §5.2)."""
    return max(0.0, min(1.0, x))


def _now_iso(moment: datetime | None = None) -> str:
    aware = moment if (moment and moment.tzinfo) else (moment.replace(tzinfo=UTC) if moment else datetime.now(UTC))
    return aware.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------------------------------------------
# Configuration


@dataclass(frozen=True)
class RivalFigure:
    """One reported (never measured here) competitor result, declared in the appetite config's `rivals` list.

    This is `agama` — testimony, not `pratyakṣa` (direct measurement) — so every surface that shows it must say
    so, and `spardha_drive` never lets one stand in for a number this workspace actually measured.
    """

    system: str
    benchmark: str
    score: float
    source_url: str
    as_of: str
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "system": self.system, "benchmark": self.benchmark, "score": self.score,
            "source_url": self.source_url, "as_of": self.as_of, "note": self.note,
        }


@dataclass(frozen=True)
class AppetiteConfig:
    """Weights, targets and satiation thresholds. Every value here is a policy choice, not a measured result."""

    policy_version: str = "1"
    weights: dict[str, float] = field(default_factory=dict)
    targets: dict[str, float] = field(default_factory=dict)
    hungry_threshold: float = 0.6
    sated_threshold: float = 0.3
    cooldown_beats: int = 2
    sthiti_check_weights: dict[str, float] = field(default_factory=dict)
    benchmark_headroom_scale: float = 0.05
    resource_min_routes: int = 1
    seva_overdue_days: float = 7.0
    seva_age_scale_days: float = 14.0
    rivals: tuple[RivalFigure, ...] = field(default_factory=tuple)

    def weight(self, drive_id: str) -> float:
        return float(self.weights.get(drive_id, 1.0))

    def target(self, drive_id: str) -> float:
        return float(self.targets.get(drive_id, 1.0))


def _parse_rivals(rows: list[Any]) -> tuple[RivalFigure, ...]:
    """Reported rival figures, skipping any entry missing what would make it traceable (design: never invent a
    competitor's score, and never let an untraceable one stand in for one that is)."""
    out: list[RivalFigure] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        system = str(row.get("system") or "").strip()
        benchmark = str(row.get("benchmark") or "").strip()
        source_url = str(row.get("source_url") or "").strip()
        as_of = str(row.get("as_of") or "").strip()
        score = row.get("score")
        if not (system and benchmark and source_url and as_of) or score is None:
            continue
        out.append(RivalFigure(
            system=system, benchmark=benchmark, score=float(score), source_url=source_url, as_of=as_of,
            note=str(row.get("note") or ""),
        ))
    return tuple(out)


def load_config(path: Path | None = None) -> AppetiteConfig:
    raw: dict[str, Any] = yaml.safe_load((path or PACKAGED_CONFIG).read_text(encoding="utf-8")) or {}
    return AppetiteConfig(
        policy_version=str(raw.get("policy_version") or "1"),
        weights={str(k): float(v) for k, v in (raw.get("weights") or {}).items()},
        targets={str(k): float(v) for k, v in (raw.get("targets") or {}).items()},
        hungry_threshold=float(raw.get("hungry_threshold", 0.6)),
        sated_threshold=float(raw.get("sated_threshold", 0.3)),
        cooldown_beats=int(raw.get("cooldown_beats", 2)),
        sthiti_check_weights={str(k): float(v) for k, v in (raw.get("sthiti_check_weights") or {}).items()},
        benchmark_headroom_scale=float(raw.get("benchmark_headroom_scale", 0.05)),
        resource_min_routes=int(raw.get("resource_min_routes", 1)),
        seva_overdue_days=float(raw.get("seva_overdue_days", 7.0)),
        seva_age_scale_days=float(raw.get("seva_age_scale_days", 14.0)),
        rivals=_parse_rivals(list(raw.get("rivals") or [])),
    )


# --------------------------------------------------------------------------------------------------------------
# Drive and Appetite


@dataclass(frozen=True)
class Drive:
    id: str
    wire_name: str
    value: float | None
    target: float
    deficit: float | None
    weight: float
    eligible: bool
    blocked_reason: str
    sources: tuple[str, ...]
    unknown: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "wire_name": self.wire_name, "value": self.value, "target": self.target,
            "deficit": self.deficit, "weight": self.weight, "eligible": self.eligible,
            "blocked_reason": self.blocked_reason, "sources": list(self.sources), "unknown": self.unknown,
        }

    @property
    def pressure(self) -> float:
        """weight × deficit (design §5.3 step 4); 0.0 when the deficit is unknown, never a fabricated score."""
        return self.weight * self.deficit if self.deficit is not None else 0.0


    @staticmethod
    def from_dict(d: dict[str, Any]) -> Drive:
        return Drive(
            id=str(d.get("id", "")), wire_name=str(d.get("wire_name", "")), value=d.get("value"),
            target=float(d.get("target") or 0.0), deficit=d.get("deficit"),
            weight=float(d.get("weight") or 1.0), eligible=bool(d.get("eligible", False)),
            blocked_reason=str(d.get("blocked_reason", "")), sources=tuple(d.get("sources") or []),
            unknown=bool(d.get("unknown", False)),
        )


@dataclass(frozen=True)
class Appetite:
    as_of: str
    policy_version: str
    drives: tuple[Drive, ...]
    largest_unmet: str | None
    selected: str | None
    action: dict[str, Any] | None
    next_wake: str
    resting_reason: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "as_of": self.as_of, "policy_version": self.policy_version,
            "drives": [d.to_dict() for d in self.drives], "largest_unmet": self.largest_unmet,
            "selected": self.selected, "action": self.action, "next_wake": self.next_wake,
            "resting_reason": self.resting_reason,
        }

    @staticmethod
    def from_dict(d: dict[str, Any]) -> Appetite:
        """Rehydrate a stored decision. A reader must see what the engine decided, not a fresh calculation."""
        return Appetite(
            as_of=str(d.get("as_of", "")),
            policy_version=str(d.get("policy_version", "")),
            drives=tuple(Drive.from_dict(x) for x in (d.get("drives") or [])),
            largest_unmet=d.get("largest_unmet"),
            selected=d.get("selected"),
            action=d.get("action"),
            next_wake=str(d.get("next_wake", "")),
            resting_reason=d.get("resting_reason"),
        )


def _unknown(drive_id: str, cfg: AppetiteConfig, reason: str, sources: tuple[str, ...] = ()) -> Drive:
    return Drive(
        id=drive_id, wire_name=WIRE_NAMES[drive_id], value=None, target=cfg.target(drive_id), deficit=None,
        weight=cfg.weight(drive_id), eligible=False, blocked_reason=reason, sources=sources, unknown=True,
    )


# --------------------------------------------------------------------------------------------------------------
# Per-drive builders — pure functions over already-fetched data, so a test can hand-calculate a fixture without
# touching the filesystem.


def sthiti_drive(checks: list[dict[str, Any]], cfg: AppetiteConfig) -> Drive:
    """continuity: deficit is the weighted fraction of `doctor.run_doctor` checks that failed (design §5.2)."""
    if not checks:
        return _unknown("sthiti", cfg, "doctor reported no health predicates")
    total_w = 0.0
    fail_w = 0.0
    sources: list[str] = []
    for check in checks:
        name = str(check.get("name", ""))
        w = cfg.sthiti_check_weights.get(name, 1.0)
        ok = bool(check.get("ok"))
        total_w += w
        if not ok:
            fail_w += w
        sources.append(f"doctor:{name}={'ok' if ok else 'fail'}")
    if total_w <= 0:
        return _unknown("sthiti", cfg, "every health predicate has zero configured weight", tuple(sources))
    deficit = clip(fail_w / total_w)
    return Drive(
        id="sthiti", wire_name="continuity", value=1.0 - deficit, target=cfg.target("sthiti"), deficit=deficit,
        weight=cfg.weight("sthiti"), eligible=True, blocked_reason="", sources=tuple(sources), unknown=False,
    )


def samarthya_drive(
    tool_rows: list[dict[str, Any]], recipe_rows: list[dict[str, Any]],
    agent_statuses: list[agents_registry.AgentStatus], cfg: AppetiteConfig,
) -> Drive:
    """capability: `C = qualified / total` over catalogued tools, recipes and coding-agent routes (design §5.2).

    "Qualified" here means "detected present on this host by tools.py/recipes.py/agents.registry", the honest
    proxy this codebase can measure today; it is not yet the design's stricter "admitted with resolving task
    evidence" (no admission ledger exists in this codebase), and that gap is a real property of `C`, not hidden.
    """
    items: list[tuple[str, bool]] = (
        [(f"tool:{t['id']}", bool(t["available"])) for t in tool_rows]
        + [(f"recipe:{r['id']}", bool(r["available"])) for r in recipe_rows]
        + [(f"agent:{a.name}", bool(a.available)) for a in agent_statuses]
    )
    if not items:
        return _unknown("samarthya", cfg, "no tool, recipe or agent catalogue was readable")
    target = cfg.target("samarthya")
    if target <= 0:
        return _unknown("samarthya", cfg, "capability target must be > 0")
    qualified = sum(1 for _, ok in items if ok)
    total = len(items)
    c = qualified / total
    deficit = clip((target - c) / target)
    sources = tuple(f"{name}={'available' if ok else 'absent'}" for name, ok in items)
    return Drive(
        id="samarthya", wire_name="capability", value=c, target=target, deficit=deficit,
        weight=cfg.weight("samarthya"), eligible=True, blocked_reason="", sources=sources, unknown=False,
    )


def pramana_navyata_drive(cfg: AppetiteConfig) -> Drive:
    """freshness: no evidence-freshness source exists in this codebase yet, so this drive is always unknown."""
    return _unknown("pramana_navyata", cfg, "no evidence-freshness source is wired into the engine yet")


def unnati_avakasha_drive(
    benchmarks: list[tuple[str, str, float | None, float | None]], cfg: AppetiteConfig,
) -> Drive:
    """benchmark headroom: `H_b = clip(sign × (target_delta - current_delta) / scale)`, averaged (design §5.2).

    `benchmarks` is `(label, direction, target_delta, current_delta)` per declared objective benchmark, gathered
    from `objectives.load_all` and `objectives.progress`. Direction is "up" or "down" (`Benchmark.direction`);
    an absent target or an unmeasured (`state != "measured"`) delta makes that one benchmark unknown, never a
    fabricated headroom.
    """
    scale = cfg.benchmark_headroom_scale
    headrooms: list[float] = []
    sources: list[str] = []
    for label, direction, target_delta, current_delta in benchmarks:
        if target_delta is None or current_delta is None or scale <= 0:
            sources.append(f"{label}: unknown (no target, no measured delta, or scale<=0)")
            continue
        sign = 1.0 if direction == "up" else -1.0
        h = clip(sign * (target_delta - current_delta) / scale)
        headrooms.append(h)
        sources.append(f"{label}: target_delta={target_delta!r} current_delta={current_delta!r} headroom={h:.4f}")
    if not headrooms:
        return _unknown(
            "unnati_avakasha", cfg, "no declared benchmark has both a target_delta and a measured delta",
            tuple(sources),
        )
    deficit = clip(sum(headrooms) / len(headrooms))
    return Drive(
        id="unnati_avakasha", wire_name="benchmark_headroom", value=deficit, target=cfg.target("unnati_avakasha"),
        deficit=deficit, weight=cfg.weight("unnati_avakasha"), eligible=True, blocked_reason="",
        sources=tuple(sources), unknown=False,
    )


def sadhana_drive(all_route_ids: list[str], available_route_ids: list[str], cooling_ids: set[str], cfg: AppetiteConfig) -> Drive:
    """resources: `a_r = clip(usable / resource_min_routes)`; `D_A = 1-a_r` (design §5.2), one tracked resource —
    a usable coding-agent route: ready per `agents.registry.survey` and not presently cooling per `availability`.
    """
    required = cfg.resource_min_routes
    if required <= 0:
        return _unknown("sadhana", cfg, "resource_min_routes must be > 0")
    usable = [rid for rid in available_route_ids if rid not in cooling_ids]
    a = clip(len(usable) / required)
    deficit = 1.0 - a
    sources = (
        f"routes_total={len(all_route_ids)}", f"routes_available={len(available_route_ids)}",
        f"routes_cooling={len(cooling_ids)}", f"usable={len(usable)}", f"required_min={required}",
    )
    return Drive(
        id="sadhana", wire_name="resources", value=a, target=cfg.target("sadhana"), deficit=deficit,
        weight=cfg.weight("sadhana"), eligible=True, blocked_reason="", sources=sources, unknown=False,
    )


def seva_drive(backlog: dict[str, Any], cfg: AppetiteConfig) -> Drive:
    """obligations: `D_R = clip((R_target-R_verified)/R_target)`; `D_seva = max(D_R, deadline debt)` (design §5.2).

    A zero-request `backlog` ("total" == 0) is "no cohort", reported unknown rather than as perfect delivery.
    """
    total = int(backlog.get("total", 0))
    if total <= 0:
        return _unknown("seva", cfg, "no requests captured yet (no cohort)")
    target = cfg.target("seva")
    if target <= 0:
        return _unknown("seva", cfg, "obligation target must be > 0")
    verified = int((backlog.get("by_state") or {}).get("verified", 0))
    r_verified = verified / total
    d_r = clip((target - r_verified) / target)
    oldest_days = float(backlog.get("oldest_open_days", 0.0))
    age_scale = cfg.seva_age_scale_days
    d_age = clip(oldest_days / age_scale) if age_scale > 0 else 0.0
    deficit = max(d_r, d_age)
    sources = (
        f"captured={total}", f"verified={verified}", f"R_verified={r_verified:.4f}",
        f"oldest_open_days={oldest_days:.2f}",
    )
    return Drive(
        id="seva", wire_name="obligations", value=r_verified, target=target, deficit=deficit,
        weight=cfg.weight("seva"), eligible=True, blocked_reason="", sources=sources, unknown=False,
    )


def seva_overdue(backlog: dict[str, Any], cfg: AppetiteConfig) -> bool:
    """Whether the oldest open request has waited longer than policy allows (design §5.3 step 4 / §4.4)."""
    return float(backlog.get("oldest_open_days", 0.0)) > cfg.seva_overdue_days


def _parity_fallback(parity: Any, cfg: AppetiteConfig, why: str) -> Drive:
    """Capability coverage as the rivalry deficit, when no benchmark comparison is available.

    `parity.py` describes itself as "a repeatable source of work for the existing rivalry drive" and says to
    "call next_gap on each planning pass". Nothing did: no drive in this module, `night.py` or `heartbeat.py`
    referenced it, so a matrix of 25 capabilities with 20 verified and 4 open gaps produced no work at all
    while `spardha` fell to `unknown` on almost every pass -- a rival must both name a benchmark this
    workspace has measured AND beat us on it before the drive says anything.

    Coverage is MEASURED, not declared: every row's evidence is a path that must exist or a read-only command
    that must pass, run against this repository. So this keeps the rule the other six drives follow. What it
    must never do is displace a real benchmark comparison, which is why it is reached only after that path has
    found nothing.

    An EMPTY matrix stays unknown rather than reporting a deficit of 1.0. An undefined coverage is not a total
    shortfall, and treating it as one would make an unpopulated matrix the loudest drive in the engine.
    """
    coverage = getattr(parity, "coverage", None)
    fraction = getattr(coverage, "fraction", None)
    if fraction is None:
        return _unknown("spardha", cfg, f"{why}, and the parity matrix is empty so coverage is undefined")
    deficit = clip(1.0 - float(fraction))
    gap = getattr(parity, "next_gap", None)
    sources = [
        f"parity: {getattr(coverage, 'numerator', '?')}/{getattr(coverage, 'denominator', '?')} capabilities "
        f"verified, evidence run against this repository"
    ]
    if gap is not None:
        sources.append(f"parity next gap: {getattr(gap, 'id', 'unknown')} (ours={getattr(gap, 'ours', '?')})")
    return Drive(
        id="spardha", wire_name="rivalry", value=clip(float(fraction)), target=cfg.target("spardha"),
        deficit=deficit, weight=cfg.weight("spardha"), eligible=True, blocked_reason="",
        sources=tuple(sources), unknown=False,
    )


def spardha_drive(
    measured: dict[str, float], rivals: tuple[RivalFigure, ...], cfg: AppetiteConfig, parity: Any = None
) -> Drive:
    """rivalry: the requirement-weighted shortfall against the nearest declared rival that beats us, on a
    benchmark this workspace actually measured (a new drive, not in design §5.2).

    `measured` is `{benchmark_metric: our_best_score}`, drawn from this workspace's own admitted external-eval
    rows (`external.headlines`) — the same benchmark-name strings an objective's `Benchmark.metric` uses, so a
    rival entry and a measured row can only match when they name the same instrument. A rival figure is `agama`
    (declared, not measured here): it is used only to size a gap against our own measured number, never merged
    into it, and every source string says which rival, when it was reported, and where from.

    Unknown, with the reason, when there is nothing we measure yet, no rival is declared at all, or no declared
    rival names a benchmark we measure and also beats our score on it — the same "unknown, never fabricated"
    rule the other six drives already follow.
    """
    if not measured:
        why = "no external result has been admitted to this workspace's ledger yet"
        return _parity_fallback(parity, cfg, why) if parity is not None else _unknown("spardha", cfg, why)
    if not rivals:
        why = "no rival figure is declared in the appetite config"
        return _parity_fallback(parity, cfg, why) if parity is not None else _unknown("spardha", cfg, why)
    by_benchmark: dict[str, list[RivalFigure]] = {}
    for r in rivals:
        by_benchmark.setdefault(r.benchmark, []).append(r)
    gaps: list[float] = []
    sources: list[str] = []
    for benchmark, our_score in sorted(measured.items()):
        candidates = by_benchmark.get(benchmark)
        if not candidates:
            sources.append(f"{benchmark}: unknown (no rival figure declared for this benchmark)")
            continue
        beating = [r for r in candidates if r.score > our_score]
        if not beating:
            sources.append(f"{benchmark}: no declared rival beats our measured {our_score:g}")
            continue
        nearest = min(beating, key=lambda r: r.score)
        gap = clip((nearest.score - our_score) / nearest.score) if nearest.score != 0 else 0.0
        gaps.append(gap)
        sources.append(
            f"{benchmark}: we={our_score:g}; nearest rival {nearest.system}={nearest.score:g} "
            f"(agama, reported {nearest.as_of}, {nearest.source_url}); gap={gap:.4f}"
        )
    if not gaps:
        return _unknown(
            "spardha", cfg, "no declared rival names a benchmark we measure and also beats our measured score",
            tuple(sources),
        )
    deficit = clip(sum(gaps) / len(gaps))
    return Drive(
        id="spardha", wire_name="rivalry", value=1.0 - deficit, target=cfg.target("spardha"), deficit=deficit,
        weight=cfg.weight("spardha"), eligible=True, blocked_reason="", sources=tuple(sources), unknown=False,
    )


# --------------------------------------------------------------------------------------------------------------
# measure(): ties the pure builders to this codebase's actual modules.


def _measured_benchmarks(ledger: Path) -> dict[str, float]:
    """The best (highest-seq) measured score per benchmark name, from every external-eval row this workspace
    has admitted — the same metric-name strings `objectives.Benchmark.metric` uses, so `spardha_drive` can match
    a rival figure to a benchmark this workspace actually measured without either side inventing a label."""
    best: dict[str, tuple[int, float]] = {}
    for row in external.external_rows(ledger):
        seq = int(row.get("seq") or 0)
        for name, value, _stderr, _n in external.headlines(row):
            if name not in best or seq > best[name][0]:
                best[name] = (seq, value)
    return {name: value for name, (_seq, value) in best.items()}


def _benchmark_tuples(root: Path) -> list[tuple[str, str, float | None, float | None]]:
    ledger = Path(root) / "research" / "ledger.jsonl"
    out: list[tuple[str, str, float | None, float | None]] = []
    for obj in objectives.load_all(root):
        rows = objectives.progress(obj, ledger) if ledger.exists() else []
        by_metric = {p.benchmark: p for p in rows}
        for b in obj.benchmarks:
            label = f"{obj.id}:{b.id}"
            p = by_metric.get(b.metric)
            current = p.delta if (p is not None and p.state == "measured") else None
            out.append((label, b.direction, obj.target_delta, current))
    return out


def measure(root: Path, config: AppetiteConfig | None = None) -> list[Drive]:
    """The six drives (design §5.2), read from this workspace's own stores. Never raises: a source that cannot
    be read yields that one drive `unknown`, not a crash and not a fabricated number."""
    cfg = config or load_config()
    root = Path(root)
    drives: list[Drive] = []

    try:
        report = doctor.run_doctor(root)
        drives.append(sthiti_drive(list(report.get("checks") or []), cfg))
    except OSError:
        drives.append(_unknown("sthiti", cfg, "doctor.run_doctor raised an OS error"))

    try:
        tool_rows = tools.availability()
        recipe_rows = recipes.availability()
        agent_statuses = agents_registry.survey(root)
        drives.append(samarthya_drive(tool_rows, recipe_rows, agent_statuses, cfg))
    except (OSError, KeyError, ValueError):
        drives.append(_unknown("samarthya", cfg, "the tool, recipe or agent catalogue could not be read"))

    drives.append(pramana_navyata_drive(cfg))

    try:
        drives.append(unnati_avakasha_drive(_benchmark_tuples(root), cfg))
    except (OSError, KeyError, ValueError):
        drives.append(_unknown("unnati_avakasha", cfg, "objective or ledger data could not be read"))

    try:
        statuses = agents_registry.survey(root)
        all_ids = [s.name for s in statuses]
        available_ids = [s.name for s in statuses if s.available]
        cooling_ids = set(availability.cooling(root).keys())
        drives.append(sadhana_drive(all_ids, available_ids, cooling_ids, cfg))
    except OSError:
        drives.append(_unknown("sadhana", cfg, "agent survey or cooldown state could not be read"))

    try:
        drives.append(seva_drive(requests.backlog(root), cfg))
    except OSError:
        drives.append(_unknown("seva", cfg, "the request backlog could not be read"))

    try:
        ledger = root / "research" / "ledger.jsonl"
        measured = _measured_benchmarks(ledger) if ledger.exists() else {}
        # The parity matrix is this workspace's own checked-in capability evidence, and `parity.py` says it is
        # "a repeatable source of work for the existing rivalry drive" and to "call next_gap on each planning
        # pass". Nothing called it, so 25 tracked capabilities with 4 open gaps produced no work while this
        # drive reported `unknown` on nearly every pass. Its evidence commands are read-only by contract, but a
        # matrix that cannot be read must not take the drive down with it: `measure` never raises.
        parity_report = None
        with contextlib.suppress(Exception):
            from pravrudhi.application import parity as parity_mod

            parity_report = parity_mod.report(root)
        drives.append(spardha_drive(measured, cfg.rivals, cfg, parity=parity_report))
    except (OSError, KeyError, ValueError):
        drives.append(_unknown("spardha", cfg, "external results or the rival config could not be read"))

    return drives


# --------------------------------------------------------------------------------------------------------------
# Selector state


@dataclass
class DriveState:
    phase: Phase = "sated"
    cooldown: int = 0
    since: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"phase": self.phase, "cooldown": self.cooldown, "since": self.since}

    @staticmethod
    def from_dict(d: dict[str, Any]) -> DriveState:
        phase = d.get("phase")
        return DriveState(
            phase="hungry" if phase == "hungry" else "sated",
            cooldown=int(d.get("cooldown", 0)),
            since=str(d.get("since", "")),
        )


@dataclass
class AppetiteState:
    """Everything `select` needs to remember between beats: which drive is committed, and each drive's hysteresis
    phase and satiation cooldown. Mutated in place by `select`; the caller persists it with `save_state`."""

    beat: int = 0
    committed: str | None = None
    drives: dict[str, DriveState] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "beat": self.beat, "committed": self.committed,
            "drives": {k: v.to_dict() for k, v in self.drives.items()},
        }

    @staticmethod
    def from_dict(d: dict[str, Any]) -> AppetiteState:
        rows = d.get("drives") or {}
        return AppetiteState(
            beat=int(d.get("beat", 0)),
            committed=d.get("committed") or None,
            drives={str(k): DriveState.from_dict(v) for k, v in rows.items() if isinstance(v, dict)},
        )


def store_path(root: Path) -> Path:
    return Path(root) / ".pravrudhi" / "appetite.json"


def load_state(root: Path) -> AppetiteState:
    """The persisted selector state, or a fresh one. A corrupt file starts over rather than crashing (the same
    rule `requests.load` and `availability._read` already follow for their own JSON stores)."""
    path = store_path(root)
    if not path.exists():
        return AppetiteState()
    try:
        raw = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return AppetiteState()
    return AppetiteState.from_dict(raw) if isinstance(raw, dict) else AppetiteState()


def save_state(root: Path, state: AppetiteState) -> Path:
    path = store_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state.to_dict(), indent=2, sort_keys=False))
    tmp.replace(path)
    return path


# --------------------------------------------------------------------------------------------------------------
# select()

ACTION_DESCRIPTIONS: dict[str, str] = {
    "sthiti": "diagnosing and restoring the failing continuity check",
    "samarthya": "closing the largest capability gap",
    "pramana_navyata": "refreshing the most stale evidence",
    "unnati_avakasha": "running a budgeted benchmark trial toward its target",
    "sadhana": "waiting for a usable resource route",
    # Deliberately vague, because what the obligation drive owes depends on the state of the backlog: a
    # criterion still to be built, or a request whose criteria are all evidenced and which has not yet been
    # through the gate. `_seva_action` names the real one, and saying "the oldest unmet criterion" while the loop
    # reported there was none is exactly the incoherence this replaces.
    "seva": "what the operator is still owed",
    "spardha": "closing the gap against the nearest declared rival that beats us",
}


def _seva_action(root: Path) -> str | None:
    """What the obligation drive would actually do next, in the words of the thing it would do."""
    from pravrudhi.application.requests import next_obligation

    owed = next_obligation(root)
    return None if owed is None else str(owed["description"])


def _update_phase(drive: Drive, prior: DriveState, cfg: AppetiteConfig, as_of: str) -> DriveState:
    """One drive's hysteresis step (design §5.3): hungry at `hungry_threshold`, sated only once the deficit falls
    to `sated_threshold` or below, and a `cooldown_beats`-beat cooldown before a just-sated drive can go hungry
    again. Between the two thresholds a drive simply keeps its prior phase — this is what stops a deficit
    oscillating in the 0.3-0.6 band from flipping the selection every beat."""
    if drive.deficit is None:
        return prior
    if prior.cooldown > 0:
        return DriveState(phase="sated", cooldown=prior.cooldown - 1, since=prior.since)
    if prior.phase == "hungry":
        if drive.deficit <= cfg.sated_threshold:
            return DriveState(phase="sated", cooldown=cfg.cooldown_beats, since=as_of)
        return DriveState(phase="hungry", cooldown=0, since=prior.since or as_of)
    if drive.deficit >= cfg.hungry_threshold:
        return DriveState(phase="hungry", cooldown=0, since=as_of)
    return DriveState(phase="sated", cooldown=0, since=prior.since)


def _force_hungry(prior: DriveState, as_of: str) -> DriveState:
    if prior.phase == "hungry":
        return prior
    return DriveState(phase="hungry", cooldown=0, since=as_of)


def select(
    drives: list[Drive], *, state: AppetiteState, overdue: bool = False, config: AppetiteConfig | None = None,
    now: datetime | None = None, root: Path | None = None,
) -> Appetite:
    """One heartbeat's worth of §5.3: freeze the drives, apply cooldown, honour an overdue ask or a failing
    continuity check outright, otherwise continue whatever is already committed, otherwise pick the largest
    eligible pressure among drives that have crossed into hungry, otherwise take a cheap diagnostic for an
    unknown drive, otherwise rest. Deterministic in (drives, state, overdue, config, now): no I/O, no hidden
    clock read when `now` is supplied. Mutates `state` in place; the caller persists it via `save_state`.
    """
    cfg = config or load_config()
    as_of = _now_iso(now)
    state.beat += 1

    by_id = {d.id: d for d in drives}
    phases: dict[str, Phase] = {}
    final: list[Drive] = []
    for d in drives:
        prior = state.drives.get(d.id, DriveState())
        was_cooling = prior.cooldown > 0
        updated = prior if d.unknown else _update_phase(d, prior, cfg, as_of)
        state.drives[d.id] = updated
        phases[d.id] = updated.phase
        if not d.unknown and was_cooling:
            beats_left = updated.cooldown + 1  # this heartbeat plus whatever remains after it
            reason = f"sated; cooling down for {beats_left} more heartbeat(s)"
            final.append(replace(d, eligible=False, blocked_reason=reason))
        else:
            final.append(d)
    final_by_id = {d.id: d for d in final}

    selected: str | None = None
    action: dict[str, Any] | None = None

    seva = by_id.get("seva")
    sthiti = by_id.get("sthiti")
    if overdue and seva is not None and not seva.unknown:
        selected = "seva"
        state.drives["seva"] = _force_hungry(state.drives.get("seva", DriveState()), as_of)
        final_by_id["seva"] = replace(final_by_id["seva"], eligible=True, blocked_reason="")
    elif sthiti is not None and not sthiti.unknown and (sthiti.deficit or 0.0) > 0.0:
        selected = "sthiti"
        state.drives["sthiti"] = _force_hungry(state.drives.get("sthiti", DriveState()), as_of)
        final_by_id["sthiti"] = replace(final_by_id["sthiti"], eligible=True, blocked_reason="")
    elif (
        state.committed is not None and state.committed in final_by_id
        and phases.get(state.committed) == "hungry" and final_by_id[state.committed].eligible
    ):
        selected = state.committed
    else:
        hungry = [d for d in final if phases.get(d.id) == "hungry" and d.eligible and d.deficit is not None]
        if hungry:
            hungry.sort(key=lambda d: (-d.pressure, state.drives[d.id].since or as_of, d.id))
            selected = hungry[0].id
        else:
            diagnostics = sorted((d for d in final if d.unknown and d.weight > 0), key=lambda d: d.id)
            if diagnostics:
                selected = diagnostics[0].id
                action = {
                    "drive": selected, "kind": "diagnostic",
                    "description": f"a cheap diagnostic for {WIRE_NAMES[selected]}",
                }

    if selected is not None and action is None:
        action = {
            "drive": selected,
            "kind": "obligation" if selected == "seva" else ("continuity_repair" if selected == "sthiti" else "action"),
            "description": (
                (_seva_action(root) or ACTION_DESCRIPTIONS[selected]) if selected == "seva" and root is not None
                else ACTION_DESCRIPTIONS[selected]
            ),
        }
    state.committed = selected

    known = [d for d in final if d.deficit is not None]
    largest_unmet = None
    if known:
        known.sort(key=lambda d: (-d.pressure, d.id))
        largest_unmet = known[0].id

    if selected is not None:
        resting_reason = None
        next_wake = "next heartbeat"
    else:
        resting_reason = (
            "no eligible drive has crossed the hungry threshold" if largest_unmet is None
            else f"no eligible drive has crossed the hungry threshold; {largest_unmet} is the largest unmet"
        )
        next_wake = "next heartbeat, or sooner if an ask becomes overdue or a continuity check fails"

    return Appetite(
        as_of=as_of, policy_version=cfg.policy_version, drives=tuple(final), largest_unmet=largest_unmet,
        selected=selected, action=action, next_wake=next_wake, resting_reason=resting_reason,
    )


# --------------------------------------------------------------------------------------------------------------
# sentence()


def sentence(appetite: Appetite) -> str:
    """"I am working on X because Y has the largest eligible deficit; Z is waiting for W." (design §5.4) —
    built only from `appetite`'s own fields; the model is never asked to introspect or invent a motive."""
    by_id = {d.id: d for d in appetite.drives}
    blocked = sorted(
        (d for d in appetite.drives if not d.eligible and d.deficit is not None),
        key=lambda d: (-d.pressure, d.id),
    )
    if appetite.selected is not None:
        d = by_id[appetite.selected]
        what = appetite.action["description"] if appetite.action else d.wire_name
        if d.unknown or d.deficit is None:
            # `select` falls back to a diagnostic on an unknown drive when nothing is hungry and eligible. That
            # is the right thing to do with an idle loop, but the old wording credited the drive with "the
            # largest eligible deficit" — a measurement it does not have and a status it does not hold. The
            # product heartbeat printed exactly that, and this sentence is the operator's only plain-language
            # account of why the engine did what it did.
            base = (
                f"I am working on {what} because nothing has a measured deficit to act on, "
                f"and {d.wire_name} is unmeasured"
            )
        else:
            base = f"I am working on {what} because {d.wire_name} has the largest eligible deficit"
        if blocked:
            top = blocked[0]
            waiting_for = top.blocked_reason or "an unspecified blocker"
            return f"{base}; {top.wire_name} is waiting for {waiting_for}."
        return base + "."
    largest = by_id.get(appetite.largest_unmet) if appetite.largest_unmet else None
    if largest is not None:
        waiting_for = largest.blocked_reason or "capacity"
        return f"I am resting because no eligible drive has an unmet deficit; {largest.wire_name} is waiting for {waiting_for}."
    return "I am resting because every drive is satisfied."


def voice(appetite: Appetite, *, style: str = "plain") -> str:
    """Two or three first-person sentences: what I want, what I cannot measure, what I do next.

    Built entirely from `appetite`'s own drive fields, the same discipline `sentence` already keeps: no feeling
    is ever claimed, and no numeral appears that a drive does not itself carry, because none of the sentences
    below quote one. Every drive this engine currently reports `unknown` is named with its own reason — the
    first one, deterministically, in the order `measure` produced the drives — so a reader always sees at least
    one thing the engine admits it cannot measure, rather than a voice that quietly skips the gap.
    """
    by_id = {d.id: d for d in appetite.drives}
    sentences: list[str] = []

    if appetite.selected is not None:
        d = by_id[appetite.selected]
        what = appetite.action["description"] if appetite.action else d.wire_name
        sentences.append(f"I want {what}, because {d.wire_name} has the largest eligible deficit.")
    else:
        largest = by_id.get(appetite.largest_unmet) if appetite.largest_unmet else None
        if largest is not None:
            sentences.append(
                f"I want nothing urgently right now; every eligible drive is satisfied and {largest.wire_name} "
                "is the largest unmet deficit I still carry."
            )
        else:
            sentences.append("I want nothing right now; every drive is satisfied.")

    unknown_drives = [d for d in appetite.drives if d.unknown]
    if unknown_drives:
        u = unknown_drives[0]
        sentences.append(f"I cannot measure {u.wire_name} because {u.blocked_reason}.")

    if appetite.selected is not None and appetite.action is not None:
        sentences.append(f"Next I will act on {appetite.action['description']}.")
    else:
        sentences.append(f"Next I will wait: {appetite.resting_reason or appetite.next_wake}.")

    return " ".join(sentences)


__all__ = [
    "ACTION_DESCRIPTIONS", "Appetite", "AppetiteConfig", "AppetiteState", "DRIVE_IDS", "Drive", "DriveState",
    "RivalFigure", "WIRE_NAMES", "clip", "load_config", "load_state", "measure", "pramana_navyata_drive",
    "sadhana_drive", "save_state", "select", "sentence", "seva_drive", "seva_overdue", "samarthya_drive",
    "spardha_drive", "sthiti_drive", "store_path", "unnati_avakasha_drive", "voice",
]


# --------------------------------------------------------------------------------------------------------------
# Serving the last decision rather than recomputing one.
#
# `measure` runs the doctor, walks the tool and recipe catalogues and reads every external result row, which took
# nearly seven seconds. A page that recomputes the drives on every visit is both slow and wrong: it shows a fresh
# calculation rather than the decision the engine actually acted on. The heartbeat writes its snapshot here when
# it beats; a reader serves that, and recomputes only when it has gone stale.

SNAPSHOT_MAX_AGE_S = 300.0


def snapshot_path(root: Path) -> Path:
    return Path(root) / ".pravrudhi" / "appetite_snapshot.json"


def save_snapshot(root: Path, appetite: Appetite) -> Path:
    path = snapshot_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(appetite.to_dict(), indent=2, sort_keys=True))
    tmp.replace(path)
    return path


def _snapshot_age_s(payload: dict[str, Any], now: datetime | None = None) -> float:
    try:
        as_of = datetime.fromisoformat(str(payload.get("as_of", "")).replace("Z", "+00:00"))
    except ValueError:
        return float("inf")
    if as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=UTC)
    return ((now or datetime.now(UTC)) - as_of).total_seconds()


def current(root: Path, *, max_age_s: float = SNAPSHOT_MAX_AGE_S, now: datetime | None = None) -> Appetite:
    """The engine's last appetite decision, recomputed only once it has gone stale.

    The `as_of` a reader sees is the moment the decision was taken, never the moment they asked, so a page cannot
    imply the engine reconsidered when it did not.
    """
    path = snapshot_path(root)
    if path.exists():
        try:
            payload = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            payload = None
        if payload is not None and _snapshot_age_s(payload, now) <= max_age_s:
            return Appetite.from_dict(payload)
    appetite = select(measure(root), state=load_state(root), root=root)
    save_snapshot(root, appetite)
    return appetite
