import pytest

from prototypes.nyaya_ttt_rsi.stats import (
    bootstrap_diff, mcnemar, mcnemar_exact, paired_table, wilson,
)


def test_wilson():
    lo, hi = wilson(0, 10)
    assert lo == pytest.approx(0, abs=1e-15)
    assert hi == pytest.approx(0.27754, abs=1e-5)
    lo, hi = wilson(5, 9)
    assert lo == pytest.approx(0.266647, abs=1e-5)
    assert hi == pytest.approx(0.811225, abs=1e-5)
    assert wilson(0, 0) == (0, 1)
    with pytest.raises(ValueError):
        wilson(11, 10)


def test_mcnemar():
    assert mcnemar_exact(0, 0) == 1.0
    assert mcnemar_exact(10, 0) == pytest.approx(2 / 1024)
    assert mcnemar_exact(0, 10) == mcnemar(10, 0)
    assert mcnemar_exact(5, 5) == 1.0
    assert mcnemar_exact(2, 8) == pytest.approx(0.109375)
    assert mcnemar_exact(1000, 1000) == 1.0
    with pytest.raises(ValueError):
        mcnemar_exact(-1, 2)


def test_paired_table():
    assert paired_table([False, False, True, True], [True, True, False, True]) == (2, 1)
    assert paired_table([], []) == (0, 0)
    with pytest.raises(ValueError):
        paired_table([True], [])


def test_bootstrap_is_paired_and_reproducible():
    a = [True, False, True, False]
    assert bootstrap_diff(a, a) == (0.0, 0.0)
    assert bootstrap_diff([False] * 4, [True] * 4) == (1.0, 1.0)
    b = [True, True, True, False]
    assert bootstrap_diff(a, b, seed=7) == bootstrap_diff(a, b, seed=7)
    lo, hi = bootstrap_diff(a, b)
    assert 0 <= lo <= 0.25 <= hi <= 1
    with pytest.raises(ValueError):
        bootstrap_diff([], [])
    with pytest.raises(ValueError):
        bootstrap_diff(a, b, n_boot=0)
