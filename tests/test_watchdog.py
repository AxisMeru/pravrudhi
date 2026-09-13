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

import pytest

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


def test_an_install_behind_the_source_is_reported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The gap that cost a week's quota.

    Every guard against runaway spend was written in the development checkout and none of it was in release
    0.4.0, which is what the end-user install ran. The updater said "already at 0.4.0" every half hour and was
    telling the truth: the version had never been bumped, so the fixes were finished and unshipped. Nothing
    measured the distance between what is written and what is installed, so nothing could mention it.
    """
    releases = tmp_path / ".pravrudhi" / "releases"
    (releases / "0.4.0").mkdir(parents=True)
    (releases / "current").symlink_to(releases / "0.4.0")
    monkeypatch.setattr(watchdog, "_packaged_version", lambda: "0.4.2")

    found = watchdog._stale_install(tmp_path)

    assert len(found) == 1 and found[0].severity == "high"
    assert "0.4.0" in found[0].detail and "0.4.2" in found[0].detail


def test_an_install_level_with_the_source_is_not_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    releases = tmp_path / ".pravrudhi" / "releases"
    (releases / "0.4.2").mkdir(parents=True)
    (releases / "current").symlink_to(releases / "0.4.2")
    monkeypatch.setattr(watchdog, "_packaged_version", lambda: "0.4.2")

    assert watchdog._stale_install(tmp_path) == []


def test_a_source_checkout_has_no_install_to_be_behind(tmp_path: Path) -> None:
    """The development checkout runs from source and has no `releases/current`; it is never stale."""
    assert watchdog._stale_install(tmp_path) == []


# 2026-09-13: Studio's own heartbeat timer was stopped for infra work at 11:42 and not restarted until 18:26 --
# nearly six hours during which `pravrudhi-watch.service` (pointed, separately, at the wrong roots entirely)
# would still have found nothing even if it had been aimed correctly, because `_silent_loop` only fires once
# `heartbeat.jsonl` has gone stale past `scheduler_max_stale_s` (2 hours) -- a real gap, but a slower one than
# checking whether the timer unit itself is active. This is that faster, more direct check: a caller who already
# knows a root's timer unit and has already asked systemd whether it is active passes both in, and `check()`
# reports it immediately rather than waiting for staleness to accumulate.
def test_an_inactive_timer_is_a_high_severity_finding(tmp_path: Path) -> None:
    found = watchdog.check(tmp_path, timer_unit="pravrudhi-heartbeat.timer", timer_active=False)

    matches = [f for f in found if f.kind == "heartbeat_timer_inactive"]
    assert len(matches) == 1 and matches[0].severity == "high"
    assert "pravrudhi-heartbeat.timer" in matches[0].detail


def test_an_active_timer_is_not_a_finding(tmp_path: Path) -> None:
    found = watchdog.check(tmp_path, timer_unit="pravrudhi-heartbeat.timer", timer_active=True)

    assert not any(f.kind == "heartbeat_timer_inactive" for f in found)


def test_no_timer_unit_given_means_no_opinion(tmp_path: Path) -> None:
    """A caller that does not know its timer's unit name (or is checking a root with no systemd timer at all,
    e.g. a fresh clone) must not be told anything is wrong -- an inconclusive check is not a finding."""
    found = watchdog.check(tmp_path)

    assert not any(f.kind == "heartbeat_timer_inactive" for f in found)


def test_timer_active_none_means_the_check_itself_could_not_run_and_says_nothing(tmp_path: Path) -> None:
    """`timer_active=None` is the honest result of a query that failed on its own terms (no systemd user bus
    reachable, `systemctl` missing) -- reading that as 'inactive' would cry wolf on every host that cannot run
    the check at all, which is worse than not running it."""
    found = watchdog.check(tmp_path, timer_unit="pravrudhi-heartbeat.timer", timer_active=None)

    assert not any(f.kind == "heartbeat_timer_inactive" for f in found)


class TestSystemdTimerActive:
    """`systemd_timer_active` is the one impure edge of this feature -- everything else in this module reads
    only files under `root`. Kept separate and tiny so `check()` itself stays a pure function of its inputs."""

    def test_reports_active(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import subprocess

        from pravrudhi.application.watchdog import systemd_timer_active

        def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            assert cmd[-2:] == ["is-active", "pravrudhi-heartbeat.timer"]
            return subprocess.CompletedProcess(cmd, 0, stdout="active\n", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)
        assert systemd_timer_active("pravrudhi-heartbeat.timer") is True

    def test_reports_inactive(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import subprocess

        from pravrudhi.application.watchdog import systemd_timer_active

        def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(cmd, 3, stdout="inactive\n", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)
        assert systemd_timer_active("pravrudhi-heartbeat.timer") is False

    def test_a_failed_query_is_none_not_inactive(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """No user bus reachable (this harness's own known failure mode without XDG_RUNTIME_DIR/
        DBUS_SESSION_BUS_ADDRESS exported) or no `systemctl` binary at all -- both are 'cannot tell', not
        'the timer stopped'."""
        import subprocess

        from pravrudhi.application.watchdog import systemd_timer_active

        def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            raise OSError("systemctl not found")

        monkeypatch.setattr(subprocess, "run", fake_run)
        assert systemd_timer_active("pravrudhi-heartbeat.timer") is None
