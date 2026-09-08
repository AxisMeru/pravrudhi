"""A reset days away must not be read as one later today.

Alibaba's Lite Plan exhausts a ONE-WEEK quota, and says so precisely: "The quota will reset at 09-14 16:14:00
UTC". `reset_at` only understood a bare clock time, so it returned None and the engine fell back to its guess —
a short cooldown, after which it retried a seat that had six days left to run, took another 429, and cooled
again. Every thirty minutes, for six days, against a quota that could not have moved.

Worse than the wasted calls: a route in cooldown is dropped from scoring, so the engine would have kept
rediscovering the outage instead of routing around it once.
"""

from __future__ import annotations

from datetime import UTC, datetime

from pravrudhi.application import availability

_MSG = "Your token-plan 1-week quota has been exhausted. The quota will reset at 09-14 16:14:00 UTC."


def test_a_dated_reset_is_read_as_that_date() -> None:
    now = datetime(2026, 9, 8, 17, 0, tzinfo=UTC)
    when = availability.reset_at(_MSG, now=now)
    assert when is not None, "the vendor stated the answer; guessing in its presence is the bug"
    assert (when.month, when.day, when.hour) == (9, 14, 16), when
    assert (when - now).days >= 5, when


def test_a_bare_clock_time_still_means_today_or_tomorrow() -> None:
    """The existing behaviour must survive: most vendors state a time, not a date."""
    now = datetime(2026, 9, 8, 15, 2, tzinfo=UTC)
    when = availability.reset_at("try again at 15:51 UTC", now=now, tz=UTC)
    assert when is not None and (when.hour, when.minute) == (15, 51)
    assert when.day == 8, "later the same day, not tomorrow"


def test_text_with_no_stated_time_is_still_none() -> None:
    assert availability.reset_at("rate limited, try later") is None
