"""#380: the batched year lookup in `_rows_in_cited_year` must answer EXACTLY what the old per-case `SELECT year ... fetchone()` answered
(first row in scan order; NULL / unreadable / absent -> no year; the +/-1 window). The old function is kept here as the reference."""

from __future__ import annotations

import random
import sqlite3
from pathlib import Path

import pytest

from pravrudhi.application import verify as V
from pravrudhi.application.case_index import CaseRecord, insert_case, open_index


def _old_rows_in_cited_year(conn: sqlite3.Connection, rows: list[sqlite3.Row], cited_year: int | None) -> list[sqlite3.Row]:
    if cited_year is None:
        return []
    ok: list[sqlite3.Row] = []
    for row in rows:
        found = conn.execute("SELECT year FROM cases WHERE case_id = ?", (row["case_id"],)).fetchone()
        try:
            year = int(found[0]) if found is not None else None
        except (TypeError, ValueError):
            year = None
        if year is not None and abs(year - cited_year) <= 1:
            ok.append(row)
    return ok


def _db(tmp_path: Path) -> sqlite3.Connection:
    conn = open_index(tmp_path / "y.sqlite3")
    conn.row_factory = sqlite3.Row
    return conn


def _add(conn: sqlite3.Connection, cid: str, year: object) -> None:
    conn.execute(
        "INSERT INTO cases (case_id, title, court, year, source, path_or_url, text) VALUES (?, ?, 'Supreme Court', ?, 't', '/x', 'text')",
        (cid, f"Title {cid}", year),
    )


def _rows(conn: sqlite3.Connection, ids: list[str]) -> list[sqlite3.Row]:
    return [conn.execute("SELECT ? AS case_id", (i,)).fetchone() for i in ids]


def test_a_duplicate_case_id_takes_the_year_of_the_first_row_in_scan_order(tmp_path: Path) -> None:
    conn = _db(tmp_path)
    _add(conn, "dup", 1990)  # lowest rowid: the old fetchone() returned this one
    _add(conn, "dup", 2005)
    _add(conn, "other", 1990)
    rows = _rows(conn, ["dup", "other"])
    assert V._recorded_years(conn, ["dup", "other"])["dup"] == 1990
    assert V._rows_in_cited_year(conn, rows, 1991) == _old_rows_in_cited_year(conn, rows, 1991)
    assert [r["case_id"] for r in V._rows_in_cited_year(conn, rows, 1991)] == ["dup", "other"]
    assert [r["case_id"] for r in V._rows_in_cited_year(conn, rows, 2005)] == []  # the SECOND row's year never counts, as before


def test_the_window_is_plus_or_minus_one_year_and_nothing_wider(tmp_path: Path) -> None:
    conn = _db(tmp_path)
    for cid, y in (("a", 1990), ("b", 1991), ("c", 1992), ("d", 1989), ("e", 1988)):
        _add(conn, cid, y)
    rows = _rows(conn, list("abcde"))
    assert [r["case_id"] for r in V._rows_in_cited_year(conn, rows, 1990)] == ["a", "b", "d"]  # 1990 +/- 1; 1992 and 1988 are outside
    assert [r["case_id"] for r in V._rows_in_cited_year(conn, rows, 1991)] == ["a", "b", "c"]


def test_null_unreadable_and_absent_years_never_qualify_and_no_cited_year_means_no_rows(tmp_path: Path) -> None:
    conn = _db(tmp_path)
    _add(conn, "null", None)
    _add(conn, "text", "not a year")
    _add(conn, "ok", 1990)
    rows = _rows(conn, ["null", "text", "ok", "absent"])
    assert [r["case_id"] for r in V._rows_in_cited_year(conn, rows, 1990)] == ["ok"]
    assert V._rows_in_cited_year(conn, rows, None) == []
    # an absent or NULL year is "no year", never year 0: a cited year of 0 or 1 must not match it
    assert V._rows_in_cited_year(conn, rows, 0) == _old_rows_in_cited_year(conn, rows, 0) == []
    assert V._rows_in_cited_year(conn, rows, 1) == []
    assert V._rows_in_cited_year(conn, rows, 1990) == _old_rows_in_cited_year(conn, rows, 1990)


def test_more_candidates_than_one_in_list_can_hold(tmp_path: Path) -> None:
    conn = _db(tmp_path)
    ids = [f"c{i:05d}" for i in range(V._YEAR_LOOKUP_CHUNK * 3 + 7)]  # crosses several chunks, past SQLITE's 999 on older builds
    for i, cid in enumerate(ids):
        _add(conn, cid, 1990 + (i % 4))
    rows = _rows(conn, ids)
    assert V._YEAR_LOOKUP_CHUNK < 999
    assert V._rows_in_cited_year(conn, rows, 1991) == _old_rows_in_cited_year(conn, rows, 1991)
    assert len(V._recorded_years(conn, ids)) == len(ids)


def test_a_derived_case_year_table_gives_the_same_answers_including_the_duplicate_rule(tmp_path: Path) -> None:
    conn = _db(tmp_path)
    _add(conn, "dup", 1990)
    _add(conn, "dup", 2005)
    _add(conn, "n", None)
    for i in range(50):
        _add(conn, f"x{i}", 1980 + i)
    rows = _rows(conn, ["dup", "n", "absent"] + [f"x{i}" for i in range(50)])
    before = {y: V._rows_in_cited_year(conn, rows, y) for y in (1979, 1990, 1991, 2005, 2030)}
    conn.executescript(
        "CREATE TABLE case_year (case_id TEXT PRIMARY KEY, year);"
        "INSERT INTO case_year SELECT case_id, year FROM cases WHERE rowid IN (SELECT MIN(rowid) FROM cases GROUP BY case_id);"
    )
    for y, expected in before.items():
        assert V._rows_in_cited_year(conn, rows, y) == expected == _old_rows_in_cited_year(conn, rows, y)
    assert V._recorded_years(conn, ["dup"])["dup"] == 1990  # a table built from the LOWEST rowid, never INSERT OR REPLACE (last row)


def test_the_batched_lookup_matches_the_old_one_on_a_random_mix(tmp_path: Path) -> None:
    conn = _db(tmp_path)
    rnd = random.Random(380)
    ids = [f"r{i}" for i in range(300)]
    for cid in ids:
        _add(conn, cid, rnd.choice([None, 1985, 1990, 1991, 1992, 1995, "junk"]))
        if rnd.random() < 0.2:
            _add(conn, cid, rnd.choice([None, 1990, 2001]))  # a second row with its own year
    rows = _rows(conn, ids + ["nowhere"])
    for cited in (None, 1984, 1990, 1991, 1993, 2001, 2002):
        assert V._rows_in_cited_year(conn, rows, cited) == _old_rows_in_cited_year(conn, rows, cited)


def test_a_failure_other_than_a_missing_derived_table_is_not_swallowed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _db(tmp_path)
    _add(conn, "a", 1990)
    conn.set_progress_handler(lambda: 1, 1)  # every statement is interrupted, as the product's deadline does
    with pytest.raises(sqlite3.OperationalError):
        V._recorded_years(conn, ["a"])
