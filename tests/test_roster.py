"""One place that answers: which model can I use right now, how good is it, and when does a spent one come back?

The engine had two half-views. `/api/agents` said which command-line tools are installed; `/api/agents/cooldowns`
said who was sitting out a usage limit. Neither said what a route costs, how well it has actually done, or which
tiers it serves, and nothing joined them — so the question an operator actually asks each morning could only be
answered by reading three files.

That mattered on 2026-09-07, when the strongest model came back from its limit and the engine went on avoiding it
for another half hour because nobody was looking at the one number that would have said so.

The roster is that join: every route the table declares, with its cost, its measured record, the tiers it serves,
and whether it can be dispatched to at this moment or is waiting, with the time it returns.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from pravrudhi.application.roster import roster


@pytest.fixture
def _now() -> datetime:
    return datetime(2026, 9, 7, 15, 30, tzinfo=UTC)


class TestWhatItReports:
    def test_every_declared_route_appears(self, tmp_path: Path, _now: datetime) -> None:
        from pravrudhi.application.routing import load_table

        ids = {r.id for r in roster(tmp_path, now=_now)}
        assert ids == set(load_table().routes)

    def test_each_row_carries_what_a_choice_needs(self, tmp_path: Path, _now: datetime) -> None:
        row = next(r for r in roster(tmp_path, now=_now) if r.id == "astra")
        assert row.agent and row.model
        assert row.relative_cost > 0
        assert isinstance(row.tiers, tuple)
        assert row.usable is True and row.returns_at is None

    def test_a_route_with_no_record_says_so_rather_than_implying_a_rate(
        self, tmp_path: Path, _now: datetime
    ) -> None:
        """A route with no trials has no success rate, and reporting one as zero would read as failure."""
        row = next(r for r in roster(tmp_path, now=_now) if r.trials == 0)
        assert row.success_rate is None


class TestWaitingRoutes:
    def test_a_cooling_route_is_reported_unusable_with_its_return_time(
        self, tmp_path: Path, _now: datetime
    ) -> None:
        from pravrudhi.application.availability import mark_limited

        back = _now + timedelta(minutes=21)
        mark_limited(tmp_path, "codex", until=back, now=_now)

        row = next(r for r in roster(tmp_path, now=_now) if r.agent == "codex")
        assert row.usable is False
        assert row.returns_at == back.strftime("%Y-%m-%dT%H:%M:%SZ")

    def test_it_becomes_usable_again_once_that_time_passes(self, tmp_path: Path, _now: datetime) -> None:
        """The half hour lost on 2026-09-07 was exactly this transition going unnoticed."""
        from pravrudhi.application.availability import mark_limited

        mark_limited(tmp_path, "codex", until=_now + timedelta(minutes=21), now=_now)

        later = _now + timedelta(minutes=22)
        row = next(r for r in roster(tmp_path, now=later) if r.agent == "codex")
        assert row.usable is True and row.returns_at is None

    def test_one_agent_cooling_does_not_ground_the_others(self, tmp_path: Path, _now: datetime) -> None:
        from pravrudhi.application.availability import mark_limited

        mark_limited(tmp_path, "codex", until=_now + timedelta(minutes=21), now=_now)
        rows = {r.id: r for r in roster(tmp_path, now=_now)}
        assert rows["astra"].usable is False, "astra runs on codex"
        assert rows["sonnet"].usable is True


class TestItIsUsefulToLookAt:
    def test_rows_lead_with_what_can_be_used_now(self, tmp_path: Path, _now: datetime) -> None:
        from pravrudhi.application.availability import mark_limited

        mark_limited(tmp_path, "codex", until=_now + timedelta(minutes=21), now=_now)
        rows = roster(tmp_path, now=_now)
        first_unusable = next(i for i, r in enumerate(rows) if not r.usable)
        assert all(r.usable for r in rows[:first_unusable])

    def test_a_row_renders_without_a_ledger_or_any_history(self, tmp_path: Path, _now: datetime) -> None:
        """A fresh install must be able to answer this question too."""
        assert roster(tmp_path, now=_now)
