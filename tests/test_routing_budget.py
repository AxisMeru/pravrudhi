"""A route that has spent its allowance stops being chosen, before the vendor stops it.

The router has always chosen on two things: relative cost and measured success rate. Neither is consumption. So
it could pick the cheapest-per-token seat all day and still exhaust a weekly quota inside one, which is exactly
what happened on 2026-09-08 — the Lite Plan's one-week allowance went in a day, and the first anyone knew was a
429 telling us to come back in six days.

Cost ratio answers "which is cheaper per unit of work". A budget answers "how much of this is left". A router
with only the first will spend the second, every time, and be surprised.

The budget is deliberately soft: it drops a route from scoring the way a cooling route is dropped, so work moves
to another seat rather than stopping. Running out of cheap capacity should make the engine dearer, not idle.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from pravrudhi.application import routing


def _outcome(route_id: str, *, tokens: int, at: datetime) -> routing.Outcome:
    return routing.Outcome(
        tier="mechanical", route_id=route_id, task_id="t", accepted=True, wall_s=1.0,
        at=at.strftime("%Y-%m-%dT%H:%M:%SZ"), tokens=tokens,
    )


def test_an_outcome_carries_what_it_spent(tmp_path: Path) -> None:
    """Without this the ledger of dispatches cannot answer the only question that matters for a quota."""
    now = datetime.now(UTC)
    routing.record_outcome(tmp_path, _outcome("qwen-lite-flash", tokens=1234, at=now))
    rows = routing.outcomes(tmp_path)
    assert rows and rows[-1].tokens == 1234


def test_spend_is_counted_per_route_within_the_window(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    for _ in range(3):
        routing.record_outcome(tmp_path, _outcome("qwen-lite-flash", tokens=1000, at=now))
    routing.record_outcome(tmp_path, _outcome("sonnet", tokens=500, at=now))
    # Older than the window: a weekly allowance must not be spent by last month's work.
    routing.record_outcome(tmp_path, _outcome("qwen-lite-flash", tokens=99_000, at=now - timedelta(days=30)))

    spent = routing.spend(tmp_path, window_days=7)
    assert spent["qwen-lite-flash"] == 3000, spent
    assert spent["sonnet"] == 500, spent


def test_a_route_over_its_allowance_is_dropped_from_the_choice(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    table = routing.load_table()
    flash = table.routes["qwen-lite-flash"]
    assert flash.token_budget, "the plan seats must declare an allowance or this cannot bind"

    routing.record_outcome(tmp_path, _outcome("qwen-lite-flash", tokens=flash.token_budget + 1, at=now))
    choice = routing.choose(table, routing.outcomes(tmp_path), "mechanical", root=tmp_path)

    assert choice.route.id != "qwen-lite-flash", choice.route.id
    assert "allowance" in choice.reason or "budget" in choice.reason, choice.reason


def test_a_route_inside_its_allowance_is_still_chosen(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    table = routing.load_table()
    flash = table.routes["qwen-lite-flash"]
    routing.record_outcome(tmp_path, _outcome("qwen-lite-flash", tokens=flash.token_budget // 4, at=now))
    choice = routing.choose(table, routing.outcomes(tmp_path), "mechanical", root=tmp_path)
    assert choice.route.id == "qwen-lite-flash", choice.reason


def test_a_route_with_no_declared_allowance_is_never_dropped_for_spend(tmp_path: Path) -> None:
    """Only a seat with a stated allowance can exceed one. Claude and codex are billed differently and are not
    guessed at here — inventing a budget for them would be a number the engine does not have."""
    now = datetime.now(UTC)
    table = routing.load_table()
    assert not table.routes["sonnet"].token_budget
    routing.record_outcome(tmp_path, _outcome("sonnet", tokens=10_000_000, at=now))
    assert "sonnet" not in routing.over_budget(tmp_path, table)
