"""#876: whitespace-tolerant quote location (strict first; collapsed fallback; offsets mapped back to the RAW fact).

Facts below are hand-written toy text, not drawn from any evaluation set.
"""

from __future__ import annotations

import random

import pytest

from pravrudhi.application.nyaya_quote import locate_quote


def _collapse(s: str) -> str:
    return " ".join(s.split())


def _strict_reference(facts: dict[str, str], quote: str):
    """The pre-change behaviour, restated: strict find, overlapping occurrence count, ambiguity rule."""
    text = facts["F1"]
    start = text.find(quote)
    if start == -1:
        return ("quote_not_found", None, None, 0)
    n, i = 0, text.find(quote)
    while i != -1:
        n += 1
        i = text.find(quote, i + 1)
    if n > 1:
        return ("ambiguous_quote", None, None, n)
    return ("ok", start, start + len(quote), 1)


WRAPPED = "TOY: Arun struck Bela every\ntime she refused to ask\n    her father for money."


class TestCollapsedFallback:
    @pytest.mark.parametrize(
        "raw",
        [
            "TOY: Arun struck Bela every\ntime she refused",  # hard wrap
            "TOY: Arun struck\tBela every time she refused",  # tab
            "TOY: Arun struck Bela every time she refused",  # NBSP
            "TOY: Arun struck Bela\x0cevery time she refused",  # form feed
            "TOY: Arun struck Bela\r\nevery time she refused",  # CRLF
            "TOY: Arun struck Bela every     time she refused",  # indentation / runs
        ],
    )
    def test_collapsed_quote_is_found_in_raw_fact(self, raw: str) -> None:
        q = "Arun struck Bela every time she refused"
        loc = locate_quote({"F1": raw}, fact_id="F1", quote=q)
        assert (loc.valid, loc.reason, loc.match) == (True, "ok", "whitespace_collapsed")
        assert _collapse(raw[loc.start : loc.end]) == q
        assert not raw[loc.start].isspace() and not raw[loc.end - 1].isspace()

    def test_offsets_are_raw_not_collapsed(self) -> None:
        f = {"F1": WRAPPED}
        q = "refused to ask her father"
        loc = locate_quote(f, fact_id="F1", quote=q)
        assert loc.valid
        assert WRAPPED[loc.start : loc.end] == "refused to ask\n    her father"
        assert loc.end - loc.start == len(q) + 4  # raw gap 5 chars vs collapsed gap 1: never mixed

    def test_leading_and_trailing_whitespace_on_fact_and_quote(self) -> None:
        raw = "  \n TOY: Arun married Bela.  \n"
        loc = locate_quote({"F1": raw}, fact_id="F1", quote="  Arun   married Bela.\n")
        assert loc.valid and raw[loc.start : loc.end] == "Arun married Bela."

    def test_quote_with_real_whitespace_variants_of_the_same_words(self) -> None:
        loc = locate_quote({"F1": "TOY: Arun  married\tBela in 2019."}, fact_id="F1", quote="Arun married Bela")
        assert loc.valid and loc.match == "whitespace_collapsed"


class TestStillRejected:
    def test_one_real_word_different_fails(self) -> None:
        loc = locate_quote({"F1": WRAPPED}, fact_id="F1", quote="Arun struck Bela every day she refused")
        assert (loc.valid, loc.reason) == (False, "quote_not_found") and loc.start is None

    def test_case_still_not_normalised(self) -> None:
        assert locate_quote({"F1": WRAPPED}, fact_id="F1", quote="arun struck bela").reason == "quote_not_found"

    def test_punctuation_still_matters(self) -> None:
        assert (
            locate_quote({"F1": "TOY: Arun, married Bela."}, fact_id="F1", quote="Arun married Bela").reason == "quote_not_found"
        )

    def test_a_word_split_by_whitespace_is_not_joined(self) -> None:
        assert (
            locate_quote({"F1": "TOY: Arun mar ried Bela."}, fact_id="F1", quote="Arun married Bela").reason == "quote_not_found"
        )

    @pytest.mark.parametrize("quote", ["", " ", "\t", "\n", " ", " \n "])
    def test_empty_quote_unchanged(self, quote: str) -> None:
        assert locate_quote({"F1": WRAPPED}, fact_id="F1", quote=quote).reason == "empty_quote"

    def test_non_evidential_unchanged(self) -> None:
        assert locate_quote({"F1": "TOY: a .\n. b"}, fact_id="F1", quote=". .").reason == "non_evidential_quote"


class TestAmbiguity:
    def test_ambiguity_counted_on_collapsed_text(self) -> None:
        raw = "TOY: Arun left.\nThen Arun  left. Later Arun left."
        loc = locate_quote({"F1": raw}, fact_id="F1", quote="Arun left.")
        # strict finds 2 of the 3 spans as exact ("Arun left." twice); strict-first keeps today's count
        assert (loc.valid, loc.reason, loc.occurrences) == (False, "ambiguous_quote", 2)

    def test_collapsed_fallback_counts_whitespace_variants(self) -> None:
        raw = "TOY: Arun\nleft early. Then Arun   left early."
        loc = locate_quote({"F1": raw}, fact_id="F1", quote="Arun left early.")
        assert (loc.valid, loc.reason, loc.occurrences) == (False, "ambiguous_quote", 2)

    def test_single_collapsed_occurrence_is_valid(self) -> None:
        loc = locate_quote({"F1": "TOY: Arun\nleft early. Then he stayed."}, fact_id="F1", quote="Arun left early.")
        assert loc.valid and loc.occurrences == 1


class TestStrictFirst:
    def test_exact_match_marked_exact(self) -> None:
        loc = locate_quote({"F1": "TOY: Arun married Bela."}, fact_id="F1", quote="Arun married Bela")
        assert (loc.valid, loc.match, loc.start, loc.end) == (True, "exact", 5, 22)

    def test_strict_valid_results_identical_on_a_property_sweep(self) -> None:
        rnd = random.Random(876)
        words = ["Arun", "Bela", "struck", "left", "the", "house", "in", "2019", "she", "refused", "money", "."]
        seps = [" ", "  ", "\n", "\t", " ", " \n  "]
        n_checked = 0
        for _ in range(400):
            toks = [rnd.choice(words) for _ in range(rnd.randint(4, 14))]
            raw = "".join(t + rnd.choice(seps) for t in toks).strip()
            a = rnd.randrange(len(raw))
            b = rnd.randrange(a + 1, len(raw) + 1)
            q = raw[a:b]
            ref = _strict_reference({"F1": raw}, q)
            if q.strip() == "" or sum(c.isalnum() for c in q) < 2:
                continue
            loc = locate_quote({"F1": raw}, fact_id="F1", quote=q)
            if ref[0] in ("ok", "ambiguous_quote"):  # strict found it: result must not move
                assert (loc.reason, loc.start, loc.end, loc.occurrences) == ref, (raw, q)
                n_checked += 1
        assert n_checked > 200

    def test_no_new_valid_when_collapsed_text_is_absent(self) -> None:
        rnd = random.Random(8760)
        for _ in range(300):
            raw = " ".join(rnd.choice(["a1", "b2", "c3", "d4"]) + rnd.choice(["", "\n", "\t"]) for _ in range(8))
            q = " ".join(rnd.choice(["a1", "b2", "c3", "d4", "zz"]) for _ in range(3))
            loc = locate_quote({"F1": raw}, fact_id="F1", quote=q)
            if loc.valid:
                assert _collapse(q) in _collapse(raw) and _collapse(raw[loc.start : loc.end]) == _collapse(q)


class TestMatchField:
    def test_failed_results_carry_match_none(self) -> None:
        f = {"F1": WRAPPED}
        for q in ("Arun struck Bela every day", "", "x", None):
            loc = locate_quote(f, fact_id="F1", quote=q)
            assert not loc.valid and loc.match == "none"
        assert locate_quote(f, fact_id="F9", quote="Arun").match == "none"
        assert locate_quote(f, fact_id=None, quote="Arun").match == "none"

    def test_ambiguity_keeps_the_route_that_found_it(self) -> None:
        assert locate_quote({"F1": "TOY: Arun left. Arun left."}, fact_id="F1", quote="Arun left.").match == "exact"
        assert (
            locate_quote({"F1": "TOY: Arun\nleft. Arun  left."}, fact_id="F1", quote="Arun left.").match == "whitespace_collapsed"
        )
