"""The SCC supplementary-volume citation resolves ONLY to a record whose own header prints it (the own-supp slim index).
Invented parties, years and passages only. Both arms of each rule: the header forms that count and the ones that do not; the year
guard; the same-party decoy; two records of one judgment; two different judgments under one key; the plain key of the same number
untouched; an index built without the table; the other tables identical with and without it."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from pravrudhi.application.case_index import CaseRecord, insert_case, open_index
from pravrudhi.application.case_index_slim import build_slim_index, own_supp_citations, read_meta
from pravrudhi.application.citations import parse_citations, supp_key
from pravrudhi.application.verify import VerifyResult, exists_in_index

KEY = "(2089) Supp (1) SCC 335"
FILL = "x " * 450  # pushes a later citation out of the label's reach


def _case(conn: sqlite3.Connection, cid: str, title: str, year: int | None, source: str, text: str) -> None:
    insert_case(conn, CaseRecord(cid, title, "Supreme Court", year, source, f"/private/{cid}.pdf", text))


@pytest.fixture
def full(tmp_path: Path) -> Path:
    p = tmp_path / "full.sqlite3"
    c = open_index(p)
    t = "Aldric Vennoway vs State Of Zorbland"
    # the judgment (decided 2087, reported in the 2089 supplement) held twice: an SC PDF record and an InJudgements record
    _case(
        c,
        "J1",
        t,
        2087,
        "sc_pdf",
        "Aldric Vennoway v. State of Zorbland on 21 November, 2087 Equivalent citations: 2089 AIR 604, "
        "2087 SCR SUPL. (3) 259, 2089 SCC (SUPP) 1 335 BENCH: A B",
    )
    _case(
        c,
        "J2",
        f"{t} on 21 November, 2087",
        None,
        "injudgements",
        "CITATION: 2089 AIR 604, 2089 SCC Supl. (1) 335 Aldric Vennoway",
    )
    # the DECOY: the same parties, a different (later) decision, in the year window of the cited year, with no such citation
    _case(
        c,
        "D1",
        t,
        2089,
        "sc_pdf",
        "Aldric Vennoway v. State of Zorbland on 18 December, 2089 Equivalent citations: 2090 AIR 1348, 2090 SCC (Cri) 5",
    )
    # a PLAIN key with the same number: (2089) 1 SCC 335 belongs to another case
    _case(c, "P1", "Quillfeather vs Mendelsohn Aerospace", 2089, "sc_pdf", "Quillfeather v. Mendelsohn Aerospace on 3 May, 2089")
    _case(
        c, "CIT", "Later Citing Case", 2095, "sc_pdf", "In Quillfeather v. Mendelsohn Aerospace, (2089) 1 SCC 335 the court held"
    )
    # NOT own citations: a case cited in the first paragraph (no label before it), a citation that precedes its decision year,
    # one that lags more than five years, one beyond the label's reach, and a record with no usable decision year
    _case(
        c,
        "B1",
        "Marlowenton vs Thackeray",
        2090,
        "sc_pdf",
        "Marlowenton v. Thackeray on 1 June, 2090. See Aldric Vennoway v. State of Zorbland 2089 Supp (1) SCC 335 held",
    )
    _case(c, "Z1", "Brelwaith vs Quillson", 2095, "sc_pdf", "Brelwaith v. Quillson Equivalent citations: 2089 SCC (SUPP) 1 335")
    _case(c, "W1", "Vellorian vs Tarmaset", 2080, "sc_pdf", "Vellorian v. Tarmaset Equivalent citations: 2089 SCC (SUPP) 1 335")
    _case(c, "R1", "Morvati vs Zerovan", 2088, "sc_pdf", f"Morvati v. Zerovan Equivalent citations: {FILL} 2089 SCC (SUPP) 1 335")
    _case(c, "N1", "Hallowbrook vs Governor", None, "injudgements", "Hallowbrook v. Governor CITATION: 2089 SCC (SUPP) 1 335")
    c.commit()
    c.close()
    return p


def _slim(full: Path, tmp_path: Path, name: str, **kw: object) -> sqlite3.Connection:
    out = tmp_path / f"{name}.sqlite3"
    build_slim_index(full, out, **kw)  # type: ignore[arg-type]
    conn = sqlite3.connect(out)
    conn.row_factory = sqlite3.Row
    return conn


def test_a_supp_citation_resolves_to_the_judgment_that_prints_it_and_not_to_the_same_party_decoy(
    full: Path, tmp_path: Path
) -> None:
    conn = _slim(full, tmp_path, "own", own_supp=True)
    assert {r[0] for r in conn.execute("SELECT case_id FROM case_citations WHERE citation = ?", (KEY,))} == {"J1", "J2"}
    assert exists_in_index(conn, "2089 Supp (1) SCC 335") == VerifyResult.IN_INDEX  # two records of one judgment
    # the decoy carries no such citation, and no other record does
    assert {r[0] for r in conn.execute("SELECT case_id FROM case_citations")} == {"J1", "J2"}


@pytest.mark.parametrize(
    "form",
    [
        "2089 Supp (1) SCC 335",
        "(2089) Supp (1) SCC 335",
        "2089 SCC (SUPP) 1 335",
        "2089 SCC Supl. (1) 335",
        "2089 SUPP(1)SCC335",
        "2089 (1) SCC(SUPP) 335",
    ],
)
def test_every_spelling_of_the_citation_finds_the_same_key(full: Path, tmp_path: Path, form: str) -> None:
    conn = _slim(full, tmp_path, "forms", own_supp=True)
    assert exists_in_index(conn, form) == VerifyResult.IN_INDEX


def test_a_wrong_volume_page_or_year_is_not_held(full: Path, tmp_path: Path) -> None:
    conn = _slim(full, tmp_path, "wrong", own_supp=True)
    for q in ["2089 Supp (2) SCC 335", "2089 Supp (1) SCC 336", "2088 Supp (1) SCC 335", "2089 Supp SCC 335"]:
        assert exists_in_index(conn, q) == VerifyResult.NOT_IN_INDEX, q


def test_the_plain_key_of_the_same_number_is_untouched(full: Path, tmp_path: Path) -> None:
    with_table = _slim(full, tmp_path, "with", own_supp=True)
    without = _slim(full, tmp_path, "without")
    assert exists_in_index(with_table, "(2089) 1 SCC 335") == exists_in_index(without, "(2089) 1 SCC 335")
    assert exists_in_index(with_table, "(2089) 1 SCC 335") == VerifyResult.IN_INDEX  # the plain case
    assert supp_key(2089, 1, 335) != "(2089) 1 SCC 335"


def test_the_guards_keep_cited_cases_and_wrong_years_out(full: Path, tmp_path: Path) -> None:
    conn = _slim(full, tmp_path, "guards", own_supp=True)
    held = {r[0] for r in conn.execute("SELECT case_id FROM case_citations")}
    assert held.isdisjoint({"B1", "Z1", "W1", "R1", "N1", "D1", "P1", "CIT"})


def test_an_index_built_without_the_table_answers_not_in_index(full: Path, tmp_path: Path) -> None:
    conn = _slim(full, tmp_path, "plain")
    assert conn.execute("SELECT name FROM sqlite_master WHERE name = 'case_citations'").fetchone() is None
    assert exists_in_index(conn, "2089 Supp (1) SCC 335") == VerifyResult.NOT_IN_INDEX
    assert "schema_ext" not in (read_meta(conn) or {})


def test_two_different_judgments_under_one_key_are_a_conflict(tmp_path: Path) -> None:
    p = tmp_path / "src_two.sqlite3"
    c = open_index(p)
    _case(c, "K1", "Aldric Vennoway vs State Of Zorbland", 2087, "sc_pdf", "Equivalent citations: 2088 SCC (SUPP) 2 77")
    _case(c, "K2", "Quillfeather Oil vs Mendelsohn Aerospace", 2087, "sc_pdf", "Equivalent citations: 2088 SCC (SUPP) 2 77")
    c.commit()
    c.close()
    conn = _slim(p, tmp_path, "two", own_supp=True)
    assert exists_in_index(conn, "2088 Supp (2) SCC 77") == VerifyResult.CONFLICT


def test_the_other_tables_are_identical_with_and_without_the_own_table(full: Path, tmp_path: Path) -> None:
    a = _slim(full, tmp_path, "a", own_supp=True)
    b = _slim(full, tmp_path, "b")
    for sql in [
        "SELECT case_id, title, court, year, source, path_or_url, text FROM cases ORDER BY rowid",
        "SELECT party_1, party_2, citation, case_id FROM citation_aliases ORDER BY party_1, party_2, citation, case_id",
    ]:
        assert [tuple(r) for r in a.execute(sql)] == [tuple(r) for r in b.execute(sql)]


def test_the_meta_records_the_extension_and_the_counts(full: Path, tmp_path: Path) -> None:
    meta = read_meta(_slim(full, tmp_path, "meta", own_supp=True)) or {}
    assert meta["schema_ext"] == "own-supp-v1" and meta["schema"] == "slim-v1"
    counts = json.loads(meta["own_supp_counts"])
    assert counts["own_supp_keys"] == 1 and counts["own_supp_cases"] == 2 and counts["own_supp_rows"] == 2


@pytest.mark.parametrize(
    ("text", "title", "year", "want"),
    [
        ("Equivalent citations: 2089 AIR 1, 2089 SCC (SUPP) 1 335", "T", 2087, [KEY]),
        ("CITATION: 2089 SCC Supl. (1) 335", "T on 1 May, 2087", None, [KEY]),
        ("CITATION: 2089 SCC Supl. (1) 335", "T", None, []),  # no decision year: nothing is accepted
        ("2089 SCC (SUPP) 1 335 Equivalent citations:", "T", 2087, []),  # the label must come first
        ("Equivalent citations: 2087 SCC (SUPP) 1 335", "T", 2089, []),  # reported before it was decided
        ("Equivalent citations: 2095 SCC (SUPP) 1 335", "T", 2087, []),  # reported more than five years later
        ("no label here 2089 SCC (SUPP) 1 335", "T", 2087, []),
        ("Equivalent citations: 2089 SCC (SUPP) 1 335, 2089 Supp (1) SCC 335", "T", 2087, [KEY]),  # repeats collapse
    ],
)
def test_own_supp_citations_rules(text: str, title: str, year: int | None, want: list[str]) -> None:
    assert own_supp_citations(text, title=title, year=year) == want


@pytest.mark.parametrize(
    ("text", "year", "volume", "page"),
    [
        ("1992 Supp (1) SCC 335", 1992, 1, 335),
        ("1990 SCC  Supl.  675", 1990, None, 675),
        ("1988 SCC (SUPP) 269", 1988, None, 269),
        ("1989 SCC (SUPP) 2 91", 1989, 2, 91),
    ],
)
def test_the_query_parser_reads_the_header_spellings(text: str, year: int, volume: int | None, page: int) -> None:
    (c,) = parse_citations(text)
    assert (c.reporter, c.year, c.volume, c.page) == ("SCC Supp", year, volume, page)


def test_a_plain_scc_and_a_cri_citation_are_not_read_as_supp() -> None:
    assert [c.reporter for c in parse_citations("(1992) 1 SCC 335")] == ["SCC"]
    assert parse_citations("1992 SCC (Cri) 426") == []


def test_variant_titles_of_one_judgment_are_one_case_and_titles_with_no_shared_word_are_two(tmp_path: Path) -> None:
    p = tmp_path / "src_variants.sqlite3"
    c = open_index(p)
    _case(
        c,
        "V1",
        "Mohan Lal Shamlal Soni vs Union Of India And Another",
        2090,
        "sc_pdf",
        "Equivalent citations: 2090 SCC (SUPP) 1 271",
    )
    _case(
        c,
        "V2",
        "Mohanlal Shamji Soni vs Union Of India And Another",
        2090,
        "sc_pdf",
        "Equivalent citations: 2090 SCC (SUPP) 1 271",
    )
    _case(c, "V3", "Balram Gupta vs Union Of India & Anr", 2090, "sc_pdf", "Equivalent citations: 2090 SCC (SUPP) 1 300")
    _case(
        c,
        "V4",
        "Balram Gupta vs Union Of India Anr on 1 September, 2090",
        None,
        "injudgements",
        "CITATION: 2090 SCC Supl. (1) 300",
    )
    _case(c, "V5", "Jagdish Chandra vs State Of Uttarpur", 2090, "sc_pdf", "Equivalent citations: 2090 SCC (SUPP) 1 400")
    _case(c, "V6", "Badruddin Karpude vs State Of Zorbland", 2090, "sc_pdf", "Equivalent citations: 2090 SCC (SUPP) 1 400")
    c.commit()
    c.close()
    conn = _slim(p, tmp_path, "variants", own_supp=True)
    assert (
        exists_in_index(conn, "2090 Supp (1) SCC 271") == VerifyResult.IN_INDEX
    )  # a respelt party: one judgment (shares "soni")
    assert exists_in_index(conn, "2090 Supp (1) SCC 300") == VerifyResult.IN_INDEX  # "&" and a dated title: one judgment
    assert exists_in_index(conn, "2090 Supp (1) SCC 400") == VerifyResult.CONFLICT  # no word shared: two judgments


@pytest.mark.parametrize(
    ("text", "volume", "page"),
    [
        ("1984 SCC (Supp) 490 1984 AIR 5", None, 490),  # a long number after SCC (Supp) is the page, never a volume
        ("1985 SCC (SUPP) 102 1985", None, 102),
        ("1992 SCC (SUPP) 1 335", 1, 335),
        ("1989 SCC (SUPP) 2 91", 2, 91),
    ],
)
def test_a_volume_is_a_single_digit_so_a_long_number_is_the_page(text: str, volume: int | None, page: int) -> None:
    (c,) = parse_citations(text)
    assert (c.volume, c.page) == (volume, page)
