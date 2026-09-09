"""A criterion the loop cannot finish must stop being retried at full price.

On 2026-09-08 the studio heartbeat dispatched request r-5795501a criterion 7 nine times in one day — five
accepted, four rejected — and the criterion was still unmet at the end of it. Every hour an agent was paid to
produce a proposal, the adversarial reviewer refused it, and the next beat dispatched the identical task again.
Nothing recorded that the attempt had already been made, so nothing could notice it was being made again.

The Lite Plan seat hit its usage limit the same afternoon.

A retry is right; an unbounded identical retry is a standing order to spend. After a few attempts that do not
move the criterion, the loop records it as stalled, says so where the operator will see it, and spends the beat
on something else instead.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from pravrudhi.application import heartbeat
from pravrudhi.application.requests import Criterion, add_criteria, capture, next_unmet


def test_attempts_are_counted_per_criterion(tmp_path: Path) -> None:
    assert heartbeat.attempts(tmp_path, "r-1", 7) == 0
    heartbeat.record_attempt(tmp_path, "r-1", 7)
    heartbeat.record_attempt(tmp_path, "r-1", 7)
    assert heartbeat.attempts(tmp_path, "r-1", 7) == 2
    assert heartbeat.attempts(tmp_path, "r-1", 8) == 0, "a different criterion has its own budget"
    assert heartbeat.attempts(tmp_path, "r-2", 7) == 0, "a different request has its own budget"


def test_progress_clears_the_count(tmp_path: Path) -> None:
    """A criterion that moves has not stalled, whatever it cost to get there."""
    heartbeat.record_attempt(tmp_path, "r-1", 7)
    heartbeat.record_attempt(tmp_path, "r-1", 7)
    heartbeat.clear_attempts(tmp_path, "r-1", 7)
    assert heartbeat.attempts(tmp_path, "r-1", 7) == 0


def test_the_budget_is_exhausted_only_after_repeated_failure(tmp_path: Path) -> None:
    for _ in range(heartbeat.MAX_CRITERION_ATTEMPTS - 1):
        heartbeat.record_attempt(tmp_path, "r-1", 7)
    assert not heartbeat.stalled(tmp_path, "r-1", 7), "one short of the budget is still worth trying"
    heartbeat.record_attempt(tmp_path, "r-1", 7)
    assert heartbeat.stalled(tmp_path, "r-1", 7)


def test_a_stalled_criterion_survives_a_restart(tmp_path: Path) -> None:
    """The count lives on disk: an hourly loop that forgot on restart would never reach any budget."""
    for _ in range(heartbeat.MAX_CRITERION_ATTEMPTS):
        heartbeat.record_attempt(tmp_path, "r-1", 7)
    assert heartbeat.stalled(tmp_path, "r-1", 7)
    assert (tmp_path / heartbeat._ATTEMPTS_FILE).is_file()


class TestTheBudgetMovesTheLoopOn:
    """Spending the budget must also release the choice.

    The budget stopped the paying and left selection pinned to the thing it had just given up on. On 2026-09-09
    both engines reported the same criterion on six consecutive beats — `r-5795501a` criterion 7 in the studio,
    `r-3981d7e0` criterion 1 in the release install — each with the reason "has stalled after 3 attempts;
    leaving it for the operator". Correct about not paying, and still the only thing either loop could see, so
    neither did anything else for days while 28 captured asks waited behind it.
    """

    def test_a_stalled_criterion_is_not_chosen_again(self, tmp_path: Path) -> None:
        old_at = (datetime.now(UTC) - timedelta(days=5)).isoformat().replace("+00:00", "Z")
        parked = capture(tmp_path, "the ask the loop could not finish", asked_at=old_at)
        add_criteria(tmp_path, parked.id, [Criterion(text="the criterion it gave up on", source="operator")])
        fresh = capture(tmp_path, "an ask it has not tried yet")
        add_criteria(tmp_path, fresh.id, [Criterion(text="work worth a beat", source="operator")])

        picked = next_unmet(tmp_path)
        assert picked is not None and picked[0].id == parked.id, "the oldest ask comes first while it is live"

        for _ in range(heartbeat.MAX_CRITERION_ATTEMPTS):
            heartbeat.record_attempt(tmp_path, parked.id, 0)

        picked = next_unmet(tmp_path)
        assert picked is not None, "a parked criterion must not hide the work behind it"
        assert picked[0].id == fresh.id, "the beat goes to what the loop can still move"
        assert picked[1].text == "work worth a beat"

    def test_nothing_is_offered_when_every_criterion_is_parked(self, tmp_path: Path) -> None:
        """`None` is what lets the beat fall through to another drive, rather than re-choosing a dead end."""
        parked = capture(tmp_path, "the only ask, and it is stuck")
        add_criteria(tmp_path, parked.id, [Criterion(text="stuck work", source="operator")])
        for _ in range(heartbeat.MAX_CRITERION_ATTEMPTS):
            heartbeat.record_attempt(tmp_path, parked.id, 0)
        assert next_unmet(tmp_path) is None

    def test_progress_puts_a_parked_criterion_back_in_play(self, tmp_path: Path) -> None:
        """The budget is spent per attempt, not per criterion for ever: clearing it restores the choice."""
        parked = capture(tmp_path, "stuck, then unstuck")
        add_criteria(tmp_path, parked.id, [Criterion(text="stuck work", source="operator")])
        for _ in range(heartbeat.MAX_CRITERION_ATTEMPTS):
            heartbeat.record_attempt(tmp_path, parked.id, 0)
        assert next_unmet(tmp_path) is None
        heartbeat.clear_attempts(tmp_path, parked.id, 0)
        picked = next_unmet(tmp_path)
        assert picked is not None and picked[0].id == parked.id
