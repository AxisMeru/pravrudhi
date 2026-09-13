"""Small paired-evaluation statistics using only Python and NumPy."""

import math
import numpy as np


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if not 0 <= k <= n or n < 0 or not math.isfinite(z) or z <= 0:
        raise ValueError("require 0 <= k <= n and finite z > 0")
    if n == 0:
        return 0.0, 1.0
    p = k / n
    denominator = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denominator
    radius = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denominator
    return max(0.0, center - radius), min(1.0, center + radius)


def _paired(a, b):
    if len(a) != len(b):
        raise ValueError("paired samples must have equal lengths")
    return np.asarray(a, dtype=bool), np.asarray(b, dtype=bool)


def paired_table(a: list[bool], b: list[bool]) -> tuple[int, int]:
    """Return (b succeeds / a fails, a succeeds / b fails)."""
    aa, bb = _paired(a, b)
    return int(np.sum(~aa & bb)), int(np.sum(aa & ~bb))


def mcnemar_exact(b: int, c: int) -> float:
    """Two-sided exact binomial test on the discordant pairs."""
    if not isinstance(b, int) or not isinstance(c, int) or b < 0 or c < 0:
        raise ValueError("discordant counts must be nonnegative integers")
    n = b + c
    if n == 0:
        return 1.0
    # Integer arithmetic avoids underflow from starting a recurrence at 2**-n.
    term = total = 1
    for i in range(1, min(b, c) + 1):
        term = term * (n - i + 1) // i
        total += term
    return min(1.0, (2 * total) / (1 << n))


mcnemar = mcnemar_exact


def bootstrap_diff(a: list[bool], b: list[bool], n_boot: int = 2000,
                   seed: int = 0) -> tuple[float, float]:
    """Paired percentile 95% interval for mean(b) - mean(a)."""
    aa, bb = _paired(a, b)
    if not len(aa) or n_boot <= 0:
        raise ValueError("bootstrap requires nonempty samples and n_boot > 0")
    differences = bb.astype(float) - aa.astype(float)
    rng = np.random.default_rng(seed)
    means = np.empty(n_boot)
    for i in range(n_boot):
        means[i] = np.mean(differences[rng.integers(len(aa), size=len(aa))])
    lo, hi = np.quantile(means, [0.025, 0.975])
    return float(lo), float(hi)
