"""Fixture: every shape RULE 3 of `scripts/check_fail_open_defaults.py` must catch -- a statistic falling
back to a numeric literal when its denominator is empty or zero.

Deliberately wrong code; see `trips_rule1.py`'s header for why it is never imported or executed.
"""

from __future__ import annotations

import math


def ifexp_form(scores: list[float]) -> float:
    """The commonest shape: `X if <no data> else <number>`, named by its assignment target."""
    mean = sum(scores) / len(scores) if scores else 0.0
    return mean


def sd_from_one_point(xs: list[float], mean: float) -> float:
    """A sample sd over fewer than two points is undefined, not 0.0 -- and 0.0 reads as zero variance."""
    sd = math.sqrt(sum((x - mean) ** 2 for x in xs) / (len(xs) - 1)) if len(xs) > 1 else 0.0
    return sd


def wilson_interval(k: int, n: int) -> tuple[float, float]:
    """The if-guarded-return shape, named by its enclosing function: a fabricated confidence interval."""
    if n <= 0:
        return (0.0, 0.0)
    p = k / n
    return (max(0.0, p - 0.1), min(1.0, p + 0.1))


def tuple_target_interval(xs: list[float]) -> tuple[float, float]:
    """The unpacked-target shape: the statistic is named only in `lo, hi`."""
    n = len(xs)
    lo, hi = (min(xs), max(xs)) if n else (0.0, 0.0)
    return lo, hi


def keyword_argument_form(drifts: list[float]) -> dict[str, float]:
    """The keyword-name shape: `median_abs_dp` is named only as a call keyword."""
    return dict(median_abs_dp=sorted(drifts)[len(drifts) // 2] if drifts else 0.0)


def false_prove_rate(fooled: int, n_negative: int) -> float:
    """The most dangerous polarity: a 0.0 rate from no data reads as a clean safety headline."""
    rate = fooled / n_negative if n_negative else 0.0
    return rate
