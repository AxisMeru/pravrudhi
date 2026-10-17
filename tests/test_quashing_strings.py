# ruff: noqa: E501
"""S3: every fixed string R1 ruled is pinned by its sha256[:16] (R1 reads the PR head against these hashes), the page emits nothing
else of its own, and the barred words never appear in an emitted string (the Court's quoted words are exempt by pin)."""

from __future__ import annotations

import hashlib
import json
from typing import Any

import pytest

from pravrudhi.application import quashing as G

H = {
    "STANDING_LINE": "46d50016151ae5e1",
    "RELIEF_LINE": "f3dd801c35012ef0",
    "HEADING": "683dee56ed8212b4",
    "ORDER_LINE": "ebfbf482bdb7e902",
    "CODE_NUMBERS_LINE": "fd8906620bf7ab78",
    "FACT_LINE": "a4720a2b6449eefa",
    "BRIDGE_GUARD": "cade39ddc88219e2",
    "CAUTION_LABEL": "f63e57ca34dc3e80",
    "OUR_LINE": "8c701726c8d34ad4",
    "QUOTES_LINE": "2d2495c6758dedc3",
    "ONE_SENTENCE": "454ead369e289852",
    "NOT_ASSESSED": "75b4e03d29b348e4",
    "PAPERS_CAPTION": "cf937c947c6a7f94",
    "UNAVAILABLE": "e8992d03171239e4",
    "SCREENING_AID": "726c9d239f783389",
    "DATA_STATEMENT": "f41bfcccc2285193",
}


def _h(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


@pytest.mark.parametrize(("name", "expected"), sorted(H.items()))
def test_each_ruled_string_has_the_ruled_hash(name: str, expected: str) -> None:
    assert _h(getattr(G, name)) == expected


def test_the_categories_line_is_the_ruled_template_with_the_year_from_the_row() -> None:
    rendered = G.CATEGORIES_LINE.format(year=2016)
    assert rendered == (
        "Categories: as set out in para 102 of State of Haryana v. Bhajan Lal, as quoted in Amanullah v. State of Bihar, decided in 2016, in the "
        "judgment text we hold. The text was copied in when this page was built; this service checks only that the case exists in its index."
    )
    assert _h(rendered) == "d03fe00fa6680b41"


def test_the_refusal_is_the_parity_wording_and_names_no_relief() -> None:
    assert G.REFUSAL == (
        "No agenda for this relief yet. For now this page covers quashing of an FIR or complaint under BNSS s.528 only. A lawyer reads the papers."
    )
    assert "{" not in G.REFUSAL


def test_the_zero_papers_line_is_the_ruled_pair() -> None:
    assert _h(G.papers_heading(0) + ". " + G.NO_PAPERS_CAPTION) == "7a65a3da11c1ef58"
    assert G.build_page([])["papers"]["caption"] == G.NO_PAPERS_CAPTION
    assert G.build_page([G.Doc("d" * 32, "a.pdf", 1, ())])["papers"]["caption"] == G.PAPERS_CAPTION


def test_the_papers_heading_is_the_ruled_form() -> None:
    assert G.papers_heading(0) == "Papers you added (0)" and G.papers_heading(12) == "Papers you added (12)"


def _strings(obj: Any) -> list[str]:
    if isinstance(obj, str):
        return [obj]
    if isinstance(obj, dict):
        return [s for v in obj.values() for s in _strings(v)]
    if isinstance(obj, list):
        return [s for v in obj for s in _strings(v)]
    return []


def test_no_emitted_string_outside_the_courts_quotes_holds_a_barred_word() -> None:
    page = G.build_page([G.Doc("d" * 32, "FIR copy.pdf", 3, ())])
    quoted = {page["stem"], page["caution"]["quote"], *[c["text"] for c in page["categories"]]}
    own = [
        s for s in _strings(page) if s not in quoted and s not in {d["name"] for d in page["papers"]["documents"]} and len(s) > 40
    ]
    assert own  # the page's own lines are scanned
    ruled = set(H.values()) | {"d03fe00fa6680b41"}  # R1 ruled these exact strings (some name a barred word only to deny it)
    unruled = [s for s in [*own, G.REFUSAL, G.STORE_OFF] if _h(s) not in ruled]
    assert unruled == [G.STORE_OFF] or set(unruled) <= {G.REFUSAL, G.STORE_OFF, G.SCREENING_AID, G.DATA_STATEMENT}
    for s in [*unruled, *own]:
        if _h(s) not in ruled:
            assert G.barred_in(s) == [], (s, G.barred_in(s))


@pytest.mark.parametrize("word", G.BARRED)
def test_each_barred_word_is_found_by_the_scan(word: str) -> None:
    assert G.barred_in(f"The page says {word} here.") == [word.lower()]


def test_the_old_strings_are_gone() -> None:
    page = json.dumps(G.build_page([]), ensure_ascii=False)
    for old in (
        "(a) to (g)",
        "as the indexed text gives them",
        "indexed text of State of Haryana v. Bhajan Lal",
        "Checked against the Bharatiya",
        "M. Narayandas",
        "corroborated",
        "single-source",
        "other copies",
        "grounds",
        "Not assessed from the papers.",
        "None of these categories has been assessed",
        "cut where the printed page changes",
        "21 November 1990",
        "SCC 335",
    ):
        assert old not in page, old


def test_the_quotes_line_contains_no_word_page() -> None:
    import re

    assert re.search(r"\bpage\b", G.QUOTES_LINE, re.I) is None


def test_a_paper_with_pages_that_have_no_text_is_listed_by_its_raw_name_and_claimed_as_nothing() -> None:
    nasty = 'scan "A&B" <1>.pdf'
    page = G.build_page([G.Doc("a" * 32, nasty, 4, (2, 3)), G.Doc("b" * 32, "typed.pdf", 1, ())])
    assert [d["name"] for d in page["papers"]["documents"]] == [nasty, "typed.pdf"]
    assert page["papers"]["heading"] == "Papers you added (2)" and page["papers"]["caption"] == "They are listed, not read."
    assert "pages_without_text" not in json.dumps(page) and "2, 3" not in json.dumps(page)
