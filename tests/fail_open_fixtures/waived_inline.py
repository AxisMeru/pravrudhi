"""Fixture for the inline waiver: `# fail-open-ok: <reason>`, the escape hatch for new code.

This is the distinction the AST cannot make, which is why the guard FLAGS a `False` default and demands a
visible waiver rather than silently allowing it: a `False` default is correct where False means REFUSE/FAIL,
and is the bug where False means "no problem found". Both are below.
"""

from __future__ import annotations

from typing import Any


def false_means_refuse(record: dict[str, Any]) -> tuple[bool, str]:
    """Reduced from `second_judge_positive_control.check_record` (PR #46/#54, line 269): a record with no
    `available` key is treated as a FAILED check, so the default fails closed. Correct."""
    # fail-open-ok: a record with no `available` key is a check that did not pass -- False fails closed here
    if not record.get("available", False):
        return False, f"last check failed: {record.get('reasons')}"
    return True, "ok"


def api_reply_ok(data: dict[str, Any]) -> bool:
    """Reduced from `src/pravrudhi/application/reach.py:224`: a reply with no `ok` field did not succeed."""
    return bool(data.get("ok", False))  # fail-open-ok: a reply with no `ok` field is a failed call

def waiver_needs_a_reason(row: dict[str, Any]) -> float:
    """A bare marker with nothing after the colon does NOT waive anything -- the guard still reports this
    line, so a waiver cannot be added without saying why."""
    return row.get("confidence", 0.0)  # fail-open-ok:
