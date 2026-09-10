"""A pass-rate interval must be computed by the method its score scale admits.

ADR-REF: ADR-0038. A Wilson interval is a confidence interval for a proportion and needs a count of
successes. `int(x.sum())` supplies one for any scale: it type-checks, it stays inside `[0, n]`, and on the
`set` kind's per-item Jaccard it silently truncates and returns a number nobody can interpret. That is the
shape of mistake this file exists to prevent, and it was live in `paired_confirm` until the `set` kind
arrived.
"""

from __future__ import annotations

import numpy as np

from pravrudhi.application.confirm_eval import pass_interval
from pravrudhi_kernel.metrics import ANSWER_KINDS, is_binary


def test_a_binary_arm_gets_a_wilson_interval() -> None:
    x = np.array([1.0] * 67 + [0.0] * 33)
    got = pass_interval(x, binary=True, seed=0)
    assert got["method"] == "wilson"
    lo, hi = got["ci95"]
    assert lo < 0.67 < hi


def test_a_fractional_arm_gets_a_bootstrap_mean_interval_instead() -> None:
    """Not a Wilson interval on a truncated count. These are Jaccard values with real denominators."""
    x = np.array([2 / 3, 1 / 3, 1.0, 0.0, 3 / 4] * 20)
    got = pass_interval(x, binary=False, seed=0)
    assert got["method"] == "bca_mean"
    lo, hi = got["ci95"]
    assert lo < float(x.mean()) < hi


def test_the_truncation_this_guards_against_is_real_and_not_hypothetical() -> None:
    """Demonstrates the bug rather than asserting the fix in the abstract: `int(sum)` on fractional scores
    throws away most of the signal, and the interval it yields is centred somewhere else entirely."""
    x = np.array([2 / 3] * 100)  # every item two-thirds right; the true mean is 0.667
    truncated_successes = int(x.sum())  # 66, from 66.666...
    assert truncated_successes == 66
    honest = pass_interval(x, binary=False, seed=0)
    # The bootstrap interval on identical values is degenerate at the mean, which is the correct answer here.
    assert honest["ci95"][0] == honest["ci95"][1] == float(x.mean())
    # And the Wilson interval a caller would have got is a wide interval for a proportion that does not exist.
    wrong = pass_interval(x, binary=True, seed=0)
    assert wrong["ci95"][1] - wrong["ci95"][0] > 0.1


def test_an_empty_arm_reports_no_interval_rather_than_a_fabricated_one() -> None:
    for binary in (True, False):
        got = pass_interval(np.array([]), binary=binary, seed=0)
        assert got["ci95"] is None
        assert got["method"] in ("wilson", "bca_mean")


def test_every_declared_answer_kind_has_a_scale() -> None:
    """`is_binary` is the single source of the choice, so a new kind cannot be added without one."""
    assert {is_binary(k) for k in ANSWER_KINDS} == {True, False}
    assert not is_binary("set")
