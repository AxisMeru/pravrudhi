"""The mechanical quote check between every Nyaya element judge and the Lean wire.

A judge that calls an element *established* names the fact that establishes it and quotes it:
`{status, fact_id, quote}`. The judge never supplies character offsets -- a model is not asked to count
characters. The SYSTEM locates the quote with an exact `str.find` in `facts[fact_id]` and computes `start`/
`end` itself (`offsets_source: "system"`). A quote that occurs more than once is `ambiguous_quote`: which occurrence
the judge meant is unknowable, so `locate_quote` returns `valid=False`, the element is NOT established, and the
occurrence count is recorded (no occurrence is chosen).

Verification is verbatim except for whitespace (#876): a quote is located exactly if it is a substring of the fact; only if it
is not is it searched with whitespace runs collapsed on both sides, the span mapped back to the raw fact. No case folding,
no fuzzy match. A quote that is not found either way is rejected and says why. The caller
(`nyaya_agent`) then treats the element as not established; this module never repairs a quote to make it fit.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

Reason = Literal[
    "ok",
    "not_established",
    "no_quote",
    "unknown_fact",
    "empty_quote",
    "non_evidential_quote",
    "quote_not_found",
    "ambiguous_quote",
]


#: A quote must carry at least this many letter, digit or combining-mark characters (Unicode L*, N*, M*; marks
#: count so a Devanagari word is not undercounted). One character or pure punctuation is not evidence.
MIN_EVIDENTIAL_CHARS = 2


def _content_chars(quote: str) -> int:
    return sum(unicodedata.category(c)[0] in "LNM" for c in quote)


@dataclass(frozen=True)
class QuoteLocation:
    valid: bool
    reason: Reason
    start: int | None = None
    end: int | None = None
    occurrences: int = 0
    offsets_source: Literal["system"] = "system"
    #: "exact" = located as a verbatim substring of the raw fact; "whitespace_collapsed" = located only after collapsing
    #: whitespace runs on both sides (#876); "none" = the quote was not located (any other reason). Audit-only: it never
    #: reaches the analyse-facts response.
    match: Literal["exact", "whitespace_collapsed", "none"] = "none"


def _count_occurrences(text: str, quote: str) -> int:
    """Every start position the quote matches at, overlapping ones included."""
    n, i = 0, text.find(quote)
    while i != -1:
        n += 1
        i = text.find(quote, i + 1)
    return n


def _collapse_with_map(text: str) -> tuple[str, list[int]]:
    """`" ".join(text.split())` plus, for every character of the result, its index in `text` (-1 for the joining space)."""
    out: list[str] = []
    idx: list[int] = []
    pending_gap = False
    for i, ch in enumerate(text):
        if ch.isspace():
            pending_gap = bool(out)
            continue
        if pending_gap:
            out.append(" ")
            idx.append(-1)
            pending_gap = False
        out.append(ch)
        idx.append(i)
    return "".join(out), idx


def _locate_collapsed(text: str, quote: str) -> QuoteLocation:
    """The whitespace-tolerant fallback (#876): find the whitespace-collapsed quote in the whitespace-collapsed fact and map
    the span back to RAW offsets in `text` (the fact as stored, whitespace runs and all). Only whitespace is forgiven: case,
    punctuation, zero-width characters and every real word still have to match."""
    ctext, idx = _collapse_with_map(text)
    cquote = " ".join(quote.split())
    start = ctext.find(cquote)
    if start == -1:
        return QuoteLocation(False, "quote_not_found")
    occurrences = _count_occurrences(ctext, cquote)
    if occurrences > 1:
        return QuoteLocation(False, "ambiguous_quote", occurrences=occurrences, match="whitespace_collapsed")
    return QuoteLocation(True, "ok", idx[start], idx[start + len(cquote) - 1] + 1, occurrences, match="whitespace_collapsed")


def locate_quote(facts: Mapping[str, str], *, fact_id: str | None, quote: str | None) -> QuoteLocation:
    """Valid iff `fact_id` is a known fact and `quote` is a substring of it with at least `MIN_EVIDENTIAL_CHARS` content
    characters; `start`/`end` are then the RAW offsets of its single occurrence, computed here. A quote occurring more
    than once is `ambiguous_quote`. Strict first: a verbatim substring is located exactly as before (`match="exact"`). Only on
    a strict miss is the quote searched whitespace-collapsed (#876, `match="whitespace_collapsed"`): runs of whitespace of
    any kind, line breaks and NBSP included, are equivalent, and nothing else is."""
    if fact_id is None:
        return QuoteLocation(False, "no_quote")
    if fact_id not in facts:
        return QuoteLocation(False, "unknown_fact")
    if quote is None:
        return QuoteLocation(False, "no_quote")
    if quote.strip() == "":
        # `str.find("")` is 0: an empty quote would otherwise "match" every fact; a whitespace-only one
        # matches any multi-word fact and is just as empty of evidence.
        return QuoteLocation(False, "empty_quote")
    if _content_chars(quote) < MIN_EVIDENTIAL_CHARS:
        return QuoteLocation(False, "non_evidential_quote")
    text = facts[fact_id]
    start = text.find(quote)
    if start == -1:
        return _locate_collapsed(text, quote)
    occurrences = _count_occurrences(text, quote)
    if occurrences > 1:
        # Which occurrence the judge meant is unknowable; picking the first would ground it silently.
        return QuoteLocation(False, "ambiguous_quote", occurrences=occurrences, match="exact")
    return QuoteLocation(True, "ok", start, start + len(quote), occurrences, match="exact")


def check_judgment(facts: Mapping[str, str], judgment: Mapping[str, Any]) -> QuoteLocation:
    """`locate_quote` over a judgment mapping. Any `start`/`end` a judgment carries is ignored -- offsets are
    the system's. A `not_established` judgment carries no quote to accept, so it is never valid here."""
    if judgment.get("status") != "established":
        return QuoteLocation(False, "not_established")
    return locate_quote(facts, fact_id=judgment.get("fact_id"), quote=judgment.get("quote"))
