"""`pravrudhi watch --systemd-timer` end to end: the flag has to actually reach `watchdog.check` as the boolean
`systemd_timer_active` resolved, not just exist as an option nobody wires through. 2026-09-13: Studio's own
heartbeat timer was stopped for six hours and nothing surfaced it because `pravrudhi-watch.service` was pointed
at roots that were never the live loops -- this is the piece that, once that service is pointed correctly and
passes its own loop's timer unit here, closes the gap directly instead of waiting for staleness to accumulate.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from pravrudhi.application import watchdog
from pravrudhi.cli.app import app

runner = CliRunner()


def test_an_inactive_named_timer_is_reported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(watchdog, "systemd_timer_active", lambda unit: False)

    r = runner.invoke(
        app, ["watch", "--root", str(tmp_path), "--systemd-timer", "pravrudhi-heartbeat.timer", "--json"],
    )

    assert r.exit_code == 0, r.output
    findings = json.loads(r.output)["findings"]
    assert any(f["kind"] == "heartbeat_timer_inactive" for f in findings)


def test_an_active_named_timer_is_not_reported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(watchdog, "systemd_timer_active", lambda unit: True)

    r = runner.invoke(
        app, ["watch", "--root", str(tmp_path), "--systemd-timer", "pravrudhi-heartbeat.timer", "--json"],
    )

    assert r.exit_code == 0, r.output
    findings = json.loads(r.output)["findings"]
    assert not any(f["kind"] == "heartbeat_timer_inactive" for f in findings)


def test_no_systemd_timer_option_means_the_check_is_skipped_entirely(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The default invocation (no --systemd-timer) must not shell out to systemctl at all -- most callers of
    `pravrudhi watch` (a fresh clone, a non-systemd host) have no timer unit to name."""
    called = False

    def _fail(unit: str) -> bool | None:
        nonlocal called
        called = True
        return None

    monkeypatch.setattr(watchdog, "systemd_timer_active", _fail)

    r = runner.invoke(app, ["watch", "--root", str(tmp_path), "--json"])

    assert r.exit_code == 0, r.output
    assert called is False
