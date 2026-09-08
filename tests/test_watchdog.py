"""What to look at when everything reports success.

Seven things reported success while doing nothing on 2026-09-08: an hourly heartbeat over five hours of rejected
dispatches, six green doctor checks, five green parity rows checking nothing, a wave verdict of "no change
produced" while the work sat in the wrong tree, eight hours of "running the remedy" that ran nothing, a sentence
asserting a deficit that did not exist, and a night reporting `closed` after spending 0.00 GPU-hours.

None were lies in the code's own terms. Each was an accurate green over a mechanism that was correct and idle,
and every one was found by a human noticing. These checks are those noticings, written down: they look for the
SHAPE of a stall — the same choice repeated, a night that cost nothing, a cheap route down while a dear one
works — rather than for an error, because in every case there was no error to find.
"""

from __future__ import annotations

import json
from pathlib import Path

from pravrudhi.application import watchdog


def _beats(root: Path, rows: list[dict[str, object]]) -> None:
    path = root / ".pravrudhi" / "heartbeat.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")


def test_a_loop_making_the_same_choice_every_beat_is_a_finding(tmp_path: Path) -> None:
    _beats(tmp_path, [
        {"at": f"2026-09-08T0{i}:00:00Z", "chose": {"check": "pools"}, "drive": "continuity",
         "reason": "proposing the remedy for the failing 'pools' continuity check (not run here): seal a pool"}
        for i in range(6)
    ])
    findings = watchdog.check(tmp_path)
    stuck = [f for f in findings if f.kind == "loop_repeating"]
    assert stuck, [f.kind for f in findings]
    assert "pools" in stuck[0].detail
    assert stuck[0].severity == "high"


def test_a_loop_doing_different_things_is_not_a_finding(tmp_path: Path) -> None:
    _beats(tmp_path, [
        {"at": "2026-09-08T01:00:00Z", "chose": {"check": "pools"}, "drive": "continuity", "reason": "a"},
        {"at": "2026-09-08T02:00:00Z", "chose": {"request": "r-1"}, "drive": "obligations", "reason": "b"},
        {"at": "2026-09-08T03:00:00Z", "chose": {"check": "docker"}, "drive": "continuity", "reason": "c"},
        {"at": "2026-09-08T04:00:00Z", "chose": {"request": "r-2"}, "drive": "obligations", "reason": "d"},
        {"at": "2026-09-08T05:00:00Z", "chose": {"drive": "freshness"}, "drive": "freshness", "reason": "e"},
        {"at": "2026-09-08T06:00:00Z", "chose": {"request": "r-3"}, "drive": "obligations", "reason": "f"},
    ])
    assert not [f for f in watchdog.check(tmp_path) if f.kind == "loop_repeating"]


def test_a_night_that_cost_nothing_is_a_finding(tmp_path: Path) -> None:
    """Night 17 closed with `status: closed`, 0.00 of 3.0 GPU-hours and no outcomes, and read as a success."""
    ledger = tmp_path / "research" / "ledger.jsonl"
    ledger.parent.mkdir(parents=True)
    ledger.write_text(json.dumps({
        "kind": "audit", "actor": "kernel", "night": 17,
        "payload": {"kind": "night_end", "spent_gpu_h": 0.0, "budget_gpu_h": 3.0, "outcomes": {}},
    }) + "\n")
    findings = watchdog.check(tmp_path)
    empty = [f for f in findings if f.kind == "night_spent_nothing"]
    assert empty, [f.kind for f in findings]
    assert "17" in empty[0].detail


def test_a_night_that_did_work_is_not_a_finding(tmp_path: Path) -> None:
    ledger = tmp_path / "research" / "ledger.jsonl"
    ledger.parent.mkdir(parents=True)
    ledger.write_text(json.dumps({
        "kind": "audit", "actor": "kernel", "night": 18,
        "payload": {"kind": "night_end", "spent_gpu_h": 0.892, "budget_gpu_h": 3.0,
                    "outcomes": {"c-0189": "pruned"}},
    }) + "\n")
    assert not [f for f in watchdog.check(tmp_path) if f.kind == "night_spent_nothing"]


def test_findings_render_for_a_phone(tmp_path: Path) -> None:
    """The whole point is a message the operator can act on from wherever they are."""
    _beats(tmp_path, [
        {"at": f"2026-09-08T0{i}:00:00Z", "chose": {"check": "pools"}, "drive": "continuity", "reason": "x"}
        for i in range(6)
    ])
    text = watchdog.render(watchdog.check(tmp_path))
    assert text and len(text) < 3500, "a phone message, not a report"
    assert "pools" in text


def test_a_healthy_workspace_says_so_briefly(tmp_path: Path) -> None:
    assert watchdog.check(tmp_path) == []
    assert "nothing" in watchdog.render([]).lower()


def test_a_workspace_with_no_records_says_so_rather_than_healthy(tmp_path: Path) -> None:
    """A clone has no ledger and no beat log, because both are gitignored.

    The cloud routine that runs this check works from exactly such a clone. Reporting "nothing stalled" there
    would be the precise failure this module exists to catch: a green answer from a check that could not see
    anything. It has to say it cannot see rather than that all is well.
    """
    assert watchdog.blind(tmp_path), "an empty workspace cannot be judged"
    text = watchdog.render(watchdog.check(tmp_path), root=tmp_path)
    assert "cannot see" in text.lower(), text
    assert "nothing stalled" not in text.lower()


def test_a_workspace_with_records_is_not_blind(tmp_path: Path) -> None:
    _beats(tmp_path, [{"at": "2026-09-08T01:00:00Z", "chose": {"a": "1"}, "reason": "x"}])
    assert not watchdog.blind(tmp_path)


def test_the_same_findings_are_only_announced_once(tmp_path: Path) -> None:
    """A watchdog that repeats itself gets muted, and a muted watchdog is worse than none.

    It sent the operator the identical line — one cooling route — every thirty minutes. Nothing had changed
    between runs, so nothing needed saying: the first message was the alert and every one after it was noise
    training the reader to ignore the channel.
    """
    findings = [watchdog.Finding(kind="cheap_seat_down", severity="medium", detail="flash is cooling")]

    assert watchdog.worth_announcing(tmp_path, findings), "the first time is the alert"
    assert not watchdog.worth_announcing(tmp_path, findings), "the second time is noise"
    assert not watchdog.worth_announcing(tmp_path, findings)


def test_a_changed_finding_is_announced_again(tmp_path: Path) -> None:
    first = [watchdog.Finding(kind="cheap_seat_down", severity="medium", detail="flash is cooling")]
    second = [watchdog.Finding(kind="loop_repeating", severity="high", detail="the loop has not moved")]
    assert watchdog.worth_announcing(tmp_path, first)
    assert watchdog.worth_announcing(tmp_path, second), "a different problem is a different alert"


def test_recovery_is_announced_once_and_then_silence(tmp_path: Path) -> None:
    """Going quiet after a problem clears leaves the reader believing it is still broken."""
    findings = [watchdog.Finding(kind="cheap_seat_down", severity="medium", detail="flash is cooling")]
    assert watchdog.worth_announcing(tmp_path, findings)
    assert watchdog.worth_announcing(tmp_path, []), "the all-clear is worth one message"
    assert not watchdog.worth_announcing(tmp_path, []), "and only one"
