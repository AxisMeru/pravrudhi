"""Negative fixture: the patterns `scripts/check_fail_open_defaults.py` must NOT flag.

Every one is copied or reduced from a place in this repo that already gets it right, cited by file:line, so
the guard is measured against the codebase's own good practice rather than against invented examples. If a
change to the guard starts flagging anything in here, the guard is wrong, not this file.
"""

from __future__ import annotations

import math
from typing import Any


class JudgeOutputError(ValueError):
    """Raised, never defaulted -- `src/pravrudhi/application/nyaya_judges.py:47-49`."""


def raise_rather_than_guess(top: dict[str, float]) -> float:
    """`nyaya_judges.p_established_from_top_logprobs`, lines 170-181: "Raises when neither token is in the
    top-k: that is no evidence either way, and 0.5 would be a number the model never gave."
    """
    est = max((top[t] for t in (" established", "established") if t in top), default=-math.inf)
    neg = max((top[t] for t in (" not", "not") if t in top), default=-math.inf)
    if est == -math.inf and neg == -math.inf:
        raise JudgeOutputError(f"neither token among the first token's top logprobs: {dict(top)}")
    return 1.0 / (1.0 + math.exp(neg - est))


def propagate_none(p: float | None, tau: float) -> str | None:
    """`scripts/t2_c3_score.py:73-76` -- a parse failure has no status, rather than a defaulted one."""
    if p is None:
        return None
    return "established" if p >= tau else "not_established"


def exclude_and_count(scored: list[dict[str, Any]], p_key: str, status_key: str) -> dict[str, Any]:
    """`scripts/t2_c3_score.py:87-92` and `:121-122`: unscoreable rows are EXCLUDED from the denominator,
    the exclusions are COUNTED separately, and n=0 yields NaN rather than 0.0."""
    negatives = [s for s in scored if s["gold"] != "established" and s[p_key] is not None]
    false_positives = sum(1 for s in negatives if s[status_key] == "established")
    n_negative = len(negatives)
    return {
        "n_neg": n_negative,
        "fp": false_positives,
        "rate": false_positives / n_negative if n_negative else float("nan"),
        "parse_rate": sum(1 for s in scored if s[p_key] is not None) / len(scored) if scored else float("nan"),
    }


def interval_is_none_without_data(xs: list[float]) -> dict[str, Any]:
    """`src/pravrudhi/application/confirm_eval.py:41-55` -- `ci95: None` at n=0, and the method is named."""
    n = len(xs)
    if not n:
        return {"method": "wilson", "ci95": None}
    return {"method": "wilson", "ci95": [min(xs), max(xs)]}


def mean_is_none_without_data(deltas: list[float]) -> float | None:
    """`src/pravrudhi/application/anchor.py:111-112` -- None, not 0.0, when nothing was anchorable."""
    return sum(deltas) / len(deltas) if deltas else None


def leave_one_out(trials: dict[str, int], successes: dict[str, int], item: str, score: int) -> float | None:
    """`src/pravrudhi/application/anchor.py:54-63`: "None when the item carries no evidence beyond the
    observation being scored, which is the honest answer."
    """
    n = trials.get(item, 0)
    if n <= 1:
        return None
    return (successes.get(item, 0) - score) / (n - 1)


def all_over_empty_is_guarded(results: list[Any]) -> bool:
    """`src/pravrudhi/application/completion.py:94`: "No evidence is not verified evidence." Contrast
    `src/pravrudhi/application/evidence.py:341`, which is the same expression WITHOUT the `bool(seq)` guard
    and so claims every observation was container-isolated from zero observations."""
    return bool(results) and all(getattr(r, "verified", False) for r in results)


def refuse_rather_than_round(scores: dict[str, float], which: str) -> dict[str, int]:
    """`src/pravrudhi/application/discordance.py:55-71` -- anything that is not 0 or 1 is refused, never
    rounded into a binary outcome."""
    out: dict[str, int] = {}
    for item, value in scores.items():
        if value not in (0, 1):
            raise ValueError(f"{which} item {item!r} scored {value!r}, which is not a binary outcome")
        out[item] = int(value)
    return out


def raise_on_empty_denominator(k: int, n: int) -> tuple[float, float]:
    """`src/pravrudhi/application/p3_prereg.py:45-54` -- raises on n<=0 instead of inventing an interval.

    The `k == 0` branch returns a real 0.0: that is the EXACT Clopper-Pearson lower bound at zero successes,
    not a substitute for a missing measurement. The guard's `COUNT_TOKENS` restriction is what keeps this and
    `src/pravrudhi/application/discordance.py:37-38` out of the findings.
    """
    if n <= 0:
        raise ValueError(f"n must be positive, got {n}")
    lower = 0.0 if k == 0 else k / (n + 1)
    upper = 1.0 if k == n else (k + 1) / (n + 1)
    return lower, upper


def none_default_is_correct(row: dict[str, Any]) -> int | None:
    """`src/pravrudhi/application/anchor.py:137-139` and `application/kshudha.py:136-138` -- a bare `.get`
    with NO default, then an explicit guard that skips the row. `None` as a default is never flagged."""
    score = row.get("score")
    if not isinstance(score, int):
        return None
    return score


def optional_display_field(row: dict[str, Any]) -> str:
    """A genuinely optional display field, the false-positive class rule 1 must stay clear of: `note` is not
    decision-bearing, so an empty default is correct (`application/kshudha.py:141`)."""
    return str(row.get("note") or "")
