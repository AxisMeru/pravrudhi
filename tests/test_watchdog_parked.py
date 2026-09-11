"""What the loop has given up on must stay visible after the loop stops naming it.

Until 2026-09-09 a parked criterion announced itself by accident: selection kept choosing it, so `_repeating`
saw six identical beats and reported a stall. Teaching `requests.next_unmet` to skip a criterion that has spent
its attempt budget is the right fix for the loop and it removes that accident — the beats stop repeating, and
the thing nobody can finish goes quiet.

So it is reported deliberately instead, from the attempt record itself rather than inferred from the shape of
the last six beats. That is the better evidence anyway: it names the criterion and its cost directly.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
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


class TestSilentLoop:
    """A frozen heartbeat log and a busy one look identical to a check that reads only content.

    On 2026-09-10 both engines had been dead for hours -- the beat crashed on an unhandled RequestError and
    systemd left the unit failed -- while `_repeating` saw the same last twelve entries it had always seen and
    reported nothing. `svasthya._check_scheduler_fresh` had the finding right (`last heartbeat was 38230s ago`)
    and published it; `pravrudhi watch`, the surface that runs every 30 minutes and notifies, never asked.
    """

    @staticmethod
    def _log(root: Path, at: str) -> None:
        d = root / ".pravrudhi"
        d.mkdir(parents=True, exist_ok=True)
        (d / "heartbeat.jsonl").write_text(
            json.dumps({"at": at, "chose": {"drive": "obligations"}, "drive": "obligations"}) + "\n"
        )

    def test_a_beat_within_the_limit_is_not_flagged(self, tmp_path: Path) -> None:
        fresh = datetime.now(UTC) - timedelta(minutes=20)
        self._log(tmp_path, fresh.isoformat(timespec="seconds").replace("+00:00", "Z"))
        assert [f for f in watchdog.check(tmp_path) if f.kind == "loop_silent"] == []

    def test_a_loop_that_stopped_is_flagged_high_with_the_command_to_run(self, tmp_path: Path) -> None:
        dead = datetime.now(UTC) - timedelta(hours=11)
        self._log(tmp_path, dead.isoformat(timespec="seconds").replace("+00:00", "Z"))
        found = [f for f in watchdog.check(tmp_path) if f.kind == "loop_silent"]
        assert len(found) == 1
        assert found[0].severity == "high"
        assert "11.0h" in found[0].detail
        assert "systemctl --user status" in found[0].detail, "a finding a reader cannot act on is half a finding"

    def test_an_unreadable_timestamp_is_a_finding_not_a_silent_pass(self, tmp_path: Path) -> None:
        self._log(tmp_path, "not-a-time")
        found = [f for f in watchdog.check(tmp_path) if f.kind == "beat_unparsable"]
        assert len(found) == 1 and found[0].severity == "high"

    def test_a_workspace_with_no_beats_is_left_to_blind(self, tmp_path: Path) -> None:
        # An empty workspace is unobserved, not stalled; `watchdog.blind` is what says so.
        assert [f for f in watchdog.check(tmp_path) if f.kind == "loop_silent"] == []


def test_a_criterion_the_beat_stalled_on_paper_is_not_announced(tmp_path: Path) -> None:
    """The studio bot sent the same four parked criteria every digest on 2026-09-11; all four had been stalled by the
    beat itself as unbuildable (kernel path, gitignored docs) with the reason on the request. Decided is not owed."""
    from pravrudhi.application import requests

    r = capture(tmp_path, "grow the kernel", criteria=[Criterion(text="`pravrudhi_kernel/x.py` gains a term", source="operator")])
    for _ in range(heartbeat.MAX_CRITERION_ATTEMPTS):
        heartbeat.record_attempt(tmp_path, r.id, 0)
    requests.note(tmp_path, r.id, "criterion 0 unbuildable: names pravrudhi_kernel/x.py: a protected prefix is an ADR")
    assert watchdog._parked_criteria(tmp_path) == []

    other = capture(tmp_path, "do the thing", criteria=[Criterion(text="`src/x.py` sets VALUE = 2", source="operator")])
    for _ in range(heartbeat.MAX_CRITERION_ATTEMPTS):
        heartbeat.record_attempt(tmp_path, other.id, 0)
    assert [f.kind for f in watchdog._parked_criteria(tmp_path)] == ["parked_criterion"], "a real parking still is"
