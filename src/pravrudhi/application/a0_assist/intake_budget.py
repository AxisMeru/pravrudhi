"""A wall-clock budget for the intake (pravrudhi 0.6.16): inside `deadline(seconds)` the intake's `tick()` raises `IntakeTimeout`
once the time is up. The check is COOPERATIVE (between stages and per paragraph or line, never inside one pattern match), so it
bounds the number of steps taken, not a single step: each pattern must still be linear on its own. It changes no result: it
only stops a run early. It is a separate module so `intake.py` itself keeps no clock."""

from __future__ import annotations

import contextlib
import contextvars
import time
from collections.abc import Iterator

__all__ = ["IntakeTimeout", "deadline", "tick"]


class IntakeTimeout(Exception):
    """The intake ran past its wall-clock budget; nothing is produced."""


_DEADLINE: contextvars.ContextVar[float | None] = contextvars.ContextVar("intake_deadline", default=None)


@contextlib.contextmanager
def deadline(seconds: float | None) -> Iterator[None]:
    """Run the intake under a budget of `seconds` (None or 0 sets none)."""
    token = _DEADLINE.set(time.monotonic() + seconds if seconds else None)
    try:
        yield
    finally:
        _DEADLINE.reset(token)


def tick() -> None:
    limit = _DEADLINE.get()
    if limit is not None and time.monotonic() > limit:
        raise IntakeTimeout
