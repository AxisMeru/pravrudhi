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

from pathlib import Path

from pravrudhi.application import heartbeat


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
