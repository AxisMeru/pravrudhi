"""#472: `_disambiguate` reads the candidate cases' years in ONE batch (`_recorded_years`), not one full-table select per case.
Invented parties only. The answer rule is unchanged: a named citation whose agreeing groups resolve to several cases is narrowed
to the one whose recorded year is within one year of the cited year, else it stays CONFLICT."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from pravrudhi.application.case_index import CaseRecord, insert_case, open_index
from pravrudhi.application.verify import VerifyResult, exists_in_index, verify

KEY = "(2083) 4 SCC 512"


def _index(tmp_path: Path, years: list[int | None]) -> sqlite3.Connection:
    """One alias group ("Aldric Vennoway" v. "State of Zorbland") that resolves to one case per entry of `years` (all titled
    alike, so the exact title match returns every one of them), plus a second spelling group so the key has two groups."""
    conn = open_index(tmp_path / "idx.sqlite3")
    insert_case(conn, CaseRecord("citer", "Later vs Other", "Supreme Court", 2095, "sc_pdf", "", "x"))
    for i, y in enumerate(years):
        insert_case(conn, CaseRecord(f"c{i}", "Aldric Vennoway vs State Of Zorbland", "Supreme Court", y, "sc_pdf", "", "x"))
    for p2 in ("State of Zorbland", "State of Zorbland and Another"):
        conn.execute(
            "INSERT INTO citation_aliases (party_1, party_2, citation, case_id) VALUES ('Aldric Vennoway', ?, ?, 'citer')",
            (p2, KEY),
        )
    conn.commit()
    conn.row_factory = sqlite3.Row
    return conn


def _year_selects(conn: sqlite3.Connection, run) -> int:  # type: ignore[no-untyped-def]
    seen: list[str] = []
    conn.set_trace_callback(lambda sql: seen.append(sql) if "FROM cases" in sql and "year" in sql.lower() else None)
    run()
    conn.set_trace_callback(None)
    return len(seen)


NAMED = "Aldric Vennoway v. State of Zorbland, (2083) 4 SCC 512"


@pytest.mark.parametrize("n", [3, 30, 300])
def test_the_number_of_year_selects_does_not_grow_with_the_candidates(tmp_path: Path, n: int) -> None:
    conn = _index(tmp_path, [2083] * n)
    selects = _year_selects(conn, lambda: exists_in_index(conn, NAMED))
    assert selects <= 3, selects  # one batched read (a chunk per 500 ids), never one per case


def test_the_year_rule_is_unchanged(tmp_path: Path) -> None:
    # exactly one candidate within a year of the cited year: it is the case
    one = _index(tmp_path / "a" if (tmp_path / "a").mkdir() is None else tmp_path, [2083, 2040, 2041, None])
    assert exists_in_index(one, NAMED) == VerifyResult.IN_INDEX
    # two within a year: not told apart (the existence rule needs exactly one case)
    two = _index(tmp_path / "b" if (tmp_path / "b").mkdir() is None else tmp_path, [2083, 2084, 2040])
    assert exists_in_index(two, NAMED) != VerifyResult.IN_INDEX
    # none within a year, or no recorded year at all: nothing qualifies
    none = _index(tmp_path / "c" if (tmp_path / "c").mkdir() is None else tmp_path, [2040, 2041, None])
    assert exists_in_index(none, NAMED) != VerifyResult.IN_INDEX


def test_verify_with_a_quote_takes_the_same_batched_path(tmp_path: Path) -> None:
    conn = _index(tmp_path, [2083] * 40)
    selects = _year_selects(conn, lambda: verify(conn, NAMED, "a passage"))
    assert selects <= 6, selects
