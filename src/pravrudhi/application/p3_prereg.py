"""Pure helpers for the P3 citation-verification pre-registration
(`research/prereg/p3-citation-verification-2026-09-25.md`): the exact Clopper-Pearson interval used for both
recall and resolution-precision, Cohen's kappa for the two-labeler agreement, and the two code-exact sampling
procedures (prereg §3/§4). None of this touches the real case index -- the sampling functions here take a
document/row count and return indices; the caller supplies the actual rows.
"""

from __future__ import annotations

import random
import sqlite3
from math import comb, exp, fsum, log, log1p

from pravrudhi.application.verify import VerifyResult, resolve_citation_key


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


def _load_text(conn: sqlite3.Connection, case_id: str) -> str:
    """The real gap R1 found (2026-09-25): a sampled record with empty/missing text would have sent a
    labeler an empty prompt. Refuse here, once, rather than let every caller re-check."""
    row = conn.execute("SELECT text FROM cases WHERE case_id = ?", (case_id,)).fetchone()
    text = (row[0] if row else "") or ""
    if not text.strip():
        raise ValueError(f"case_id {case_id!r} has no usable text (empty or not found) -- refusing to emit it")
    return text


def recall_order_with_text(conn: sqlite3.Connection, case_id_order: list[str], prefix: int) -> list[dict[str, str]]:
    """Load the real document text for the first `prefix` entries of the prereg §3 walk order. `prefix`
    over-provisions the walk (the labeler stops at their own 200th hand-marked citation, which this function
    cannot know in advance) rather than loading text for all 38,657 documents up front; if a labeler exhausts
    `prefix` documents before reaching 200 citations, re-run with a larger `prefix` over the SAME `case_id_order`
    -- a strict extension of the same frozen walk, never a different order."""
    return [{"case_id": cid, "text": _load_text(conn, cid)} for cid in case_id_order[:prefix]]


def precision_sample_with_text(conn: sqlite3.Connection, sample: list[dict[str, object]]) -> list[dict[str, object]]:
    """Attach the real text a Labeler needs to judge each sampled alias's resolution (prereg §4): the citing
    document's own text (always required -- refuses if missing, same as `recall_order_with_text`), plus,
    when `verify()`'s resolution step (`resolve_citation_key`) actually resolves the alias's citation, the
    resolved case's own id/title/text too. `status` is `"resolved"`, `"not_in_index"`, or `"conflict"` --
    never a bare boolean, so a labeler (or the scoring script) can tell a real ambiguity from "no evidence"."""
    out = []
    for item in sample:
        citing_text = _load_text(conn, str(item["citing_case_id"]))
        resolved = resolve_citation_key(conn, str(item["citation"]))
        record = {**item, "citing_text": citing_text}
        if resolved.status is None:
            row = resolved.case_rows[0]
            record["status"] = "resolved"
            record["resolved_case_id"] = row["case_id"]
            record["resolved_title"] = row["title"]
            record["resolved_text"] = row["text"]
        elif resolved.status == VerifyResult.CONFLICT:
            record["status"] = "conflict"
        else:
            record["status"] = "not_in_index"
        out.append(record)
    return out
