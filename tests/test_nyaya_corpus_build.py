"""Tests for scripts/nyaya_corpus_build.py's single-page-work parser.

The Constitution of India is split across many Wikisource subpages (one per Part); the
Indian Evidence Act, 1872 is one single Wikisource page with internal h2 (Part) / h3
(Chapter) / h4 (Section) headings and inline mw-editsection "[edit]" markup that must not
leak into the extracted body text. This fixture reproduces that real structure (verified
against the live page during development, not invented) at a small scale.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from nyaya_corpus_build import parse_single_page_articles  # noqa: E402

_FIXTURE_HTML = """
<div class="mw-heading mw-heading2"><h2 id="PART_I_-_TEST">PART I - TEST</h2></div>
<div class="mw-heading mw-heading3"><h3 id="CHAPTER_I_-_Intro">CHAPTER I - Intro</h3></div>
<div class="mw-heading mw-heading4"><h4 id="1._Short_title">1. Short title</h4>\
<span class="mw-editsection"><span class="mw-editsection-bracket">[</span>\
<a href="/w/index.php?action=edit&amp;section=1"><span>edit</span></a>\
<span class="mw-editsection-bracket">]</span></span></div>
<p>This Act may be called the Test Act, 1900.</p>
<div class="mw-heading mw-heading4"><h4 id="2._x">2. [Repeal of enactment.] Rep. by Act X</h4>\
<span class="mw-editsection"><span class="mw-editsection-bracket">[</span>\
<a href="/w/index.php?action=edit&amp;section=2"><span>edit</span></a>\
<span class="mw-editsection-bracket">]</span></span></div>
<p>[Repeal of enactment.] Rep. by Act X, 1950.</p>
<div class="mw-heading mw-heading3"><h3 id="CHAPTER_II_-_More">CHAPTER II - More</h3></div>
<div class="mw-heading mw-heading4"><h4 id="3._Definitions">3. Definitions</h4>\
<span class="mw-editsection"><span class="mw-editsection-bracket">[</span>\
<a href="/w/index.php?action=edit&amp;section=3"><span>edit</span></a>\
<span class="mw-editsection-bracket">]</span></span></div>
<p>In this Act, unless the context otherwise requires—</p>
<p>“Court” includes all Judges.</p>
"""


def test_extracts_one_article_per_h4_section() -> None:
    articles = parse_single_page_articles("Test Act", "TA", _FIXTURE_HTML)
    assert [a.section for a in articles] == ["Section 1", "Section 2", "Section 3"]


def test_editsection_markup_never_leaks_into_body_or_title() -> None:
    articles = parse_single_page_articles("Test Act", "TA", _FIXTURE_HTML)
    sec1 = next(a for a in articles if a.section == "Section 1")
    assert "edit" not in sec1.text.lower()
    assert "edit" not in sec1.title.lower()
    assert sec1.text == "This Act may be called the Test Act, 1900."
    assert sec1.title == "Short title"


def test_part_and_chapter_tracked_across_sections() -> None:
    articles = parse_single_page_articles("Test Act", "TA", _FIXTURE_HTML)
    sec1 = next(a for a in articles if a.section == "Section 1")
    sec3 = next(a for a in articles if a.section == "Section 3")
    assert "PART I - TEST" in sec1.part and "CHAPTER I - Intro" in sec1.part
    assert "PART I - TEST" in sec3.part and "CHAPTER II - More" in sec3.part


def test_a_repealed_section_still_carries_its_real_note_as_text() -> None:
    articles = parse_single_page_articles("Test Act", "TA", _FIXTURE_HTML)
    sec2 = next(a for a in articles if a.section == "Section 2")
    assert "Rep. by Act X, 1950" in sec2.text


def test_ids_are_prefixed_and_act_name_is_set() -> None:
    articles = parse_single_page_articles("Test Act", "TA", _FIXTURE_HTML)
    assert all(a.id.startswith("TA/Section ") for a in articles)
    assert all(a.act == "Test Act" for a in articles)
