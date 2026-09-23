"""Tests for `application.case_index.verify` (P3): resolve a citation against the index, then check a
quoted claim against the resolved case's own text.

Scope, stated up front rather than silently: `verify` currently resolves only SCC-family citations
("(year) N SCC page"), because `mine_aliases` (case_index.py) only mines that one "party v. party, (year) N
SCC page" shape from judgment text. AIR/SCC OnLine/INSC/SCR/HC-neutral citations correctly come back
NOT_IN_INDEX today -- an honest "we have no alias evidence for this", never "fake" -- rather than a silent
false negative dressed up as a real check. Widening the alias miner to the other reporter families is
tracked as follow-up, not done here.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from pravrudhi.application.case_index import CaseRecord, insert_case, open_index
from pravrudhi.application.verify import VerifyResult, verify

# MINED: opennyaiorg/InJudgements shard0 `Text` column (same source as test_case_index.py's alias fixture).
_CITING_TEXT = (
    "This Court in Narandas Karsondas v. S.A. Kamtam and Anr. (1977) 3 SCC 247 clarified the "
    "position on specific performance and time being of the essence in a contract for sale."
)

# A real-shaped SC judgment document: title from the corpus's own filename convention
# (`<Parties>_on_<day>_<Month>_<year>_<n>.PDF` -> "Narandas Karsondas vs S A Kamtam ..."), body text
# containing the actual holding a citing document might quote.
_RESOLVED_CASE_TEXT = (
    "In a suit for specific performance, time is not ordinarily of the essence of the contract "
    "for sale of immovable property unless the parties expressly intend it to be so."
)


@pytest.fixture
def db(tmp_path: Path) -> sqlite3.Connection:
    conn = open_index(tmp_path / "verify_index.sqlite3")
    # The citing document -- this is what populates citation_aliases via insert_case's mine_aliases call.
    insert_case(
        conn,
        CaseRecord(
            case_id="citer1",
            title="Some Later Case v Someone Else",
            court="Supreme Court",
            year=2005,
            source="sc_pdf",
            path_or_url="/fake/citer.pdf",
            text=_CITING_TEXT,
        ),
    )
    # The resolved case itself -- title matches the party names from the alias, as it would for a real SC
    # PDF (filename-derived title "Narandas Karsondas vs S A Kamtam ...").
    insert_case(
        conn,
        CaseRecord(
            case_id="resolved1",
            title="Narandas Karsondas vs S A Kamtam and Anr",
            court="Supreme Court",
            year=1977,
            source="sc_pdf",
            path_or_url="/fake/narandas.pdf",
            text=_RESOLVED_CASE_TEXT,
        ),
    )
    conn.commit()
    yield conn
    conn.close()


def test_verified_when_case_resolves_and_quote_matches(db: sqlite3.Connection) -> None:
    result = verify(db, "(1977) 3 SCC 247", "time is not ordinarily of the essence of the contract")
    assert result == VerifyResult.VERIFIED


def test_exists_quote_not_found_when_case_resolves_but_quote_absent(db: sqlite3.Connection) -> None:
    result = verify(db, "(1977) 3 SCC 247", "this sentence never appears in that judgment at all")
    assert result == VerifyResult.EXISTS_QUOTE_NOT_FOUND


def test_not_in_index_when_citation_has_no_alias_evidence(db: sqlite3.Connection) -> None:
    result = verify(db, "(1994) 3 SCC 569", "any proposition")
    assert result == VerifyResult.NOT_IN_INDEX


def test_not_in_index_for_reporter_family_alias_mining_does_not_cover(db: sqlite3.Connection) -> None:
    # AIR is a real, parseable citation (citations.py handles it) but mine_aliases only covers SCC-shaped
    # cites -- honestly NOT_IN_INDEX, never "fake", for a real gap in coverage.
    result = verify(db, "AIR 1958 SC 398", "any proposition")
    assert result == VerifyResult.NOT_IN_INDEX


def test_malformed_when_citation_does_not_parse(db: sqlite3.Connection) -> None:
    result = verify(db, "not a citation at all", "any proposition")
    assert result == VerifyResult.MALFORMED


def test_malformed_when_multiple_citations_in_one_string(db: sqlite3.Connection) -> None:
    # verify() takes exactly one citation -- an input carrying two is a caller error, not a NOT_IN_INDEX.
    result = verify(db, "AIR 1958 SC 398 and (1977) 3 SCC 247", "any proposition")
    assert result == VerifyResult.MALFORMED


def test_conflict_when_alias_maps_citation_to_two_different_party_pairs(db: sqlite3.Connection) -> None:
    # A second, different citing document attributes the SAME citation to different parties -- real
    # data-integrity signal, not silently resolved to either one.
    insert_case(
        db,
        CaseRecord(
            case_id="citer2",
            title="A Different Later Case",
            court="Supreme Court",
            year=2010,
            source="sc_pdf",
            path_or_url="/fake/citer2.pdf",
            text="This Court in Totally Different Party v. Another Stranger (1977) 3 SCC 247 held that",
        ),
    )
    db.commit()
    result = verify(db, "(1977) 3 SCC 247", "any proposition")
    assert result == VerifyResult.CONFLICT
