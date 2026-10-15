"""#508: a landmark citation whose alias groups are noisy was answered CONFLICT although its case is indexed. One engine-only rule with both arms; invented
parties only. R-C: a title that prints "Corp" carries the alias word "Corporation" (the abbreviation fold the spelling test already has)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from pravrudhi.application.case_index import CaseRecord, insert_case, open_index
from pravrudhi.application.verify import VerifyResult, exists_in_index

KEY = "(2083) 4 SCC 512"
PASSAGE = "a wholly different holding about arbitration clauses and their severability"


def _db(tmp_path: Path, name: str, groups: list[tuple[str, str]], cases: list[tuple[str, str, int]]) -> sqlite3.Connection:
    conn = open_index(tmp_path / f"{name}.sqlite3")
    insert_case(conn, CaseRecord("citer", "Later vs Other", "Supreme Court", 2095, "sc_pdf", "", PASSAGE))
    for cid, title, year in cases:
        insert_case(conn, CaseRecord(cid, title, "Supreme Court", year, "sc_pdf", "", PASSAGE))
    for p1, p2 in groups:
        conn.execute("INSERT INTO citation_aliases (party_1, party_2, citation, case_id) VALUES (?, ?, ?, 'citer')", (p1, p2, KEY))
    conn.commit()
    return conn


# ---- R-C: the abbreviation fold in the title containment -------------------------------------------------------------------------


def test_a_title_that_prints_corp_carries_the_alias_word_corporation(tmp_path: Path) -> None:
    conn = _db(
        tmp_path,
        "rc_yes",
        [("Marlowenton Hydro Corporation", "Aldric Vennoway")],
        [("real", "Marlowenton Hydro Corp vs Aldric Vennoway", 2083)],
    )
    assert exists_in_index(conn, KEY) == VerifyResult.IN_INDEX


def test_a_title_missing_another_alias_word_is_still_not_carried_even_with_the_fold(tmp_path: Path) -> None:
    conn = _db(
        tmp_path,
        "rc_no",
        [("Marlowenton Hydro Corporation", "Aldric Vennoway Zorbland")],
        [("real", "Marlowenton Hydro Corp vs Aldric Vennoway", 2083)],
    )
    assert exists_in_index(conn, KEY) == VerifyResult.NOT_IN_INDEX
