"""Pure helpers for the P3 citation-verification pre-registration
(`research/prereg/p3-citation-verification-2026-09-25.md`): the exact Clopper-Pearson interval used for both
recall and resolution-precision, Cohen's kappa for the two-labeler agreement, and the two code-exact sampling
procedures (prereg §3/§4). None of this touches the real case index -- the sampling functions here take a
document/row count and return indices; the caller supplies the actual rows.
"""

from __future__ import annotations

import random
from math import comb, exp, fsum, log, log1p


def _beta_ppf(q: float, a: float, b: float) -> float:
    """The `q`-quantile of a Beta(a, b) distribution via bisection on the regularized incomplete beta
    function, computed as a sum of the equivalent binomial tail (both `a` and `b` are always integers or
    half-integers here since `a`/`b` come from Clopper-Pearson's own success/failure counts). Avoids a scipy
    dependency for one formula, consistent with `application.discordance._lower_proportion`'s own bisection
    style elsewhere in this project."""
    if q <= 0.0:
        return 0.0
    if q >= 1.0:
        return 1.0
    # For integer a, b: P(Beta(a, b) <= p) = P(Binomial(a + b - 1, p) >= a) -- the standard identity
    # Clopper-Pearson itself is built on. a, b here are always positive integers (k, n-k+1 or k+1, n-k).
    n = int(round(a + b - 1))
    k = int(round(a))
    coefficients = [(j, log(comb(n, j))) for j in range(k, n + 1)]
    low, high = 0.0, 1.0
    for _ in range(200):
        p = (low + high) / 2.0
        if p in (low, high):
            break
        tail = fsum(exp(c + j * log(p) + (n - j) * log1p(-p)) if 0 < p < 1 else (1.0 if p == 1 else 0.0) for j, c in coefficients)
        if tail < q:
            low = p
        else:
            high = p
    return (low + high) / 2.0


def clopper_pearson_ci(k: int, n: int) -> tuple[float, float]:
    """The exact two-sided 95% Clopper-Pearson interval for `k` successes of `n` trials (prereg §3/§4's one
    line: `[Beta.ppf(0.025, k, n-k+1), Beta.ppf(0.975, k+1, n-k)]`, with the usual k=0/k=n edge cases)."""
    if n <= 0:
        raise ValueError(f"n must be positive, got {n}")
    if not 0 <= k <= n:
        raise ValueError(f"k={k} must be between 0 and n={n}")
    lower = 0.0 if k == 0 else _beta_ppf(0.025, k, n - k + 1)
    upper = 1.0 if k == n else _beta_ppf(0.975, k + 1, n - k)
    return lower, upper


def cohens_kappa(a: list[str], b: list[str]) -> float:
    """Cohen's kappa for two labelers' categorical judgments over the same items, in the same order.

    kappa = (p_o - p_e) / (1 - p_e), where p_o is observed agreement and p_e is chance agreement from each
    labeler's own marginal category frequencies. When p_e == 1.0 (both labelers' marginals are a single,
    shared category with no variation at all), kappa's usual formula is 0/0; since p_o must also be 1.0 in
    that case (every item is that one category for both), this is defined as 1.0 -- real, if uninformative,
    perfect agreement -- rather than NaN.
    """
    if len(a) != len(b):
        raise ValueError(f"labelers judged different numbers of items: {len(a)} vs {len(b)}")
    if not a:
        raise ValueError("cannot compute kappa over zero items")
    n = len(a)
    categories = sorted(set(a) | set(b))
    p_o = sum(x == y for x, y in zip(a, b, strict=True)) / n
    p_e = sum((a.count(c) / n) * (b.count(c) / n) for c in categories)
    if p_e >= 1.0:
        return 1.0
    return (p_o - p_e) / (1 - p_e)


def recall_sample_order(n_docs: int, seed: int) -> list[int]:
    """Prereg §3's code-exact recall-sample walk order: one full random permutation of `range(n_docs)`,
    deterministic for a fixed seed, no reseeding partway through."""
    rng = random.Random(seed)
    return rng.sample(range(n_docs), n_docs)


def precision_sample_indices(n_rows: int, k: int, seed: int) -> list[int]:
    """Prereg §4's code-exact resolution-precision sample: `k` distinct indices drawn from `range(n_rows)`,
    deterministic for a fixed seed."""
    if k > n_rows:
        raise ValueError(f"cannot sample k={k} distinct rows from a population of only {n_rows}")
    rng = random.Random(seed)
    return rng.sample(range(n_rows), k)
