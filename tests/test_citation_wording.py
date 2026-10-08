"""#813: an element's citation claim is chosen by `quote_source` alone, and never claims a quote the judge did not write."""

from __future__ import annotations

import pytest

from pravrudhi.api.partner import ElementResultOut
from pravrudhi.application.citation_wording import MODEL_NOTE, citation_note, whole_fact_note

WHOLE = "Cites your fact F1 in full (the judge names the fact; it does not quote words)."


def test_a_judge_written_quote_that_passed_the_check_says_so() -> None:
    assert citation_note("model", "F1") == "A word-for-word quote that passed the quote check."
    assert citation_note("model", None) == MODEL_NOTE


def test_a_whole_fact_citation_says_the_fact_is_cited_in_full_and_no_words_are_quoted() -> None:
    note = citation_note("whole_fact", "F3")
    assert note == "Cites your fact F3 in full (the judge names the fact; it does not quote words)."
    assert "word-for-word" not in note and "quote check" not in note


def test_no_quote_source_makes_no_citation_claim() -> None:
    assert citation_note(None, "F1") is None
    assert citation_note(None, None) is None


def test_a_whole_fact_note_without_a_fact_id_still_quotes_nothing() -> None:
    assert whole_fact_note(None) == "Cites a fact in full (the judge names the fact; it does not quote words)."


@pytest.mark.parametrize("unknown", ["span", "", "Model", " whole_fact"])
def test_an_unknown_source_raises_instead_of_being_worded_as_a_quote_or_as_no_claim(unknown: str) -> None:
    """Fail-closed by design: only None means no claim, so an empty string is an unknown source like any other."""
    with pytest.raises(ValueError, match="unknown quote_source"):
        citation_note(unknown, "F1")


def _element(source: str | None, fact_id: str | None) -> ElementResultOut:
    on = source is not None
    return ElementResultOut(
        element="a promise",
        is_denial=False,
        status="established" if on else "not_established",
        claimed=on,
        p_established=0.9,
        fact_id=fact_id,
        quote="q" if on else None,
        start=0 if on else None,
        end=1 if on else None,
        quote_check="ok" if on else None,
        attempts=1,
        occurrences=1,
        offsets_source="system" if on else None,
        quote_source=source,
        error=None,
    )


@pytest.mark.parametrize(
    ("source", "fact_id", "expected"),
    [("model", "F1", MODEL_NOTE), ("whole_fact", "F1", WHOLE), (None, None, None)],
)
def test_the_response_element_carries_the_note_for_its_quote_source(
    source: str | None, fact_id: str | None, expected: str | None
) -> None:
    assert _element(source, fact_id).model_dump()["citation_note"] == expected
