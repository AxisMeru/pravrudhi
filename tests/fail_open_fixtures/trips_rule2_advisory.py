"""Fixture for RULE 2, which is ADVISORY: it reports and never fails the build.

Both functions below are matched identically by the rule, and only one of them is a bug. That is the whole
reason rule 2 cannot block a merge -- measured ~85-90% false positives unscoped and ~30% scoped to scorer
paths over this repo.
"""

from __future__ import annotations

from typing import Any


def a_real_fail_open(row: dict[str, Any]) -> float:
    """The bug: an unscored element's probability becomes a confident 0.0 -- "not established"."""
    return float(row.get("p_established") or 0.0)


def a_correct_zero_when_absent(usage: dict[str, Any]) -> int:
    """Not a bug: a usage record with no token count really did spend zero tokens. This is the shape that
    makes up most of rule 2's matches (`src/pravrudhi/agents/cli_agents.py:108`, and ~40 other files)."""
    return int(usage.get("total_tokens") or 0)
