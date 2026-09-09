"""Choosing which agent and model does a piece of delegated work, from measured outcomes rather than from taste.

The routing table used to be four hardcoded pairs. Two of the four were wrong, and neither error was visible until
someone looked at a bill. The mechanical tier pointed at a local open-weight model that produced no change at all
on two tasks a hosted agent finished in minutes. The standard tier passed no model at all, which takes the agent's
default, and on this account the default is the most expensive model available -- so three of the four tiers were
quietly paying the top rate. Twenty sessions in one day cost seven million input tokens, nearly all of it on work
that did not need that model.

A hardcoded table cannot notice either mistake, because it records a belief and never a result. This module records
results. Every dispatch appends an outcome -- tier, route, accepted or not, how long it took -- to a routing log,
and the next choice is made from those outcomes: among the routes permitted at a tier, take the cheapest whose
success rate is not distinguishably worse than the best route's, using the same Wilson interval the rest of the
engine uses for a proportion. Below a declared minimum number of trials a route has no record, and the declared
order decides.

Two boundaries matter and are enforced rather than assumed.

The routing log is not the ledger. It records what this engine spent on its own upkeep, not what any experiment
measured, so it carries no pramana tag, it is not replayed, and no evidence document may cite it. It lives beside
the workspace's other operational state.

The router never widens a route's permissions. A route serves the tiers its configuration names and no others, so
the router cannot decide to try the cheapest model on the most critical work because the cheap one has been lucky.
Promoting a route to a harder tier is a human edit to `configs/routing.yaml`.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from pravrudhi.application import availability
from pravrudhi_kernel.stats import wilson_ci

PACKAGED_CONFIG = Path(__file__).resolve().parents[1] / "assets" / "configs" / "routing.yaml"


class RoutingError(ValueError):
    """A routing table that would send work to a route not permitted at that tier."""


@dataclass(frozen=True)
class Route:
    id: str
    agent: str
    model: str
    relative_cost: float
    tiers: tuple[str, ...]
    note: str = ""
    token_budget: int = 0
    """Tokens this route may spend in a rolling week, or 0 for "not declared".

    Cost ratio answers "which is cheaper per unit of work"; it does not answer "how much of this is left". A
    router with only the first will spend the second and be surprised, which is what happened on 2026-09-08 when
    a one-week plan allowance went inside a day. Only a seat whose allowance is actually known declares one —
    inventing a budget for a differently-billed account would be a number the engine does not have."""

    sentinel: bool = False
    """A standby that takes over, rather than a route that competes.

    Declaring it last was not enough: the chooser prefers the cheapest route whose interval overlaps the best,
    and a free route with no measured trials is always the cheapest and never rules itself out, so the sentinel
    won four complex tasks it cannot do. A sentinel is now excluded from scoring outright and becomes reachable
    only when every ordinary route at the tier is cooling down."""

    def pair(self) -> tuple[str, str]:
        return self.agent, self.model


@dataclass(frozen=True)
class Outcome:
    """One completed dispatch. `accepted` is the swarm's own verdict: the diff was in scope and the task's check
    passed. It is the only success signal available without a human reading the change.

    `limited` marks a dispatch that ended in a vendor usage limit rather than an ordinary pass or fail: it is not
    evidence about the route's quality, so it must never move `accepted`'s trial count either way."""

    tier: str
    route_id: str
    task_id: str
    accepted: bool
    wall_s: float
    at: str = ""
    limited: bool = False
    tokens: int | None = None
    """What this dispatch consumed, cache included, when the adapter could tell.

    `None` is "the seat could not tell"; zero is "the seat says it was free". This field was `int = 0` carrying
    the docstring "Zero means unknown, never free" — a distinction the type could not hold. Counted on
    2026-09-09, 155 of the 157 recorded outcomes read zero, so `spend` was summing 1.3% of the dispatches and
    `over_budget` could essentially never trip."""

    cost_usd: float | None = None
    """What the vendor said it cost. Cheaper to trust than a token sum, because cache reads, cache writes and
    output are billed at different rates. `cli_agents` already extracted this and the record dropped it."""

    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    """The prompt-cache ratio's two halves. Recorded rather than summed into `tokens`, because the ratio is the
    target and a sum destroys it."""


@dataclass(frozen=True)
class Record:
    """What the log says about one route at one tier."""

    route_id: str
    tier: str
    trials: int
    successes: int
    rate: float
    lo: float
    hi: float
    mean_wall_s: float
    relative_cost: float

    @property
    def measured(self) -> bool:
        return self.trials > 0


@dataclass(frozen=True)
class Choice:
    """A routing decision and the reason for it, so a surprising route can be explained without guessing."""

    tier: str
    route: Route
    reason: str
    considered: tuple[str, ...]
    records: tuple[Record, ...]


@dataclass(frozen=True)
class Table:
    routes: dict[str, Route]
    declared: dict[str, tuple[str, ...]]
    minimum_trials: int
    confidence: float
    prefer: dict[str, str] = field(default_factory=dict)
    """Tiers whose route the operator has named, which the cost heuristic does not get to overrule."""

    def preferred(self, tier: str) -> str | None:
        """The route the operator named for this tier, if any. A stated preference, not a measured one."""
        value = self.prefer.get(tier) if isinstance(self.prefer, dict) else None
        return str(value) if value else None

    def permitted(self, tier: str) -> list[Route]:
        """Routes allowed at this tier, in declared order, with any permitted route the declaration forgot
        appended. A route missing from `declared` is a configuration slip, not a reason to refuse work."""
        order = list(self.declared.get(tier, ()))
        named = [self.routes[r] for r in order if r in self.routes and tier in self.routes[r].tiers]
        rest = [r for r in self.routes.values() if tier in r.tiers and r.id not in {x.id for x in named}]
        return named + sorted(rest, key=lambda r: (r.relative_cost, r.id))


def load_table(path: Path | None = None) -> Table:
    raw = yaml.safe_load((path or PACKAGED_CONFIG).read_text())
    routes = {
        str(r["id"]): Route(
            id=str(r["id"]),
            agent=str(r["agent"]),
            model=str(r["model"]),
            relative_cost=float(r["relative_cost"]),
            tiers=tuple(str(t) for t in (r.get("tiers") or ())),
            token_budget=int(r.get("token_budget") or 0),
            note=str(r.get("note") or "").strip(),
            sentinel=bool(r.get("sentinel", False)),
        )
        for r in raw["routes"]
    }
    declared = {str(k): tuple(str(x) for x in v) for k, v in (raw.get("declared") or {}).items()}
    unknown = {r for names in declared.values() for r in names} - set(routes)
    if unknown:
        raise RoutingError(f"declared order names routes that do not exist: {', '.join(sorted(unknown))}")
    return Table(
        routes=routes,
        declared=declared,
        prefer={str(k): str(v) for k, v in (raw.get("prefer") or {}).items()},
        minimum_trials=int(raw.get("minimum_trials", 3)),
        confidence=float(raw.get("confidence", 0.95)),
    )


def log_path(root: Path) -> Path:
    return Path(root) / ".pravrudhi" / "routing.jsonl"


def record_outcome(root: Path, outcome: Outcome, table: Table | None = None) -> None:
    """Append one outcome. Operational state, deliberately not the ledger: this is what the engine spent on its own
    upkeep, and no evidence document may cite it.

    A `limited` outcome additionally starts a cooldown for the route's agent, so the very next dispatch does not
    walk straight back into the same usage limit. `table` is accepted so a caller that already loaded one is not
    made to pay for a second `load_table()`; it is loaded here when omitted."""
    p = log_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    row = asdict(outcome)
    row["at"] = outcome.at or datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    with p.open("a") as fh:
        fh.write(json.dumps(row, sort_keys=True) + "\n")
    if outcome.limited:
        route = (table or load_table()).routes.get(outcome.route_id)
        if route is not None:
            availability.mark_limited(root, route.agent)


def outcomes(root: Path) -> list[Outcome]:
    p = log_path(root)
    if not p.exists():
        return []
    out: list[Outcome] = []
    for line in p.read_text().splitlines():
        if not line.strip():
            continue
        try:
            d = json.loads(line)
            out.append(Outcome(tier=d["tier"], route_id=d["route_id"], task_id=d.get("task_id", ""),
                               accepted=bool(d["accepted"]), wall_s=float(d.get("wall_s", 0.0)), at=d.get("at", ""),
                               limited=bool(d.get("limited", False)),
                               tokens=_maybe_int(d.get("tokens")),
                               cost_usd=_maybe_float(d.get("cost_usd")),
                               cache_read_tokens=_maybe_int(d.get("cache_read_tokens")),
                               cache_write_tokens=_maybe_int(d.get("cache_write_tokens"))))
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            continue  # a corrupt line must not blind the router to the rest
    return out


def records(table: Table, rows: list[Outcome], tier: str) -> list[Record]:
    """What the log says about each route permitted at this tier, including routes with no trials at all.

    A `limited` outcome is excluded here rather than merely uncounted as a loss: it is not evidence about the
    route's quality, so it must not appear in `trials` either, or a route that is otherwise flawless would look
    unproven just because its account happened to be rate-limited a few times."""
    out: list[Record] = []
    for route in table.permitted(tier):
        mine = [o for o in rows if o.tier == tier and o.route_id == route.id and not o.limited]
        n = len(mine)
        k = sum(1 for o in mine if o.accepted)
        lo, hi = wilson_ci(k, n) if n else (0.0, 1.0)
        out.append(Record(
            route_id=route.id, tier=tier, trials=n, successes=k,
            rate=(k / n) if n else 0.0, lo=lo, hi=hi,
            mean_wall_s=(sum(o.wall_s for o in mine) / n) if n else 0.0,
            relative_cost=route.relative_cost,
        ))
    return out


def choose(table: Table, rows: list[Outcome], tier: str, root: Path | None = None) -> Choice:
    """The cheapest route that is not distinguishably worse than the best one at this tier.

    "Distinguishably worse" compares two intervals, not an interval against a point: a route is ruled out only when
    its upper bound lies below the best route's lower bound. Comparing against the best route's point estimate was
    the first form of this and it was wrong in a way worth recording, because a route with a perfect record has a
    point estimate of 1.0 and nothing can reach it -- so a flawless run of four would have evicted every cheaper
    route on evidence that could not distinguish them.

    The test is deliberately weak. With a handful of trials almost nothing is distinguishable, so the router spends
    cheaply until the log gives it a reason not to, which is the correct default when the expensive option's
    advantage is unproven.

    `root` is optional so every existing caller keeps working unchanged; passing it lets the router also see which
    agents are cooling down from a recent usage-limit hit (`availability.usable_routes`) and route around them. A
    route whose agent is cooling is dropped from scoring, not merely deprioritised, because a limited account cannot
    do the work at all right now. If every permitted route is cooling, dropping them all would leave nothing to
    dispatch, so the cheapest one is returned anyway and the reason says the fallback was forced.
    """
    permitted = table.permitted(tier)
    if not permitted:
        raise RoutingError(f"no route is permitted at tier {tier!r}; check configs/routing.yaml")

    usable = availability.usable_routes(root, permitted) if root is not None else permitted

    # A seat that has spent its declared weekly allowance is dropped exactly as a cooling one is: work moves to
    # another route rather than stopping, because running out of cheap capacity should make the engine dearer,
    # not idle. This is the check the router did not have on 2026-09-08, when a one-week plan allowance went in a
    # day and the first anyone knew was a 429 saying come back in six.
    exhausted: set[str] = over_budget(root, table) if root is not None else set()
    if exhausted:
        within = [r for r in usable if r.id not in exhausted]
        if within:  # if every route is spent, spending is no longer the deciding fact
            usable = within

    # Sentinels stand by. They enter the running only when nothing ordinary is left standing at this tier.
    ordinary = [r for r in usable if not r.sentinel]
    standby = [r for r in usable if r.sentinel]
    if ordinary:
        usable = ordinary
    elif standby:
        cheapest = min(standby, key=lambda r: (r.relative_cost, r.id))
        reason = (f"every ordinary route at this tier is cooling down from a usage limit; the standby "
                  f"{cheapest.id} takes over")
        return Choice(tier, cheapest, reason, tuple(r.id for r in standby), tuple(records(table, rows, tier)))

    if root is not None and not usable:
        cheapest = min(permitted, key=lambda r: (r.relative_cost, r.id))
        reason = (f"every route permitted at this tier is cooling down from a recent usage-limit hit; forcing "
                  f"{cheapest.id} anyway rather than stall")
        return Choice(tier, cheapest, reason, tuple(r.id for r in permitted), tuple(records(table, rows, tier)))

    # A tier the operator has spoken for takes its route from that, not from the cost heuristic — unless the
    # named route is cooling or unavailable here, in which case the ordinary rules resume.
    wanted = table.preferred(tier)
    if wanted:
        named = next((r for r in usable if r.id == wanted), None)
        if named is not None:
            return Choice(
                tier, named,
                f"{wanted} is the route declared for this tier by the operator, so cost does not decide it",
                tuple(r.id for r in usable), tuple(records(table, rows, tier)),
            )

    considered = tuple(r.id for r in usable)
    usable_ids = set(considered)
    dropped = [r.id for r in permitted if r.id not in usable_ids]
    spent_out = sorted(exhausted & {r.id for r in permitted}) if root is not None else []
    cooling_suffix = f"; dropped cooling route(s) {', '.join(dropped)}" if dropped else ""
    if spent_out:
        cooling_suffix += f"; dropped for weekly token allowance: {', '.join(spent_out)}"

    rs_all = records(table, rows, tier)
    rs = [r for r in rs_all if r.route_id in usable_ids]

    seasoned = [r for r in rs if r.trials >= table.minimum_trials]
    if not seasoned:
        first = usable[0]
        return Choice(tier, first, f"no route has {table.minimum_trials} outcomes at this tier yet, so the "
                                   f"declared order decides{cooling_suffix}", considered, tuple(rs_all))

    best = max(seasoned, key=lambda r: (r.rate, -r.relative_cost))
    viable = [r for r in rs if r.trials < table.minimum_trials or r.hi >= best.lo]
    if not viable:
        viable = [best]
    pick = min(viable, key=lambda r: (r.relative_cost, considered.index(r.route_id)))
    route = table.routes[pick.route_id]

    if pick.route_id == best.route_id:
        reason = (f"{pick.route_id} has the best measured success rate at this tier "
                  f"({pick.successes}/{pick.trials}) and nothing cheaper matches it{cooling_suffix}")
    elif pick.trials < table.minimum_trials:
        reason = (f"{pick.route_id} is cheaper than {best.route_id} and has only {pick.trials} outcomes, "
                  f"too few to rule out; trying it{cooling_suffix}")
    else:
        reason = (f"{pick.route_id} costs {pick.relative_cost:g} against {best.route_id}'s "
                  f"{best.relative_cost:g} and their intervals overlap "
                  f"({pick.successes}/{pick.trials} against {best.successes}/{best.trials}), so the extra "
                  f"spend is not yet justified{cooling_suffix}")
    return Choice(tier, route, reason, considered, tuple(rs_all))


def _maybe_int(value: Any) -> int | None:
    """`None` stays `None`. Reading an absent cost back as zero is how the distinction was lost in the first place."""
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _maybe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def cost_coverage(root: Path) -> dict[str, Any]:
    """How much of the routing record actually carries a cost, overall and per route.

    `spend` sums what it can see and says nothing about what it cannot, which is how a token budget stopped
    being a control without anyone noticing: on 2026-09-09 it was measuring a weekly allowance against two of
    157 dispatches. A target on token efficiency is meaningless until this share is high, so the share itself is
    the first thing to report and the first thing to move.
    """
    rows = outcomes(root)
    by_route: dict[str, dict[str, int]] = {}
    measured = nonzero = 0
    for row in rows:
        seat = by_route.setdefault(row.route_id, {"total": 0, "measured": 0, "measured_nonzero": 0})
        seat["total"] += 1
        if row.tokens is not None:
            seat["measured"] += 1
            measured += 1
            if row.tokens:
                seat["measured_nonzero"] += 1
                nonzero += 1
    return {
        "total": len(rows),
        "measured": measured,
        "unmeasured": len(rows) - measured,
        "share_measured": round(measured / len(rows), 4) if rows else 0.0,
        # Rows written before 2026-09-09 encoded "unknown" as zero, so `measured` overstates coverage on
        # historical data. Reported separately rather than corrected, because a row cannot now be re-read: on
        # the real record the two differ, 5 against 2 of 157. Judge progress by the nonzero share.
        "measured_nonzero": nonzero,
        "share_measured_nonzero": round(nonzero / len(rows), 4) if rows else 0.0,
        "by_route": by_route,
    }


def spend(root: Path, *, window_days: int = 7, now: datetime | None = None) -> dict[str, int]:
    """Tokens consumed per route inside a rolling window.

    A weekly allowance must not be spent by last month's work, so the window is rolling rather than a calendar
    period: a route that ran hot a fortnight ago is not still paying for it today.
    """
    cutoff = (now or datetime.now(UTC)).timestamp() - window_days * 86400
    totals: dict[str, int] = {}
    for row in outcomes(root):
        if row.tokens is None:
            continue  # unmeasured, not free: `not row.tokens` also skipped a genuine zero
        try:
            when = datetime.strptime(row.at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC).timestamp()
        except (ValueError, TypeError):
            continue  # an unparseable timestamp must not silently count as recent
        if when >= cutoff:
            totals[row.route_id] = totals.get(row.route_id, 0) + int(row.tokens)
    return totals


def over_budget(root: Path, table: Table, *, now: datetime | None = None) -> set[str]:
    """Routes that have spent their declared allowance for the week.

    Only a route that declares one can exceed one. Claude and codex seats are billed differently and declare
    nothing, so they are never dropped for spend — guessing an allowance for them would be a number the engine
    does not have, and dropping a route on a guess is how you end up with no route at all.
    """
    spent = spend(root, now=now)
    return {
        route.id for route in table.routes.values()
        if route.token_budget and spent.get(route.id, 0) >= route.token_budget
    }


def permitted_after(
    table: Table, rows: list[Outcome], tier: str, *, exclude: set[str], root: Path | None = None
) -> list[Route]:
    """The routes still worth trying at this tier once some agents are known not to run here, cheapest first.

    `choose` answers "which one route", which is the wrong question when the answer turns out to be unrunnable:
    the caller then needs the rest of the tier in the order it would have taken them. Excluding by AGENT rather
    than by route id matters — two routes can share one agent (the same CLI at two models), and if that CLI is
    missing then neither of them can run, so offering the sibling would only fail again one line later.

    Cooling routes are dropped when `root` is given, on the same reasoning `choose` uses: a route sitting out a
    vendor limit cannot do the work now, and handing it a task that a usage limit will refuse is not a fallback.
    """
    cooling: set[str] = set()
    if root is not None:
        usable = {r.id for r in availability.usable_routes(root, table.permitted(tier))}
        cooling = {r.id for r in table.permitted(tier)} - usable
    ordered = sorted(
        (r for r in table.permitted(tier) if r.agent not in exclude and r.id not in cooling),
        key=lambda r: (r.relative_cost, r.id),
    )
    return ordered


def report(root: Path, table: Table | None = None) -> list[dict[str, Any]]:
    """Every tier, what it would choose now, and why. This is what `pravrudhi routing` prints."""
    t = table or load_table()
    rows = outcomes(root)
    out: list[dict[str, Any]] = []
    for tier in t.declared or {r: () for r in ("mechanical", "standard", "design", "critical")}:
        try:
            c = choose(t, rows, tier, root=root)
        except RoutingError as e:
            out.append({"tier": tier, "error": str(e)})
            continue
        out.append({
            "tier": tier,
            "route": c.route.id,
            "agent": c.route.agent,
            "model": c.route.model,
            "relative_cost": c.route.relative_cost,
            "reason": c.reason,
            "records": [asdict(r) for r in c.records],
        })
    return out
