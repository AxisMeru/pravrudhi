"""The mechanical span check the Nyaya agent puts between every judge and the Lean wire.

A judgment that says an element is established must name a fact and an exact span of it; the span is valid
only when `facts[fact_id][start:end] == quote` exactly. Everything else is invalid and says why -- the agent
then treats the element as not established, and nothing here ever repairs a span to make it fit.

Facts below are hand-written toy text, not drawn from any evaluation set.
"""

from __future__ import annotations

from typing import Any

import pytest

from pravrudhi.application.nyaya_quote import check_judgment, check_quote

FACTS = {
    "F1": "TOY: Arun married Bela in 2019.",
    "F2": "TOY: Arun struck Bela every time she refused to ask her father for money.",
}


def _judgment(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {"status": "established", "fact_id": "F1", "quote": "Arun married Bela", "start": 5, "end": 22}
    base.update(over)
    return base


class TestCheckQuote:
    def test_exact_span_is_valid(self) -> None:
        res = check_quote(FACTS, fact_id="F1", quote="Arun married Bela", start=5, end=22)
        assert res.valid
        assert res.reason == "ok"

    def test_whole_fact_span_is_valid(self) -> None:
        text = FACTS["F2"]
        assert check_quote(FACTS, fact_id="F2", quote=text, start=0, end=len(text)).valid

    def test_unknown_fact_is_invalid(self) -> None:
        res = check_quote(FACTS, fact_id="F9", quote="Arun", start=0, end=4)
        assert not res.valid
        assert res.reason == "unknown_fact"

    def test_missing_span_is_invalid(self) -> None:
        res = check_quote(FACTS, fact_id=None, quote=None, start=None, end=None)
        assert not res.valid
        assert res.reason == "no_span"

    @pytest.mark.parametrize(("start", "end"), [(-1, 4), (4, 4), (10, 5), (0, 999)])
    def test_out_of_bounds_or_empty_offsets_are_invalid(self, start: int, end: int) -> None:
        """`text[0:999]` would silently truncate to the whole fact in Python -- the check refuses it rather
        than letting slicing semantics accept an offset the text does not have."""
        text = FACTS["F1"]
        res = check_quote(FACTS, fact_id="F1", quote=text[max(start, 0) : end], start=start, end=end)
        assert not res.valid
        assert res.reason == "bad_offsets"

    def test_offsets_without_a_quote_are_judged_on_the_offsets_first(self) -> None:
        """The house judge names offsets but no quote when they fall outside the fact (live: `F1:0:103` of an
        87-character fact) -- that is reported as the bad offsets it is, not as a missing span."""
        res = check_quote(FACTS, fact_id="F1", quote=None, start=0, end=103)
        assert (res.valid, res.reason) == (False, "bad_offsets")

    def test_in_bounds_offsets_without_a_quote_are_a_mismatch(self) -> None:
        res = check_quote(FACTS, fact_id="F1", quote=None, start=0, end=3)
        assert (res.valid, res.reason) == (False, "quote_mismatch")

    def test_bool_offsets_are_not_integers(self) -> None:
        res = check_quote(FACTS, fact_id="F1", quote="T", start=False, end=True)
        assert not res.valid
        assert res.reason == "bad_offsets"

    def test_non_integer_offsets_are_invalid(self) -> None:
        res = check_quote(FACTS, fact_id="F1", quote="TOY", start="0", end="3")  # type: ignore[arg-type]
        assert not res.valid
        assert res.reason == "bad_offsets"

    def test_quote_that_differs_by_one_character_is_invalid(self) -> None:
        res = check_quote(FACTS, fact_id="F1", quote="Arun married Bela ", start=5, end=22)
        assert not res.valid
        assert res.reason == "quote_mismatch"

    def test_case_and_whitespace_are_not_normalised(self) -> None:
        assert not check_quote(FACTS, fact_id="F1", quote="arun married bela", start=5, end=22).valid

    def test_right_words_at_the_wrong_offsets_are_invalid(self) -> None:
        assert not check_quote(FACTS, fact_id="F1", quote="Arun married Bela", start=4, end=21).valid


class TestCheckJudgment:
    def test_established_with_exact_span_is_valid(self) -> None:
        assert check_judgment(FACTS, _judgment()).valid

    def test_not_established_is_never_a_valid_span(self) -> None:
        res = check_judgment(FACTS, _judgment(status="not_established"))
        assert not res.valid
        assert res.reason == "not_established"

    def test_missing_keys_are_invalid_not_a_crash(self) -> None:
        res = check_judgment(FACTS, {"status": "established"})
        assert not res.valid
        assert res.reason == "no_span"
