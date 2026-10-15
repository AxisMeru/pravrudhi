"""`index_coverage` must stay fast on a slim-index-sized database on every SQLite version.

`cases` is an FTS5 virtual table. The coverage query used a JOIN with `citation_aliases`, which SQLite 3.46 (the Python 3.13
container image) plans as a full scan of `cases` for every alias row: about 30 s on the real 10 MB slim index, paid by the first
verify call after start. The semi-join form gives the same counts in milliseconds. This test builds a database at the real size
(38,657 cases, 17,664 aliases) and bounds the call, so a return of the JOIN fails on a SQLite that plans it badly, and checks the
counts against the JOIN's on a small fixture (which every version computes quickly)."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from pravrudhi.application import verify as V
from pravrudhi.application.case_index import open_index

JOIN_REFERENCE = (
    "SELECT c.court AS court, COUNT(DISTINCT c.case_id) AS n FROM cases c JOIN citation_aliases a ON a.case_id = c.case_id "
    "GROUP BY c.court ORDER BY n DESC"
)


def _db(path: Path, cases: int, aliases: int) -> sqlite3.Connection:
    conn = open_index(path)
    courts = ["SC", "HC-Madras", "HC-Delhi", "HC-Bombay"]
    conn.executemany(
        "INSERT INTO cases (case_id, title, court, year, source, path_or_url, text) VALUES (?, ?, ?, ?, ?, ?, ?)",
        [(f"c{i}", f"P{i} v. Q{i}", courts[i % 4], 1950 + i % 70, "t", "u", f"P{i} v. Q{i}") for i in range(cases)],
    )
    conn.executemany(
        "INSERT OR IGNORE INTO citation_aliases (party_1, party_2, citation, case_id) VALUES (?, ?, ?, ?)",
        [(f"p{i}", f"q{i}", f"({1950 + i % 70}) {1 + i % 9} SCC {i}", f"c{(i * 2) % cases}") for i in range(aliases)],
    )
    conn.commit()
    return conn


def test_the_counts_equal_the_join_on_a_fixture_with_duplicate_aliases(tmp_path: Path) -> None:
    conn = _db(tmp_path / "small.sqlite3", 200, 150)
    conn.execute(
        "INSERT OR IGNORE INTO citation_aliases (party_1, party_2, citation, case_id) VALUES ('x', 'y', '(1970) 1 SCC 1', 'c4')"
    )
    conn.execute(
        "INSERT OR IGNORE INTO citation_aliases (party_1, party_2, citation, case_id) VALUES ('x', 'z', '(1970) 1 SCC 2', 'c4')"
    )
    conn.commit()
    conn.row_factory = sqlite3.Row
    reference = {r["court"]: r["n"] for r in conn.execute(JOIN_REFERENCE)}
    V._COVERAGE_CACHE.clear()
    cov = V.index_coverage(conn, str(tmp_path / "small.sqlite3"), 1)
    assert cov["courts_resolvable"] == reference and cov["resolvable_cases"] == sum(reference.values())
    assert cov["courts"] == sorted(reference)


def test_coverage_is_fast_on_an_index_of_the_real_size(tmp_path: Path) -> None:
    path = tmp_path / "real_size.sqlite3"
    conn = _db(path, 38_657, 17_664)
    V._COVERAGE_CACHE.clear()
    started = time.monotonic()
    cov = V.index_coverage(conn, str(path), 2)
    elapsed = time.monotonic() - started
    assert cov["judgments_held"] == 38_657 and cov["resolvable_cases"] > 1000
    assert elapsed < 3.0, f"index_coverage took {elapsed:.1f} s (the JOIN form takes about 30 s on SQLite 3.46)"
