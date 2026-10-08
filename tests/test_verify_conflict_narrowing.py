"""#823 verifier fix (Lead-2, 8 Oct): a CONFLICT is kept only while at least two candidates survive the name AND year tests
and the exact quote is not found in exactly one of them. NEW constructed fixtures only (invented parties and passages)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from pravrudhi.application.case_index import CaseRecord, insert_case, open_index
from pravrudhi.application.verify import VerifyResult, verify

KEY = "(1990) 2 SCC 100"
QUOTE_A = "the zorbatha clause binds only the original lessee and not his assigns"
QUOTE_B = "a mendelsohn covenant survives the termination of the head lease"


def _case(conn: sqlite3.Connection, cid: str, title: str, year: int, text: str) -> None:
    record = CaseRecord(case_id=cid, title=title, court="Supreme Court", year=year, source="t", path_or_url=f"/{cid}", text=text)
    insert_case(conn, record)


def _citer(conn: sqlite3.Connection, cid: str, parties: str, key: str = KEY) -> None:
    _case(conn, cid, f"Citer {cid}", 2015, f"This Court in {parties} {key} held that")


@pytest.fixture
def db(tmp_path: Path) -> sqlite3.Connection:
    conn = open_index(tmp_path / "narrow.sqlite3")
    _case(conn, "A", "Zorbatha Mendelsohn vs Quillfeather Aerospace", 1990, f"Held: {QUOTE_A}.")
    _case(conn, "B", "Brandolini Vexworth vs Thistledown Estates", 1990, f"Held: {QUOTE_B}.")
    # one citation key mined for two different party pairs (a real conflict in the alias table)
    _citer(conn, "c1", "Zorbatha Mendelsohn v. Quillfeather Aerospace")
    _citer(conn, "c2", "Brandolini Vexworth v. Thistledown Estates")
    conn.commit()
    yield conn
    conn.close()


def test_names_typed_in_the_citation_text_pick_the_group(db: sqlite3.Connection) -> None:
    cite = f"Zorbatha Mendelsohn v. Quillfeather Aerospace, {KEY}"
    assert verify(db, cite, QUOTE_A) == VerifyResult.VERIFIED
    # the named case does not hold B's quote
    assert verify(db, cite, QUOTE_B) == VerifyResult.EXISTS_QUOTE_NOT_FOUND
    # names that agree with no alias group: nothing survives the name test, so the CONFLICT stays (#331)
    assert verify(db, f"Nobody Known v. Somebody Else, {KEY}", QUOTE_A) == VerifyResult.CONFLICT


def test_bare_key_with_the_quote_in_exactly_one_candidate_is_not_a_conflict(db: sqlite3.Connection) -> None:
    assert verify(db, KEY, QUOTE_A) == VerifyResult.VERIFIED
    assert verify(db, KEY, QUOTE_B) == VerifyResult.VERIFIED


def test_a_quote_in_neither_or_in_both_candidates_stays_a_conflict(db: sqlite3.Connection) -> None:
    assert verify(db, KEY, "words that are in neither judgment") == VerifyResult.CONFLICT
    shared = "the court weighed the equities between the parties"
    db.execute("UPDATE cases SET text = text || ?", (" " + shared,))
    db.commit()
    assert verify(db, KEY, shared) == VerifyResult.CONFLICT


def test_an_empty_quote_never_resolves_a_conflict(db: sqlite3.Connection) -> None:
    assert verify(db, KEY, "") == VerifyResult.CONFLICT
    assert verify(db, KEY, "   ") == VerifyResult.CONFLICT
    db.execute("UPDATE cases SET year = 1950 WHERE case_id = 'B'")  # one candidate survives the year test
    db.commit()
    assert verify(db, KEY, "") == VerifyResult.CONFLICT  # still no resolution without a quote


def test_the_year_test_removes_a_group_and_the_survivor_is_checked(db: sqlite3.Connection) -> None:
    db.execute("UPDATE cases SET year = 1950 WHERE case_id = 'B'")
    db.commit()
    assert verify(db, KEY, QUOTE_A) == VerifyResult.VERIFIED
    assert verify(db, KEY, QUOTE_B) == VerifyResult.EXISTS_QUOTE_NOT_FOUND  # B is out of year: only A is checked


def test_a_surviving_group_that_resolves_to_no_case_keeps_the_conflict(db: sqlite3.Connection) -> None:
    _citer(db, "c3", "Glimmerfold Harrowgate v. Ottoline Pennyworth")  # a third alias group with no case in the index
    db.commit()
    assert verify(db, KEY, QUOTE_A) == VerifyResult.CONFLICT  # an uncheckable candidate survives (#331)
    # once the name test removes it, the quote can decide again
    assert verify(db, f"Zorbatha Mendelsohn v. Quillfeather Aerospace, {KEY}", QUOTE_A) == VerifyResult.VERIFIED


def test_no_group_surviving_keeps_the_conflict(db: sqlite3.Connection) -> None:
    db.execute("UPDATE cases SET year = 1950")
    db.commit()
    assert verify(db, KEY, QUOTE_A) == VerifyResult.CONFLICT  # every case is out of year


def test_a_non_scc_or_unknown_key_is_unchanged(db: sqlite3.Connection) -> None:
    assert verify(db, "(1991) 9 SCC 999", QUOTE_A) == VerifyResult.NOT_IN_INDEX
    assert verify(db, "AIR 1990 SC 100", QUOTE_A) == VerifyResult.NOT_IN_INDEX
