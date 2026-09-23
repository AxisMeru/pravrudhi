"""The mechanical quote check the Nyaya agent puts between every judge and the Lean wire.

A judge that calls an element established names a fact and quotes it: `{status, fact_id, quote}`. The judge
never supplies character offsets -- the SYSTEM locates the quote with an exact `str.find` in
`facts[fact_id]` and computes `start`/`end` itself (first occurrence; the occurrence count is recorded).
Verification stays strictly verbatim: no normalisation, no trimming, no fuzzy match. A quote that is not a
verbatim substring of the named fact is rejected, and nothing here repairs it.

Facts below are hand-written toy text, not drawn from any evaluation set.
"""

from __future__ import annotations

from typing import Any

from pravrudhi.application.nyaya_quote import check_judgment, locate_quote

FACTS = {
    "F1": "TOY: Arun married Bela in 2019.",
    "F2": "TOY: Arun struck Bela every time she refused to ask her father for money.",
    "F3": "TOY: Arun shouted at Bela; later Arun shouted at her mother.",
}


class TestLocateQuote:
    def test_verbatim_substring_is_located_by_the_system(self) -> None:
        loc = locate_quote(FACTS, fact_id="F1", quote="Arun married Bela")
        assert (loc.valid, loc.reason) == (True, "ok")
        assert (loc.start, loc.end) == (5, 22)
        assert FACTS["F1"][loc.start : loc.end] == "Arun married Bela"
        assert loc.occurrences == 1
        assert loc.offsets_source == "system"

    def test_whole_fact_quote_is_valid(self) -> None:
        text = FACTS["F2"]
        loc = locate_quote(FACTS, fact_id="F2", quote=text)
        assert loc.valid and (loc.start, loc.end) == (0, len(text))

    def test_multiple_occurrences_take_the_first_and_record_the_count(self) -> None:
        loc = locate_quote(FACTS, fact_id="F3", quote="Arun shouted at")
        assert loc.valid
        assert (loc.start, loc.end) == (5, 20)
        assert loc.occurrences == 2

    def test_occurrences_count_overlapping_matches(self) -> None:
        loc = locate_quote({"F1": "aaaa"}, fact_id="F1", quote="aa")
        assert (loc.start, loc.occurrences) == (0, 3)

    def test_non_verbatim_quote_is_rejected(self) -> None:
        loc = locate_quote(FACTS, fact_id="F1", quote="Arun wed Bela")
        assert (loc.valid, loc.reason) == (False, "quote_not_found")
        assert loc.start is None and loc.end is None and loc.occurrences == 0

    def test_case_is_not_normalised(self) -> None:
        assert locate_quote(FACTS, fact_id="F1", quote="arun married bela").reason == "quote_not_found"

    def test_whitespace_is_not_trimmed(self) -> None:
        # The fact ends "in 2019." -- a quote with a trailing space the fact does not have is not in it.
        assert locate_quote(FACTS, fact_id="F1", quote="Arun married Bela in 2019. ").reason == "quote_not_found"
        assert locate_quote(FACTS, fact_id="F1", quote="Arun  married Bela").reason == "quote_not_found"

    def test_quote_from_a_different_fact_is_rejected(self) -> None:
        assert locate_quote(FACTS, fact_id="F1", quote="Arun struck Bela").reason == "quote_not_found"

    def test_unknown_fact_is_rejected(self) -> None:
        assert locate_quote(FACTS, fact_id="F9", quote="Arun").reason == "unknown_fact"

    def test_missing_fact_or_quote_is_rejected(self) -> None:
        assert locate_quote(FACTS, fact_id=None, quote="Arun").reason == "no_quote"
        assert locate_quote(FACTS, fact_id="F1", quote=None).reason == "no_quote"

    def test_empty_quote_is_rejected_not_found_at_zero(self) -> None:
        """`str.find("")` is 0 -- an empty quote would otherwise 'match' every fact."""
        assert locate_quote(FACTS, fact_id="F1", quote="").reason == "empty_quote"


class TestCheckJudgment:
    def _judgment(self, **over: Any) -> dict[str, Any]:
        base: dict[str, Any] = {"status": "established", "fact_id": "F1", "quote": "Arun married Bela"}
        base.update(over)
        return base

    def test_established_with_verbatim_quote_is_valid(self) -> None:
        assert check_judgment(FACTS, self._judgment()).valid

    def test_model_offsets_are_ignored_even_when_they_overshoot(self) -> None:
        """A model's own offsets no longer matter: `start`/`end` in the judgment are never read."""
        loc = check_judgment(FACTS, self._judgment(start=0, end=999))
        assert loc.valid and (loc.start, loc.end) == (5, 22)

    def test_not_established_is_never_a_valid_quote(self) -> None:
        loc = check_judgment(FACTS, self._judgment(status="not_established"))
        assert (loc.valid, loc.reason) == (False, "not_established")

    def test_missing_keys_are_invalid_not_a_crash(self) -> None:
        assert check_judgment(FACTS, {"status": "established"}).reason == "no_quote"
