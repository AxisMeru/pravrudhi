"""Shared helper for the scripts that score a typed-arm completion. `score_decision` returns
`(scores, missing_options)`; an option in `missing_options` has only a bound, not a measured score, so a script
that compares or seals a typed p must not take `scores["true"]` as if it were exact."""

from __future__ import annotations

from pravrudhi.application.typed.decoder import score_decision
from pravrudhi.application.typed.house_judge import _STATUS_FIELD
from pravrudhi.models.openai_compat import CompletionResult


class BoundedTypedScore(RuntimeError):
    """A typed decision option had no token in the top-k, so its score is a bound, not a number."""


def typed_p(res: CompletionResult) -> float:
    scores, missing = score_decision(res, _STATUS_FIELD)
    if missing:
        raise BoundedTypedScore(f"typed option(s) {sorted(missing)} missing from the top-k; p is a bound, not a score")
    return scores["true"]
