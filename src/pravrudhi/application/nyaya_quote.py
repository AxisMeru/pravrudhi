"""The mechanical span check between every Nyaya element judge and the Lean wire.

A judge that calls an element *established* must name the fact that establishes it and an exact span of that
fact: `{status, fact_id, quote, start, end}`. The span is valid only when `facts[fact_id][start:end] == quote`,
compared byte for byte -- no case folding, no whitespace normalisation, and no reliance on Python's forgiving
slice semantics (`text[0:999]` of a 30-character fact is the whole fact; here it is an out-of-bounds offset).

Anything else is invalid and says why. The caller (`nyaya_agent`) then treats the element as not established;
this module never repairs a span to make it fit, because a repaired span is a quote the judge did not give.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

Reason = Literal["ok", "not_established", "no_span", "unknown_fact", "bad_offsets", "quote_mismatch"]


@dataclass(frozen=True)
class QuoteCheck:
    valid: bool
    reason: Reason


def _is_int(x: object) -> bool:
    # `bool` is an `int` subclass; `True`/`False` are not offsets a judge meant.
    return isinstance(x, int) and not isinstance(x, bool)


def check_quote(
    facts: Mapping[str, str], *, fact_id: str | None, quote: str | None, start: int | None, end: int | None
) -> QuoteCheck:
    """Valid iff `fact_id` is a known fact, `0 <= start < end <= len(fact)`, and the slice equals `quote`."""
    if fact_id is None or start is None or end is None:
        return QuoteCheck(False, "no_span")
    if fact_id not in facts:
        return QuoteCheck(False, "unknown_fact")
    text = facts[fact_id]
    if not (_is_int(start) and _is_int(end)) or not (0 <= start < end <= len(text)):
        return QuoteCheck(False, "bad_offsets")
    if quote is None or text[start:end] != quote:
        return QuoteCheck(False, "quote_mismatch")
    return QuoteCheck(True, "ok")


def check_judgment(facts: Mapping[str, str], judgment: Mapping[str, Any]) -> QuoteCheck:
    """`check_quote` over a judgment mapping. A `not_established` judgment carries no span to accept, so it is
    never valid here -- the check answers "does this judgment establish the element?", nothing weaker."""
    if judgment.get("status") != "established":
        return QuoteCheck(False, "not_established")
    return check_quote(
        facts,
        fact_id=judgment.get("fact_id"),
        quote=judgment.get("quote"),
        start=judgment.get("start"),
        end=judgment.get("end"),
    )
