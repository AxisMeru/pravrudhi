"""The mechanical quote check between every Nyaya element judge and the Lean wire.

A judge that calls an element *established* names the fact that establishes it and quotes it:
`{status, fact_id, quote}`. The judge never supplies character offsets -- a model is not asked to count
characters. The SYSTEM locates the quote with an exact `str.find` in `facts[fact_id]` and computes `start`/
`end` itself (`offsets_source: "system"`); on several occurrences it takes the first and records the count.

Verification stays strictly verbatim: no case folding, no whitespace trimming or collapsing, no fuzzy match.
A quote that is not a verbatim substring of the named fact is rejected and says why. The caller
(`nyaya_agent`) then treats the element as not established; this module never repairs a quote to make it fit.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

Reason = Literal["ok", "not_established", "no_quote", "unknown_fact", "empty_quote", "quote_not_found"]


@dataclass(frozen=True)
class QuoteLocation:
    valid: bool
    reason: Reason
    start: int | None = None
    end: int | None = None
    occurrences: int = 0
    offsets_source: Literal["system"] = "system"


def _count_occurrences(text: str, quote: str) -> int:
    """Every start position the quote matches at, overlapping ones included."""
    n, i = 0, text.find(quote)
    while i != -1:
        n += 1
        i = text.find(quote, i + 1)
    return n


def locate_quote(facts: Mapping[str, str], *, fact_id: str | None, quote: str | None) -> QuoteLocation:
    """Valid iff `fact_id` is a known fact and `quote` is a non-empty verbatim substring of it; `start`/`end`
    are then the first occurrence's offsets, computed here."""
    if fact_id is None:
        return QuoteLocation(False, "no_quote")
    if fact_id not in facts:
        return QuoteLocation(False, "unknown_fact")
    if quote is None:
        return QuoteLocation(False, "no_quote")
    if quote == "":
        # `str.find("")` is 0: an empty quote would otherwise "match" every fact.
        return QuoteLocation(False, "empty_quote")
    text = facts[fact_id]
    start = text.find(quote)
    if start == -1:
        return QuoteLocation(False, "quote_not_found")
    return QuoteLocation(True, "ok", start, start + len(quote), _count_occurrences(text, quote))


def check_judgment(facts: Mapping[str, str], judgment: Mapping[str, Any]) -> QuoteLocation:
    """`locate_quote` over a judgment mapping. Any `start`/`end` a judgment carries is ignored -- offsets are
    the system's. A `not_established` judgment carries no quote to accept, so it is never valid here."""
    if judgment.get("status") != "established":
        return QuoteLocation(False, "not_established")
    return locate_quote(facts, fact_id=judgment.get("fact_id"), quote=judgment.get("quote"))
