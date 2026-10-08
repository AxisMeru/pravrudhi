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


# --- same-name sibling (R1 on #373): a different case with the SAME party names whose text holds the quote ---
QUOTE_S = "the sibling holding says a drawer's silence is not consent to an altered cheque"


@pytest.fixture
def sibling_db(tmp_path: Path) -> sqlite3.Connection:
    conn = open_index(tmp_path / "sibling.sqlite3")
    _case(conn, "A", "Zorbatha Mendelsohn vs Quillfeather Aerospace", 1990, f"Held: {QUOTE_A}.")
    # same names, six years later
    _case(conn, "S", "Zorbatha Mendelsohn vs Quillfeather Aerospace", 1996, f"Held: {QUOTE_S}.")
    _citer(conn, "c1", "Zorbatha Mendelsohn v. Quillfeather Aerospace")
    conn.commit()
    yield conn
    conn.close()


def test_same_name_sibling_documented_outcome_is_unchanged_by_the_fix(sibling_db: sqlite3.Connection) -> None:
    """ONE alias group (the party pair) resolves by title to BOTH same-named cases, and `verify()` checks the quote only against a
    case within a year of the citation (#717/#723). This is the behaviour on main BEFORE this fix (no CONFLICT is involved, so
    the narrowing never runs): a sibling SIX years away is never checked (its quote is not verified against the other's
    citation), but
    a same-name sibling WITHIN a year is checked, so its quote verifies: a documented limitation, the N3 near-miss the O3 runs do
    not test (#834)."""
    cite = f"Zorbatha Mendelsohn v. Quillfeather Aerospace, {KEY}"
    assert verify(sibling_db, cite, QUOTE_A) == VerifyResult.VERIFIED
    assert verify(sibling_db, cite, QUOTE_S) == VerifyResult.EXISTS_QUOTE_NOT_FOUND  # sibling six years away: not checked
    sibling_db.execute("UPDATE cases SET year = 1991 WHERE case_id = 'S'")
    sibling_db.commit()
    assert verify(sibling_db, cite, QUOTE_S) == VerifyResult.VERIFIED  # sibling within a year: documented limitation


def test_sibling_under_a_conflict_is_removed_by_the_year_test_when_it_is_years_away(db: sqlite3.Connection) -> None:
    """The fix's own sibling case: two alias groups (a real CONFLICT), the sibling's group names differ and its case is far
    in year, so the year test removes it and its quote is NOT verified against the other group's citation."""
    db.execute("UPDATE cases SET year = 1996 WHERE case_id = 'B'")
    db.commit()
    assert verify(db, KEY, QUOTE_B) == VerifyResult.EXISTS_QUOTE_NOT_FOUND  # B is out of year, only A is checked
    # within a year of the citation both stay candidates and the quote held by exactly one verifies (Lead-2's rule)
    db.execute("UPDATE cases SET year = 1991 WHERE case_id = 'B'")
    db.commit()
    assert verify(db, KEY, QUOTE_B) == VerifyResult.VERIFIED


# --- R2 on #373: year tolerance, non-party prefix, missing year ---


def test_the_year_window_is_exactly_one_year(db: sqlite3.Connection) -> None:
    """One year from the citation's year is still a candidate; two years off is not (the quote check's own window)."""
    # one year off: both survive, the quote held by exactly one verifies
    db.execute("UPDATE cases SET year = 1991 WHERE case_id = 'B'")
    db.commit()
    assert verify(db, KEY, QUOTE_B) == VerifyResult.VERIFIED
    db.execute("UPDATE cases SET year = 1992 WHERE case_id = 'B'")  # two years off: B is out, only A is checked
    db.commit()
    assert verify(db, KEY, QUOTE_B) == VerifyResult.EXISTS_QUOTE_NOT_FOUND
    db.execute("UPDATE cases SET year = 1989 WHERE case_id = 'B'")  # one year before is inside too
    db.commit()
    assert verify(db, KEY, QUOTE_B) == VerifyResult.VERIFIED


def test_a_missing_decision_year_never_qualifies(db: sqlite3.Connection) -> None:
    db.execute("UPDATE cases SET year = NULL WHERE case_id = 'B'")
    db.commit()
    assert verify(db, KEY, QUOTE_B) == VerifyResult.EXISTS_QUOTE_NOT_FOUND  # B has no year: not a candidate, only A is checked
    db.execute("UPDATE cases SET year = NULL")
    db.commit()
    assert verify(db, KEY, QUOTE_A) == VerifyResult.CONFLICT  # no candidate has a year: nothing survives


def test_no_cited_year_keeps_the_conflict(db: sqlite3.Connection) -> None:
    from pravrudhi.application.verify import _narrow_conflict, normalize_text_for_match, resolve_citation_key

    resolved = resolve_citation_key(db, KEY, None, None)
    assert resolved.status is VerifyResult.CONFLICT
    out = _narrow_conflict(db, KEY, resolved, None, None, normalize_text_for_match(QUOTE_A))
    assert out is resolved  # without a cited year no case can be checked: nothing is narrowed


@pytest.mark.parametrize(
    "prefix,expected",
    [
        ("Zorbatha Mendelsohn v. Quillfeather Aerospace, ", "Zorbatha Mendelsohn v. Quillfeather Aerospace"),
        ("Zorbatha Mendelsohn vs. Quillfeather Aerospace, ", "Zorbatha Mendelsohn vs. Quillfeather Aerospace"),
        ("See ", None),  # a prefix with no party separator is not a claimed name
        ("Held in the case reported at ", None),
        ("", None),
        ("   ", None),
    ],
)
def test_only_a_party_pair_before_the_citation_is_a_claimed_name(prefix: str, expected: str | None) -> None:
    from pravrudhi.application.citations import parse_citations
    from pravrudhi.application.verify import _name_before_citation

    text = f"{prefix}{KEY}"
    assert _name_before_citation(text, parse_citations(text)[0]) == expected


def test_a_non_party_prefix_behaves_like_the_bare_key(db: sqlite3.Connection) -> None:
    """Semantics, stated: with NO party pair typed, a bare ambiguous key whose quote is held by exactly ONE surviving candidate
    returns
    VERIFIED (the untested same-name path of #834); a prefix that names no parties changes nothing."""
    assert verify(db, f"See {KEY}", QUOTE_A) == verify(db, KEY, QUOTE_A) == VerifyResult.VERIFIED
    assert verify(db, f"See {KEY}", "in neither judgment") == verify(db, KEY, "in neither judgment") == VerifyResult.CONFLICT


# --- #832 E1 existence-only: the same resolution, year window and title test as verify(); never VERIFIED ---


def test_existence_only_found_when_the_resolved_case_passes_year_and_title(db: sqlite3.Connection) -> None:
    from pravrudhi.application.verify import exists_in_index

    cite = f"Zorbatha Mendelsohn v. Quillfeather Aerospace, {KEY}"
    assert exists_in_index(db, cite) == VerifyResult.IN_INDEX
    assert exists_in_index(db, cite) != VerifyResult.VERIFIED


def test_existence_only_keeps_a_shared_key_as_conflict_without_names(db: sqlite3.Connection) -> None:
    from pravrudhi.application.verify import exists_in_index

    assert exists_in_index(db, KEY) == VerifyResult.CONFLICT  # nothing narrows it without a typed name or a quote


def test_existence_only_applies_the_year_window(tmp_path: Path) -> None:
    from pravrudhi.application.verify import exists_in_index

    conn = open_index(tmp_path / "year.sqlite3")
    _case(conn, "Y", "Zorbatha Mendelsohn vs Quillfeather Aerospace", 1950, f"Held: {QUOTE_A}.")  # 40 years from the citation
    _citer(conn, "cy", "Zorbatha Mendelsohn v. Quillfeather Aerospace")
    conn.commit()
    assert exists_in_index(conn, KEY) == VerifyResult.NOT_IN_INDEX
    conn.close()


def test_existence_only_applies_the_title_test(tmp_path: Path) -> None:
    from pravrudhi.application.verify import exists_in_index

    conn = open_index(tmp_path / "title.sqlite3")
    # right year, but the indexed case is between different parties than the citation's alias
    _case(conn, "T", "Zorbatha Holdings vs Quillfeather Industries", 1990, f"Held: {QUOTE_A}.")
    _citer(conn, "ct", "Zorbatha Mendelsohn v. Quillfeather Aerospace")
    conn.commit()
    assert exists_in_index(conn, KEY) == VerifyResult.NOT_IN_INDEX
    conn.close()


def test_existence_only_with_no_matching_rows_and_with_a_bad_citation(db: sqlite3.Connection) -> None:
    from pravrudhi.application.verify import exists_in_index

    assert exists_in_index(db, "(2001) 4 SCC 999") == VerifyResult.NOT_IN_INDEX  # key not in the alias table
    assert exists_in_index(db, "no citation here") == VerifyResult.MALFORMED
    assert exists_in_index(db, "AIR 1990 SC 5") == VerifyResult.NOT_IN_INDEX  # a reporter the alias table does not mine
