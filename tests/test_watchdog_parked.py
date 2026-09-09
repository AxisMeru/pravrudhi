"""What the loop has given up on must stay visible after the loop stops naming it.

Until 2026-09-09 a parked criterion announced itself by accident: selection kept choosing it, so `_repeating`
saw six identical beats and reported a stall. Teaching `requests.next_unmet` to skip a criterion that has spent
its attempt budget is the right fix for the loop and it removes that accident — the beats stop repeating, and
the thing nobody can finish goes quiet.

So it is reported deliberately instead, from the attempt record itself rather than inferred from the shape of
the last six beats. That is the better evidence anyway: it names the criterion and its cost directly.
"""

from __future__ import annotations

from pathlib import Path

from pravrudhi.application import heartbeat, watchdog
from pravrudhi.application.requests import Criterion, Evidence, add_criteria, capture, meet


def _parked_request(root: Path, text: str = "a thing the loop could not finish") -> str:
    r = capture(root, text)
    add_criteria(root, r.id, [Criterion(text="the criterion it gave up on", source="operator")])
    for _ in range(heartbeat.MAX_CRITERION_ATTEMPTS):
        heartbeat.record_attempt(root, r.id, 0)
    return r.id


def test_a_parked_criterion_is_reported(tmp_path: Path) -> None:
    rid = _parked_request(tmp_path)
    findings = watchdog._parked_criteria(tmp_path)
    assert len(findings) == 1
    assert findings[0].kind == "parked_criterion"
    assert findings[0].severity == "high", "work the engine cannot finish unaided is the operator's to unblock"
    assert rid in findings[0].detail
    assert "the criterion it gave up on" in findings[0].detail
    assert str(heartbeat.MAX_CRITERION_ATTEMPTS) in findings[0].detail, "the cost is part of the finding"


def test_a_criterion_within_its_budget_is_not_reported(tmp_path: Path) -> None:
    r = capture(tmp_path, "still worth trying")
    add_criteria(tmp_path, r.id, [Criterion(text="work in progress", source="operator")])
    heartbeat.record_attempt(tmp_path, r.id, 0)
    assert watchdog._parked_criteria(tmp_path) == []


def test_a_criterion_that_was_met_after_parking_is_not_reported(tmp_path: Path) -> None:
    """`meet` clears the budget, so a criterion that moved is not still parked."""
    rid = _parked_request(tmp_path)
    meet(tmp_path, rid, 0, [Evidence("commit", "deadbee")])
    assert watchdog._parked_criteria(tmp_path) == []


def test_a_clean_workspace_reports_nothing(tmp_path: Path) -> None:
    assert watchdog._parked_criteria(tmp_path) == []


def test_the_check_runs_as_part_of_the_watchdog(tmp_path: Path) -> None:
    rid = _parked_request(tmp_path)
    kinds = [f.kind for f in watchdog.check(tmp_path)]
    assert "parked_criterion" in kinds, "a check nothing calls is not a check"
    assert rid in watchdog.render(watchdog.check(tmp_path))
