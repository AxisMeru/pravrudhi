"""Tests for `application.case_index` (P3). Extraction tests run against a handful of real files from the
corpora at `/home/ss/fusion-project/corpus-raw/` (skipped, not failed, if that path is absent -- it is a local
data mount, never shipped with the repo) rather than synthetic PDF bytes, per house rule: no fakes as evidence.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from pravrudhi.application.case_index import (
    CaseRecord,
    extract_pdf_text,
    insert_case,
    iter_sc_pdfs,
    lookup_alias,
    mine_aliases,
    open_index,
    search,
    title_and_year_from_filename,
)

SC_ROOT = Path("/home/ss/fusion-project/corpus-raw/supreme_court_judgments")

pytestmark_corpus = pytest.mark.skipif(not SC_ROOT.exists(), reason="corpus-raw not mounted on this host")


def _2024_pdfs() -> list[Path]:
    return [p for p in iter_sc_pdfs(SC_ROOT) if p.parent.name == "2024"]


@pytestmark_corpus
def test_extract_real_pdf_yields_text() -> None:
    pdfs = _2024_pdfs()
    assert pdfs, "expected real 2024 SC judgment PDFs"
    result = extract_pdf_text(pdfs[0])
    assert result.ok
    assert len(result.text) > 200


@pytestmark_corpus
def test_extract_coverage_over_small_real_sample() -> None:
    # A real coverage measurement, not asserted-and-forgotten: at least 90% of a same-year sample must yield
    # extractable text, since these are digitally-produced SC judgments, not scans.
    pdfs = sorted(_2024_pdfs())[:25]
    results = [extract_pdf_text(p) for p in pdfs]
    ok = sum(1 for r in results if r.ok)
    assert ok / len(results) >= 0.9, [r.error for r in results if not r.ok]


def test_title_and_year_from_filename() -> None:
    title, year = title_and_year_from_filename(
        Path("A_K_Gopalan_vs_The_State_Of_Madras_Union_Of_India__on_19_May_1950_1.PDF")
    )
    assert year == 1950
    assert "A K Gopalan" in title
    assert "State Of Madras" in title


def test_title_and_year_from_unrecognized_filename_falls_back() -> None:
    title, year = title_and_year_from_filename(Path("not_a_known_shape.PDF"))
    assert year is None
    assert title == "not a known shape"


# MINED: opennyaiorg/InJudgements shard0, `Text` column (see mine_alias.py, throwaway scratchpad script --
# not committed). Real "party v. party, (year) N SCC page" strings as they actually appear in judgment text.
_MINED_ALIAS_TEXT = (
    "Life Insurance Corporation of India v. Asha Goel reported in (2001) 2 SCC 160 held that "
    "This Court in Narandas Karsondas v. S.A. Kamtam and Anr. (1977) 3 SCC 247 clarified "
    "Pune Municipal Corporation and Anr. v. Harakchand Misirimal Solanki and Ors., (2014) 3 SCC 183 confirmed"
)


def test_mine_aliases_real_text() -> None:
    aliases = mine_aliases(_MINED_ALIAS_TEXT)
    citations = [a.citation for a in aliases]
    assert "(2001) 2 SCC 160" in citations
    assert "(1977) 3 SCC 247" in citations
    assert "(2014) 3 SCC 183" in citations
    first = aliases[0]
    assert first.party_1 == "Life Insurance Corporation of India"
    assert first.party_2 == "Asha Goel"


def test_mine_aliases_no_match_on_plain_text() -> None:
    assert mine_aliases("no citation-shaped alias appears here at all") == []


@pytest.fixture
def db(tmp_path: Path) -> sqlite3.Connection:
    conn = open_index(tmp_path / "test_index.sqlite3")
    yield conn
    conn.close()


def test_insert_and_search(db: sqlite3.Connection) -> None:
    insert_case(
        db,
        CaseRecord(
            case_id="abc123",
            title="A K Gopalan v State Of Madras",
            court="Supreme Court",
            year=1950,
            source="sc_pdf",
            path_or_url="/fake/path.pdf",
            text="preventive detention under Article 22 was upheld in this case about personal liberty",
        ),
    )
    db.commit()
    hits = search(db, "preventive detention")
    assert len(hits) == 1
    assert hits[0]["case_id"] == "abc123"
    assert hits[0]["title"] == "A K Gopalan v State Of Madras"


def test_insert_populates_alias_table(db: sqlite3.Connection) -> None:
    insert_case(
        db,
        CaseRecord(
            case_id="xyz789",
            title="some case",
            court="Supreme Court",
            year=2001,
            source="injudgements",
            path_or_url="https://example.invalid/doc/1",
            text=_MINED_ALIAS_TEXT,
        ),
    )
    db.commit()
    rows = lookup_alias(db, "Life Insurance Corporation of India", "Asha Goel")
    assert len(rows) == 1
    assert rows[0]["citation"] == "(2001) 2 SCC 160"
    assert rows[0]["case_id"] == "xyz789"


def test_search_no_match_returns_empty(db: sqlite3.Connection) -> None:
    assert search(db, "nonexistent_token_xyz") == []
