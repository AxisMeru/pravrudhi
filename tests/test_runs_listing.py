"""A run happening now must be visible however it was started, and one that died must not read as running.

The run manager knows only what the app itself launched, and every night in this project has been started from
the command line, so the interface showed nothing while the GPU was busy. Detecting an open night from the ledger
fixed that and immediately introduced the opposite fault: a night abandoned months ago, with no closing row, read
as running for ever.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pravrudhi.api.runs import _in_flight


def _ledger(root: Path, rows: list[dict]) -> None:
    (root / "research").mkdir(parents=True, exist_ok=True)
    (root / "research" / "ledger.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")


def _row(night: int, kind: str, when: datetime, track: str = "lora", **payload) -> dict:
    # The kernel validates every row it reads, so a fixture must be a complete one: the timestamp is pinned to
    # milliseconds and every field is required. Writing a partial row exercises the schema, not this function.
    return {
        "seq": night * 100 + len(kind),
        "t": when.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "epoch": 0, "night": night, "cycle": None, "kind": "audit", "actor": "kernel",
        "candidate_id": None, "surface": None, "bucket": None, "provenance": "anumana",
        "kernel_release": "0.1.0", "prev_hash": "0" * 64, "this_hash": "0" * 64,
        "payload": {"kind": kind, "track": track, **payload},
    }


def test_a_night_started_outside_the_app_is_reported_as_running(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    _ledger(tmp_path, [_row(16, "night_start", now - timedelta(minutes=3), selection_policy="efe")])
    rows = _in_flight(tmp_path)
    assert [r["id"] for r in rows] == ["night-16-lora"]
    assert rows[0]["status"] == "running"
    assert rows[0]["request"]["policy"] == "efe"


def test_a_closed_night_is_not_reported_as_running(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    _ledger(tmp_path, [
        _row(16, "night_start", now - timedelta(minutes=9)),
        _row(16, "night_end", now - timedelta(minutes=2)),
    ])
    assert _in_flight(tmp_path) == []


def test_a_night_that_died_without_closing_stops_being_running(tmp_path: Path) -> None:
    old = datetime.now(UTC) - timedelta(days=4)
    _ledger(tmp_path, [_row(7, "night_start", old, track="harness")])
    assert _in_flight(tmp_path) == [], "an abandoned night must not read as running for ever"


def test_an_abandoned_night_is_not_running(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    _ledger(tmp_path, [
        _row(11, "night_start", now - timedelta(minutes=30)),
        _row(11, "night_abandoned", now - timedelta(minutes=20)),
    ])
    assert _in_flight(tmp_path) == []


def test_a_workspace_with_no_ledger_has_nothing_in_flight(tmp_path: Path) -> None:
    assert _in_flight(tmp_path) == []
