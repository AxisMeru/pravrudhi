"""Tests for scripts/india_code_extract.py's PDF-text-to-section-boundary parser.

The real indiacode.gov.in Bare Act PDFs (verified during development against the actual
fetched files, not invented) mix in: a per-page "IndiaCode" watermark split across separate
lines ("In" / "di" / "aC" / "od" / "e"), an "ARRANGEMENT OF SECTIONS" index that repeats every
section number/title WITHOUT a heading-body dash before the real body does, footnotes that
also start with a bare "<digit>." but are never followed by a dash, section headings that
wrap onto a second line before the dash appears, and both a single em dash (`—`) and a
doubled en dash (`––`) used as the heading/body separator depending on the Act. This
fixture reproduces all of that at a small scale.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from india_code_extract import (  # noqa: E402
    dedupe_ambiguous_sections,
    find_body_start,
    split_sections,
    strip_watermark,
)

_WATERMARKED = """THE TEST ACT, 2020

In

di

aC

od

e
Some real body line one.
"""


def test_strip_watermark_removes_only_the_five_known_fragment_lines():
    out = strip_watermark(_WATERMARKED)
    assert "In" not in out.splitlines()
    assert "di" not in out.splitlines()
    assert "aC" not in out.splitlines()
    assert "od" not in out.splitlines()
    assert "e" not in out.splitlines()
    assert "Some real body line one." in out
    assert "THE TEST ACT, 2020" in out


def test_strip_watermark_keeps_a_genuine_short_line():
    text = "or\nof\nthe\nby"
    assert strip_watermark(text) == text


_FULL_TEXT = """THE TEST ACT, 2020

ARRANGEMENT OF SECTIONS

PRELIMINARY
1. Short title.
2. Interpretation-clause.
19. Voidability.
19A. Power to set aside.
20. Mistake of fact.

ACT NO. 7 OF 2020
[1st January, 2020.]
Preamble—WHEREAS it is expedient;
It is hereby enacted as follows:—
PRELIMINARY
1. Short title.—This Act may be called the Test Act, 2020.
2. Interpretation-clause.—In this Act unless the context otherwise requires,—
(a) proposal means an offer;
(b) promisor means the person making it.

1. For the report on which this Act was based, see the Gazette, 1919.
2. Subs. by Act 3 of 1951, s. 3, for "except Part B States."

3.Communication, acceptance and revocation—The communication of a proposal is complete
when it comes to the knowledge of the person to whom it is made.
19. Voidability of agreements without free consent.—Explained below.
19A. Power to set aside contract induced by undue
influence.—A court may so order.
20. Agreement void where both parties are under a
mistake as to a matter of fact.––Void in that case.
[21. Effect of mistakes as to law.—A bracketed, amendment-substituted section.

SCHEDULE
1. Form of charge.
2. Form of summons.
"""


def test_find_body_start_skips_the_arrangement_of_sections_index():
    idx = find_body_start(_FULL_TEXT)
    body = _FULL_TEXT[idx:]
    assert body.startswith("ACT NO. 7 OF 2020")


def test_split_sections_ignores_footnotes_that_lack_a_heading_dash():
    body = _FULL_TEXT[find_body_start(_FULL_TEXT):]
    sections = split_sections(body)
    numbers = [s["number"] for s in sections]
    assert numbers == ["1", "2", "3", "19", "19A", "20", "21"]


def test_split_sections_strips_a_leading_amendment_bracket_from_the_number():
    # Some genuinely-in-force sections are typeset "[<number>. <heading>...—<body>" with
    # a leading "[" marking text substituted by a later amending Act -- verified against the
    # real Indian Contract Act PDF (sections 16, 19A and 178 all use this convention there).
    # A naive "must start with a digit" scan silently drops these as non-sections.
    body = _FULL_TEXT[find_body_start(_FULL_TEXT):]
    sections = {s["number"]: s for s in split_sections(body)}
    assert "A bracketed, amendment-substituted section." in sections["21"]["text"]


def test_split_sections_extracts_heading_and_body_across_both_dash_styles():
    body = _FULL_TEXT[find_body_start(_FULL_TEXT):]
    sections = {s["number"]: s for s in split_sections(body)}

    sec1 = sections["1"]
    assert sec1["title"] == "Short title."
    assert "Test Act, 2020" in sec1["text"]

    sec20 = sections["20"]  # heading wraps a line AND uses the doubled en-dash style
    assert "matter of fact" in sec20["title"]
    assert "Void in that case." in sec20["text"]


def test_split_sections_handles_a_multi_line_wrapped_heading():
    body = _FULL_TEXT[find_body_start(_FULL_TEXT):]
    sections = {s["number"]: s for s in split_sections(body)}
    sec19a = sections["19A"]
    assert "undue" in sec19a["title"] and "influence" in sec19a["title"]
    assert "A court may so order." in sec19a["text"]


def test_split_sections_stops_before_a_schedule_that_restarts_numbering():
    body = _FULL_TEXT[find_body_start(_FULL_TEXT):]
    sections = split_sections(body)
    numbers = [s["number"] for s in sections]
    assert "Form of charge" not in " ".join(s["title"] for s in sections)
    assert numbers[-1] == "21"  # nothing from the Schedule's own "1./2." leaks in


def test_dedupe_ambiguous_sections_drops_a_colliding_number_on_both_sides():
    # Real example, found by hand in the fetched Indian Penal Code PDF: a Jammu & Kashmir and
    # Ladakh state-amendment block inserts its own "354E. Sextortion" text, which happens to
    # collide with the central Act's own, unrelated, later section 354E. Neither side of a
    # genuine collision like this should be silently kept as "the" text for that section
    # number -- both are dropped and the collision is reported instead.
    sections = [
        {"number": "1", "title": "Short title.", "text": "..."},
        {"number": "354E", "title": "Sextortion.", "text": "state amendment text"},
        {"number": "354E", "title": "Liability of person present.", "text": "central act text"},
        {"number": "355", "title": "Assault.", "text": "..."},
    ]
    clean, excluded = dedupe_ambiguous_sections(sections)
    numbers = [s["number"] for s in clean]
    assert "354E" not in numbers
    assert numbers == ["1", "355"]
    assert len(excluded) == 1
    assert excluded[0]["number"] == "354E"
    assert excluded[0]["titles"] == ["Sextortion.", "Liability of person present."]


def test_dedupe_ambiguous_sections_is_a_no_op_when_nothing_collides():
    sections = [
        {"number": "1", "title": "A", "text": "x"},
        {"number": "2", "title": "B", "text": "y"},
    ]
    clean, excluded = dedupe_ambiguous_sections(sections)
    assert clean == sections
    assert excluded == []
