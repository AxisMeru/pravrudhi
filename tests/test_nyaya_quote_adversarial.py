"""Adversarial tests of the Nyaya quote check (`nyaya_quote.locate_quote`): the `quote == facts[s:e]` guarantee.

Branch tag/night-harness-failopen, night of 2026-09-30. Tag's claims, pending verification: every finding these
tests support is Tag's claim until a second reviewer reproduces it.

Everything here is CONSTRUCTED text (hand-written toy strings and generated strings). No case material, no
evaluation set, no sealed capture, and no T3-T9 text is used as an input anywhere in this file. The judges are
test doubles and live only in this file.

Two questions are asked of the check:

1. Can a quote pass without appearing in the named fact? (`TestNoQuoteSurvivesMutation`, `TestUnicodeAttacks`,
   `TestProperties`)
2. Is the check symmetric, i.e. does it normalise BOTH sides the same way (here: neither side)? An asymmetry,
   where only the quote or only the fact is folded, is a defect in itself. (`TestNormalisationSymmetry`, both
   directions, every transform.)

Tests named `test_DEFECT_*` assert the DESIRED behaviour and are `xfail(strict=True)`: they fail on the current
code (that is the reproduction) and turn into a hard failure the moment the code is fixed, so the marker must
then be removed. Tests without the marker pin behaviour that is correct today.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Callable

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from pravrudhi.application.nyaya_quote import check_judgment, locate_quote

# -- the transforms an attacker (or a sloppy model) might apply to ONE side only -----------------------------

_HOMOGLYPHS = str.maketrans({"a": "а", "e": "е", "o": "о", "p": "р", "c": "с", "x": "х"})
_ZW = "​‌‍⁠﻿­"


def _strip_zero_width(s: str) -> str:
    return "".join(ch for ch in s if ch not in _ZW)


def _collapse_ws(s: str) -> str:
    return " ".join(s.split())


def _fullwidth(s: str) -> str:
    return "".join(chr(ord(c) + 0xFEE0) if "!" <= c <= "~" else c for c in s)


TRANSFORMS: dict[str, Callable[[str], str]] = {
    "nfc": lambda s: unicodedata.normalize("NFC", s),
    "nfd": lambda s: unicodedata.normalize("NFD", s),
    "nfkc": lambda s: unicodedata.normalize("NFKC", s),
    "nfkd": lambda s: unicodedata.normalize("NFKD", s),
    "casefold": str.casefold,
    "upper": str.upper,
    "strip_zero_width": _strip_zero_width,
    "collapse_whitespace": _collapse_ws,
    "homoglyph_cyrillic": lambda s: s.translate(_HOMOGLYPHS),
    "fullwidth": _fullwidth,
    "insert_zwsp_mid": lambda s: s[: len(s) // 2] + "​" + s[len(s) // 2 :],
    "nbsp_for_space": lambda s: s.replace(" ", " "),
}

# Constructed facts chosen so that every transform above changes at least one of them.
FACTS = [
    "TOY: Zoé said the café was closed on Monday.",  # NFD + NFC accents mixed
    "TOY:  Two  spaces\tand a tab\nand a newline.",  # runs of whitespace
    "TOY: the ligature ﬁnal and the fullwidth ＡBC and the ceiling ⅕ cup.",  # compat forms
    "TOY: apple, pepper, cocoa and an exact copy.",  # latin letters that have Cyrillic twins
    "TOY: zero​width and soft­hyphen and a word⁠joiner and a ﻿BOM.",  # invisibles in the FACT
    "TOY: emoji \U0001f600 and a clef \U0001d11e after astral characters, then plain tail.",  # astral plane
]


def _quotes_of(fact: str) -> list[str]:
    """A few substrings of `fact` (prefix, middle, tail, whole) -- all verbatim by construction."""
    n = len(fact)
    return [fact[:10], fact[n // 4 : n // 2 + 3], fact[-12:], fact]


class TestNormalisationSymmetry:
    """The check must normalise neither side, or both identically. `locate_quote` is exact `str.find`, so the
    oracle is: valid  <=>  quote is non-empty and a literal substring of the fact. Both directions are tested:
    (a) the QUOTE is transformed, the fact left alone; (b) the FACT is transformed, the quote left alone."""

    @pytest.mark.parametrize("name", sorted(TRANSFORMS))
    def test_transform_applied_to_quote_only_is_never_silently_folded(self, name: str) -> None:
        t = TRANSFORMS[name]
        changed = rejected = 0
        for fact in FACTS:
            for q in _quotes_of(fact):
                tq = t(q)
                res = locate_quote({"F1": fact}, fact_id="F1", quote=tq)
                assert res.valid == (tq != "" and tq in fact), (name, q, tq)
                if res.valid:
                    assert fact[res.start : res.end] == tq
                if tq != q:
                    changed += 1
                    if not res.valid:
                        rejected += 1
        assert changed > 0, f"transform {name} never changed a quote -- the test would be vacuous"
        # At least some changed quotes must have been REJECTED, else the check is folding the quote side.
        assert rejected > 0, f"transform {name}: every changed quote still passed -- quote side is being folded"

    @pytest.mark.parametrize("name", sorted(TRANSFORMS))
    def test_transform_applied_to_fact_only_is_never_silently_folded(self, name: str) -> None:
        t = TRANSFORMS[name]
        changed = rejected = 0
        for fact in FACTS:
            tf = t(fact)
            for q in _quotes_of(fact):
                res = locate_quote({"F1": tf}, fact_id="F1", quote=q)
                assert res.valid == (q != "" and q in tf), (name, q)
                if res.valid:
                    assert tf[res.start : res.end] == q
                if tf != fact:
                    changed += 1
                    if not res.valid:
                        rejected += 1
        assert changed > 0, f"transform {name} never changed a fact -- the test would be vacuous"
        assert rejected > 0, f"transform {name}: every quote still passed -- fact side is being folded"

    @pytest.mark.parametrize("name", sorted(TRANSFORMS))
    def test_transform_applied_to_both_sides_equals_plain_substring_test(self, name: str) -> None:
        # Applying the SAME transform to both sides is what a symmetric normaliser would do. The check does not
        # do it (no normalisation), so the result must equal the plain substring test on the transformed pair.
        t = TRANSFORMS[name]
        for fact in FACTS:
            for q in _quotes_of(fact):
                res = locate_quote({"F1": t(fact)}, fact_id="F1", quote=t(q))
                assert res.valid == (t(q) != "" and t(q) in t(fact))

    def test_nfc_quote_against_nfd_fact_and_the_reverse_are_both_rejected(self) -> None:
        nfc, nfd = "café", "café"
        assert nfc != nfd and unicodedata.normalize("NFC", nfd) == nfc
        assert not locate_quote({"F1": f"TOY: {nfd} open"}, fact_id="F1", quote=nfc).valid  # quote NFC, fact NFD
        assert not locate_quote({"F1": f"TOY: {nfc} open"}, fact_id="F1", quote=nfd).valid  # quote NFD, fact NFC
        assert locate_quote({"F1": f"TOY: {nfc} open"}, fact_id="F1", quote=nfc).valid
        assert locate_quote({"F1": f"TOY: {nfd} open"}, fact_id="F1", quote=nfd).valid


class TestUnicodeAttacks:
    def test_homoglyph_quote_is_rejected(self) -> None:
        fact = "TOY: the accused took the apple."
        forged = fact.replace("a", "а", 1)  # Cyrillic a in place of the first Latin a
        assert forged != fact
        res = locate_quote({"F1": fact}, fact_id="F1", quote=forged)
        assert not res.valid and res.reason == "quote_not_found"

    def test_zero_width_inserted_into_quote_or_fact_is_rejected_both_ways(self) -> None:
        plain, zw = "TOY: the accused took it.", "TOY: the acc​used took it."
        assert not locate_quote({"F1": plain}, fact_id="F1", quote=zw).valid
        assert not locate_quote({"F1": zw}, fact_id="F1", quote=plain).valid

    def test_bidi_override_and_soft_hyphen_are_not_ignored(self) -> None:
        fact = "TOY: pay the sum now."
        for mark in ("‮", "‭", "‏", "­", "؜"):
            assert not locate_quote({"F1": fact}, fact_id="F1", quote=fact.replace("sum", "s" + mark + "um")).valid

    def test_quote_cannot_straddle_two_facts(self) -> None:
        facts = {"F1": "TOY: the first fact ends here", "F2": "and the second begins there"}
        res = locate_quote(facts, fact_id="F1", quote="ends here and the second")
        assert not res.valid
        # a real substring of F2 cited against F1 is also refused (no nearest-fact rescue)
        assert locate_quote(facts, fact_id="F1", quote="second begins").reason == "quote_not_found"

    def test_astral_characters_offsets_are_codepoints_and_slice_back_exactly(self) -> None:
        fact = "a\U0001f600b\U0001d11ec"
        res = locate_quote({"F1": fact}, fact_id="F1", quote="\U0001d11e")
        assert res.valid and (res.start, res.end) == (3, 4)
        assert fact[res.start : res.end] == "\U0001d11e"

    def test_surrogate_pair_halves_do_not_match_the_astral_character(self) -> None:
        astral = "x\U0001f600y"
        hi, lo = "\ud83d", "\ude00"
        assert not locate_quote({"F1": astral}, fact_id="F1", quote=hi).valid  # lone high surrogate
        assert not locate_quote({"F1": astral}, fact_id="F1", quote=lo).valid  # lone low surrogate
        assert not locate_quote({"F1": astral}, fact_id="F1", quote=hi + lo).valid  # the pair as two code points
        # and the reverse direction: a fact that holds the two halves separately (a broken decoder's output)
        assert not locate_quote({"F1": "x" + hi + lo + "y"}, fact_id="F1", quote="\U0001f600").valid

    def test_the_three_offset_units_disagree_after_an_astral_character(self) -> None:
        # Characterises the hazard behind DEFECT H-12: `start`/`end` are Python code-point indices. A consumer that
        # slices by UTF-16 code units (JavaScript) or UTF-8 bytes (storage, Rust, Go) lands on a different span.
        fact = "\U0001f600\U0001f600 needle"
        res = locate_quote({"F1": fact}, fact_id="F1", quote="needle")
        assert res.valid and res.start == 3
        utf16_index = len(fact[: res.start].encode("utf-16-le")) // 2
        utf8_index = len(fact[: res.start].encode("utf-8"))
        assert (res.start, utf16_index, utf8_index) == (3, 5, 9)
        assert fact.encode("utf-8")[res.start : res.start + 6] != b"needle"  # byte-slicing by the code-point offset
        js_slice = fact.encode("utf-16-le")[res.start * 2 : res.start * 2 + 12].decode("utf-16-le", errors="replace")
        assert js_slice != "needle"  # JavaScript's fact.slice(start, end) with the same numbers

    def test_leading_whitespace_offsets_are_relative_to_the_stripped_fact(self) -> None:
        # Characterises DEFECT H-12's second half: `ingest_facts` strips, so offsets are into the STRIPPED text,
        # and the response carries no hint of how much was trimmed.
        from pravrudhi.application.nyaya_agent import ingest_facts

        raw = "\n\n   TOY: the needle is here."
        fact = ingest_facts([raw])[0]
        res = locate_quote({fact.id: fact.text}, fact_id=fact.id, quote="needle")
        assert res.valid
        assert raw[res.start : res.end] != "needle"  # caller slicing its ORIGINAL text is off by the trim
        assert fact.text[res.start : res.end] == "needle"


class TestNoQuoteSurvivesMutation:
    """Mutate a known-good quote in every elementary way; none may be accepted unless it is still a substring."""

    FACT = "TOY: Kiran told Lata he would marry her in the spring."
    GOOD = "he would marry her"

    @pytest.mark.parametrize(
        "mutant",
        [
            GOOD.upper(), GOOD.title(), GOOD + "!", " " + GOOD + " ", GOOD.replace(" ", "  "),
            GOOD.replace(" ", " "), GOOD.replace("e", "е", 1), GOOD[:-1], GOOD[1:] + "x",
            GOOD + "​", "​" + GOOD, GOOD.replace("would", "will"), "he  would marry her",
            GOOD.replace(" ", "\n"), "".join(reversed(GOOD)),
        ],
    )
    def test_mutated_quote_is_refused(self, mutant: str) -> None:
        res = locate_quote({"F1": self.FACT}, fact_id="F1", quote=mutant)
        assert res.valid == (mutant in self.FACT)

    def test_unmutated_quote_is_accepted_with_system_offsets(self) -> None:
        res = locate_quote({"F1": self.FACT}, fact_id="F1", quote=self.GOOD)
        assert res.valid and res.offsets_source == "system"
        assert self.FACT[res.start : res.end] == self.GOOD

    def test_judge_supplied_offsets_are_ignored(self) -> None:
        judgment = {"status": "established", "fact_id": "F1", "quote": self.GOOD, "start": 0, "end": 3}
        res = check_judgment({"F1": self.FACT}, judgment)
        assert res.valid and self.FACT[res.start : res.end] == self.GOOD and (res.start, res.end) != (0, 3)

    @pytest.mark.parametrize("fact_id", ["f1", "F01", " F1", "F1 ", "F1​", "Ｆ１", "F1́", "", "F2"])
    def test_lookalike_fact_ids_are_unknown_not_nearest(self, fact_id: str) -> None:
        res = locate_quote({"F1": self.FACT}, fact_id=fact_id, quote=self.GOOD)
        assert not res.valid and res.reason == "unknown_fact"

    @pytest.mark.parametrize("bad", [1, b"abc", ["F1"], ("F1",), 1.5, None])
    def test_non_string_quote_never_validates(self, bad: object) -> None:
        # A non-str quote either raises (TypeError, fail closed) or is reported as absent -- never valid.
        try:
            res = locate_quote({"F1": self.FACT}, fact_id="F1", quote=bad)  # type: ignore[arg-type]
        except TypeError:
            return
        assert not res.valid

    @pytest.mark.parametrize("bad_id", [1, ["F1"], ("F1",), b"F1"])
    def test_non_string_fact_id_never_validates(self, bad_id: object) -> None:
        try:
            res = locate_quote({"F1": self.FACT}, fact_id=bad_id, quote=self.GOOD)  # type: ignore[arg-type]
        except TypeError:
            return  # unhashable id raises -- fail closed
        assert not res.valid

    def test_check_judgment_only_accepts_the_exact_established_status(self) -> None:
        for status in ("Established", "ESTABLISHED", "established ", "yes", None, ""):
            res = check_judgment({"F1": self.FACT}, {"status": status, "fact_id": "F1", "quote": self.GOOD})
            assert not res.valid and res.reason == "not_established"
        assert not check_judgment({"F1": self.FACT}, {"fact_id": "F1", "quote": self.GOOD}).valid  # status absent


class TestDegenerateQuotes:
    """The check proves the quote APPEARS. It does not prove the quote says anything. DEFECT H-05."""

    @pytest.mark.parametrize(
        "fact, quote",
        [
            ("TOY: one two  three", " "),  # a single space
            ("TOY: one\ntwo", "\n"),  # a bare newline
            ("TOY: one two", "e"),  # one letter
            ("TOY: one, two.", ","),  # one punctuation mark
            ("TOY: hidden​mark", "​"),  # an INVISIBLE quote: nothing to read
            ("TOY: Zoé", "́"),  # a lone combining mark (half a grapheme)
            ("TOY: a   b", "   "),  # run of spaces
        ],
    )
    @pytest.mark.xfail(strict=True, reason="DEFECT H-05: whitespace/invisible/one-character quotes are accepted as evidence")
    def test_DEFECT_degenerate_quote_is_accepted_as_evidence(self, fact: str, quote: str) -> None:
        assert not locate_quote({"F1": fact}, fact_id="F1", quote=quote).valid

    def test_characterisation_current_behaviour_is_accept(self) -> None:
        assert locate_quote({"F1": "TOY: hidden​mark"}, fact_id="F1", quote="​").valid
        assert locate_quote({"F1": "TOY: one two"}, fact_id="F1", quote=" ").valid

    def test_empty_quote_is_still_refused(self) -> None:
        res = locate_quote({"F1": "TOY: anything"}, fact_id="F1", quote="")
        assert not res.valid and res.reason == "empty_quote"


class TestProperties:
    """Hypothesis properties over generated text, including the nasty alphabets."""

    NASTY = st.sampled_from(
        ["́", "​", "‍", "﻿", "­", "‮", "\U0001f600", "\U0001d11e", "\ud83d", "\ude00", "é",
         "é", "а", "a", " ", "\n", "\t", " ", "　", "\x00", "Ａ"]
    )
    TEXT = st.lists(st.one_of(st.text(max_size=4), NASTY), max_size=12).map("".join)

    @settings(max_examples=400, deadline=None)
    @given(fact=TEXT, quote=TEXT)
    def test_valid_iff_literal_nonempty_substring(self, fact: str, quote: str) -> None:
        res = locate_quote({"F1": fact}, fact_id="F1", quote=quote)
        assert res.valid == (quote != "" and quote in fact)
        if res.valid:
            assert fact[res.start : res.end] == quote
            assert res.end - res.start == len(quote)
            assert res.start == fact.find(quote)
            assert res.occurrences >= 1

    @settings(max_examples=300, deadline=None)
    @given(fact=TEXT, data=st.data())
    def test_every_real_substring_is_found_at_a_correct_offset(self, fact: str, data: st.DataObject) -> None:
        if not fact:
            return
        i = data.draw(st.integers(0, len(fact) - 1))
        j = data.draw(st.integers(i + 1, len(fact)))
        quote = fact[i:j]
        res = locate_quote({"F1": fact}, fact_id="F1", quote=quote)
        assert res.valid and fact[res.start : res.end] == quote and res.start <= i

    @settings(max_examples=300, deadline=None)
    @given(fact=TEXT, quote=TEXT, name=st.sampled_from(sorted(TRANSFORMS)))
    def test_no_hidden_folding_on_either_side(self, fact: str, quote: str, name: str) -> None:
        t = TRANSFORMS[name]
        for f, q in ((fact, t(quote)), (t(fact), quote), (t(fact), t(quote))):
            res = locate_quote({"F1": f}, fact_id="F1", quote=q)
            assert res.valid == (q != "" and q in f)
