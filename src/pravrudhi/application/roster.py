"""Which model can be used right now, how good it has been, and when a spent one comes back.

The engine had two half-views. `/api/agents` said which command-line tools are installed; `/api/agents/cooldowns`
said who was sitting out a vendor usage limit. Neither said what a route costs, how well it had actually done, or
which tiers it serves, and nothing joined them, so the question an operator asks each morning could only be
answered by reading three files.

It cost something real on 2026-09-07. The strongest model returned from its limit at 15:51 and the engine went on
avoiding it until 16:02, because the fixed cooldown was a guess and nobody was looking at the one number that
would have shown the guess was wrong. Managing a fleet of models is a feature of this product, not an
administrative chore beside it.

So this is the join: every route the table declares, with its cost, its measured record, the tiers it serves, and
whether it can be dispatched to at this moment or is waiting, with the moment it returns. Usable routes come
first, because the reason to open this is to decide what to run next.

It reports and does not decide. `routing.choose` remains the only thing that picks a route, and it reads the same
two sources this does.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pravrudhi.application import availability, routing


@dataclass(frozen=True)
class Seat:
    """One route, as it stands at a moment in time."""

    id: str
    agent: str
    model: str
    relative_cost: float
    tiers: tuple[str, ...]
    sentinel: bool
    trials: int
    successes: int
    usable: bool
    returns_at: str | None
    note: str

    @property
    def success_rate(self) -> float | None:
        """None rather than zero when nothing has been measured: a route with no trials has no rate, and
        reporting zero would read as a route that always fails."""
        return self.successes / self.trials if self.trials else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "agent": self.agent, "model": self.model,
            "relative_cost": self.relative_cost, "tiers": list(self.tiers), "sentinel": self.sentinel,
            "trials": self.trials, "successes": self.successes,
            "success_rate": self.success_rate, "usable": self.usable,
            "returns_at": self.returns_at, "note": self.note,
        }


def roster(root: Path, *, now: datetime | None = None, table: routing.Table | None = None) -> list[Seat]:
    """Every route, usable ones first, then by cost.

    A route is unusable when the agent behind it is inside a cooldown. Several routes can share one agent — astra
    and any other codex model do — so one vendor limit grounds all of them together, which is exactly what
    happened and is worth seeing at a glance rather than deducing.
    """
    when = now or datetime.now(UTC)
    table = table or routing.load_table()
    cooling = availability.cooling(root, now=when)

    try:
        rows = routing.outcomes(root)
    except (OSError, ValueError):
        rows = []  # a fresh install has no history and must still answer this question

    tallies: dict[str, tuple[int, int]] = {}
    for outcome in rows:
        if getattr(outcome, "limited", False):
            continue  # a usage limit says nothing about a route's quality
        trials, successes = tallies.get(outcome.route_id, (0, 0))
        tallies[outcome.route_id] = (trials + 1, successes + (1 if outcome.accepted else 0))

    seats: list[Seat] = []
    for route in table.routes.values():
        trials, successes = tallies.get(route.id, (0, 0))
        returns_at = cooling.get(route.agent)
        seats.append(
            Seat(
                id=route.id, agent=route.agent, model=route.model,
                relative_cost=route.relative_cost, tiers=tuple(route.tiers), sentinel=route.sentinel,
                trials=trials, successes=successes,
                usable=returns_at is None, returns_at=returns_at, note=route.note,
            )
        )

    seats.sort(key=lambda s: (not s.usable, s.relative_cost, s.id))
    return seats


__all__ = ["Seat", "roster"]
