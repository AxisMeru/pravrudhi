"""What an element's citation claims, by who supplied the cited text (#813).

`quote_source` says where the quote text on an established element came from:

* `"model"` -- the judge wrote words (the opt-in frontier judge) and the mechanical quote check looked for them, word for word, in the
  fact the judge named. Only then is "a word-for-word quote that passed the quote check" true.
* `"whole_fact"` -- the house judges (the 4B first judge and the 32B second judge) name a fact; the cited text is that fact in full and
  nothing is quoted from it.
* `None` -- the element is not established (or has no citation): no citation claim is made.

The wording is chosen by `quote_source` alone, never by looking at the text, so a new source cannot be worded as a quote by default: an
unknown value raises instead of guessing.
"""

from __future__ import annotations

#: The one sentence for a judge-written quote that passed the quote check.
MODEL_NOTE = "A word-for-word quote that passed the quote check."


def whole_fact_note(fact_id: str | None) -> str:
    """The sentence for a cited fact taken in full. A missing fact id still names no words: it says the judge named a fact."""
    which = f"your fact {fact_id}" if fact_id else "a fact"
    return f"Cites {which} in full (the judge names the fact; it does not quote words)."


def citation_note(quote_source: str | None, fact_id: str | None) -> str | None:
    """The plain-language note for an element's citation, or None when there is no citation claim to make."""
    if quote_source is None:
        return None
    if quote_source == "model":
        return MODEL_NOTE
    if quote_source == "whole_fact":
        return whole_fact_note(fact_id)
    raise ValueError(f"unknown quote_source {quote_source!r}: add its wording here before it can reach a reader")
