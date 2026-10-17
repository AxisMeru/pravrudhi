"""#508: rows of the dev index that carry one judgment twice are merged into one row of the slim index. The party-name test and
the grouping (`case_merge`), the merge-map script, and the builder's `merge_map=`. Invented parties only. Both arms of each
rule."""

from __future__ import annotations

import importlib.util
import json
import sqlite3
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from pravrudhi.application.case_index import CaseRecord, insert_case, open_index
from pravrudhi.application.case_index_slim import SlimBuildError, build_slim_index, read_meta
from pravrudhi.application.case_merge import Row, normalise_party, parties_agree, plan_merges
from pravrudhi.application.citations import supp_key
from pravrudhi.application.verify import VerifyResult, exists_in_index

KEY = "(2083) 4 SCC 512"
SUPP = supp_key(2088, 2, 77)
_ROOT = Path(__file__).resolve().parent.parent


def _script(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, _ROOT / "scripts" / f"{name}.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---- the party-name test -------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "compact", "truncated"),
    [
        ("Zorbland Mills Ltd.", "zorblandmillsltd", False),
        ("Zorbland Mills Ltd", "zorblandmillsltd", False),
        ("The State Of U.P", "stateofup", False),
        ("State Of U P", "stateofup", False),
        ("State Of J&K on 6 August, 1997", "stateofjk", False),
        ("Aldric Vennoway & Ors. on 4 May, 1950", "aldricvennoway", False),
        ("Aldric Vennoway And Others", "aldricvennoway", False),
        ("Anaconda Zorb Ltd on 13 December 1955 (1) 1", "anacondazorbltd", False),
        ("Commissioner Of Income-Tax, ...", "commissionerofincometax", True),
        ("The <b>Income</b>-<b>Tax</b> Officer", "incometaxofficer", False),
        ("", "", False),
    ],
)
def test_normalise_party(raw: str, compact: str, truncated: bool) -> None:
    assert normalise_party(raw) == (compact, truncated)


@pytest.mark.parametrize(
    ("a", "b"),
    [
        (("Aldric Vennoway", "The State Of Zorbland"), ("Aldric Vennoway", "State of Zorbland on 4 May, 1950")),
        (("Quillfeather Oil Co Ltd", "Mendelsohn Ors"), ("Quillfeather Oil & Co. Ltd", "Mendelsohn & Ors")),
        (("Zorbland Revenue Commissioner Of", "Thistledown"), ("Zorbland Revenue Commissioner Of Tax, ...", "Thistledown")),
        (("Brandolini", "State Of Zorbland(And ..."), ("Brandolini", "State Of Zorbland and Connected")),
        (("Brandolini Vexworth", "State Of J K"), ("Brandolini Vexworth", "State Of J&K on 6 August, 1997")),
    ],
)
def test_parties_that_are_spellings_of_one_name_agree(a: tuple[str, str], b: tuple[str, str]) -> None:
    assert parties_agree(a, b)


@pytest.mark.parametrize(
    ("a", "b"),
    [
        # two persons against one respondent: the one different-person pair of the measured 233
        (("Sharifa Quillbrook", "The State Of Zorbland Home"), ("Vakilo Quillbrook", "The State Of Zorbland Home")),
        # a prefix of the name is a match only when one of the two titles was cut short (ellipsis); here neither was
        (("Aldric Vennoway", "State Of Zorbland"), ("Aldric Vennoway Junior", "State Of Zorbland")),
        # one side differs although the other agrees
        (("Aldric Vennoway", "State Of Zorbland"), ("Aldric Vennoway", "State Of Thistledown")),
        # a party that is only noise words has no identity: no match
        (("Aldric Vennoway", "The Others"), ("Aldric Vennoway", "And Anr")),
        # the cut title is not a prefix of the other
        (("Aldric Vennoway ...", "State"), ("Bertrand Vennoway", "State")),
    ],
)
def test_parties_that_name_different_people_do_not_agree(a: tuple[str, str], b: tuple[str, str]) -> None:
    assert not parties_agree(a, b)


# ---- the grouping ----------------------------------------------------------------------------------------------------------


def _row(rowid: int, title: str, *, source: str = "sc_pdf", year: int | None = 2083, case_id: str | None = None) -> Row:
    p1, _, p2 = title.partition(" vs ")
    return Row(rowid, case_id or f"c{rowid}", title, year, source, p1, p2)


def test_hash_equal_rows_merge_whatever_their_titles_say() -> None:
    a = _row(1, "Aldric Vennoway vs State Of Zorbland")
    b = _row(2, "Totally Other Name vs Someone", source="injudgements", year=None)
    plan = plan_merges([[a, b]], [])
    assert plan.dropped_to_kept == {2: 1}
    assert plan.refused_pairs == []


def test_a_ratio_pair_with_agreeing_names_merges_and_one_with_different_names_is_refused_and_listed() -> None:
    a, b = _row(1, "Aldric Vennoway vs State Of Zorbland"), _row(2, "Aldric Vennoway vs State Of Zorbland on 4 May, 2083")
    c, d = _row(3, "Sharifa Quillbrook vs The State Home"), _row(4, "Vakilo Quillbrook vs The State Home")
    plan = plan_merges([], [(a, b, 0.99), (c, d, 0.997)])
    assert plan.dropped_to_kept == {2: 1}
    assert [(r["a"]["rowid"], r["b"]["rowid"], r["ratio"]) for r in plan.refused_pairs] == [(3, 4, 0.997)]
    assert plan.refused_but_joined == []
    assert (plan.n_ratio_pairs, plan.n_ratio_kept) == (2, 1)


def test_a_refused_pair_whose_rows_are_joined_through_another_link_is_reported() -> None:
    a, b = _row(1, "Sharifa Quillbrook vs The State Home"), _row(2, "Vakilo Quillbrook vs The State Home")
    twin = _row(3, "Sharifa Quillbrook vs The State Home", source="injudgements", year=None)
    plan = plan_merges([[a, twin], [twin, b]], [(a, b, 0.99)])
    assert len(plan.refused_pairs) == 1 and len(plan.refused_but_joined) == 1
    assert set(plan.dropped_to_kept) == {2, 3}


def test_the_kept_row_has_a_year_then_is_sc_pdf_then_has_the_lowest_rowid() -> None:
    no_year = _row(1, "X vs Y", source="injudgements", year=None)
    inj_year = _row(2, "X vs Y", source="injudgements", year=2083)
    pdf = _row(3, "X vs Y", source="sc_pdf", year=2083)
    pdf_late = _row(4, "X vs Y", source="sc_pdf", year=2083)
    assert plan_merges([[no_year, inj_year, pdf, pdf_late]], []).dropped_to_kept == {1: 3, 2: 3, 4: 3}
    assert plan_merges([[no_year, inj_year]], []).dropped_to_kept == {1: 2}
    assert plan_merges([[pdf_late, pdf]], []).dropped_to_kept == {4: 3}


def test_a_group_of_three_with_one_refused_link_splits_as_the_links_allow() -> None:
    a = _row(1, "Aldric Vennoway vs State Of Zorbland")
    b = _row(2, "Aldric Vennoway vs State Of Zorbland on 4 May, 2083", source="injudgements", year=None)
    c = _row(3, "Bertrand Quillbrook vs State Of Zorbland", source="injudgements", year=None)
    plan = plan_merges([], [(a, b, 0.99), (b, c, 0.99)])
    assert plan.dropped_to_kept == {2: 1}
    assert len(plan.refused_pairs) == 1


# ---- the merge-map script and the builder ----------------------------------------------------------------------------------


def _handoff_row(row: Row, **extra: Any) -> dict[str, Any]:
    return {
        "rowid": row.rowid,
        "case_id": row.case_id,
        "title": row.title,
        "year": row.year,
        "source": row.source,
        "party_1_raw": row.party_1,
        "party_2_raw": row.party_2,
        **extra,
    }


def _source(tmp_path: Path) -> tuple[Path, dict[int, Row]]:
    """rowid 1 and 2: one judgment twice (an sc_pdf copy and an injudgements copy, the second carrying the header key);
    3 and 4: the same case name twice but in two different judgments (no merge); 5/6: a ratio-only pair of two persons;
    7: the citing judgment that mines the alias."""
    p = tmp_path / "src.sqlite3"
    c = open_index(p)
    rows = {
        1: ("A1", "Aldric Vennoway vs State Of Zorbland", 2083, "sc_pdf", "Held: one."),
        2: ("A2", "Aldric Vennoway vs State Of Zorbland", 2083, "injudgements",
            "Equivalent citations: 2088 SCC (SUPP) 2 77. Held: one."),
        3: ("T1", "Quillfeather Oil vs Mendelsohn Aerospace", 2084, "sc_pdf", "Held: first."),
        4: ("T2", "Quillfeather Oil vs Mendelsohn Aerospace", 2084, "sc_pdf", "Held: second, a different judgment."),
        5: ("P1", "Sharifa Quillbrook vs The State Home", 2085, "sc_pdf", "Held: same words."),
        6: ("P2", "Vakilo Quillbrook vs The State Home", 2085, "sc_pdf", "Held: same words."),
        7: ("CIT", "Later vs Other", 2095, "sc_pdf", f"In Aldric Vennoway v. State Of Zorbland {KEY} it was held"),
    }
    for cid, title, year, source, text in rows.values():
        insert_case(c, CaseRecord(cid, title, "Supreme Court", year, source, f"/private/{cid}.pdf", text))
    c.execute("INSERT OR IGNORE INTO citation_aliases (party_1, party_2, citation, case_id) VALUES ('Aldric Vennoway', "
              "'State Of Zorbland', ?, 'A2')", (KEY,))
    c.commit()
    ids = {r[0]: r[1] for r in c.execute("SELECT rowid, case_id FROM cases")}
    c.close()
    assert ids == {k: v[0] for k, v in rows.items()}
    return p, {k: _row(k, v[1], source=v[3], year=v[2], case_id=v[0]) for k, v in rows.items()}


def _map(tmp_path: Path, rows: dict[int, Row]) -> dict[str, Any]:
    h = tmp_path / "handoff"
    h.mkdir()
    (h / "hash_equal_groups.jsonl").write_text(
        json.dumps({"rows": [_handoff_row(rows[1]), _handoff_row(rows[2])]}) + "\n", encoding="utf-8"
    )
    (h / "ratio_only_pairs_233.jsonl").write_text(
        json.dumps({"ratio": 0.99, "a": _handoff_row(rows[5]), "b": _handoff_row(rows[6])}) + "\n", encoding="utf-8"
    )
    mod = _script("build_merge_map_508")
    out = tmp_path / "out"
    assert mod.main(["--handoff", str(h), "--out-dir", str(out)]) == 0
    refused = json.loads((out / "refused_pairs.json").read_text(encoding="utf-8"))
    assert [p["a"]["rowid"] for p in refused["refused_pairs"]] == [5]
    return json.loads((out / "merge_map.json").read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def test_the_script_writes_the_map_and_the_refused_list_and_never_overwrites(tmp_path: Path) -> None:
    _, rows = _source(tmp_path)
    doc = _map(tmp_path, rows)
    assert doc["dropped_to_kept"] == {"2": 1}
    assert doc["counts"]["ratio_pairs_refused"] == 1 and doc["counts"]["rows_dropped"] == 1
    mod = _script("build_merge_map_508")
    assert mod.main(["--handoff", str(tmp_path / "handoff"), "--out-dir", str(tmp_path / "out")]) == 2


def test_a_double_ingested_judgment_is_found_after_the_merge_and_a_real_twin_stays_two(tmp_path: Path) -> None:
    src, rows = _source(tmp_path)
    doc = _map(tmp_path, rows)
    before = build_slim_index(src, tmp_path / "before.sqlite3")
    after = build_slim_index(src, tmp_path / "after.sqlite3", merge_map=doc)
    assert before["cases"] == 7 and after["cases"] == 6
    # two rows of one judgment cannot be told apart: no evidence either way
    assert exists_in_index(sqlite3.connect(tmp_path / "before.sqlite3"), KEY) == VerifyResult.NOT_IN_INDEX
    conn = sqlite3.connect(tmp_path / "after.sqlite3")
    titles = [r[0] for r in conn.execute("SELECT title FROM cases ORDER BY rowid")]
    assert titles.count("Quillfeather Oil vs Mendelsohn Aerospace") == 2  # a true twin is untouched
    assert titles.count("Aldric Vennoway vs State Of Zorbland") == 1  # the second copy of the judgment is dropped
    assert {r[0] for r in conn.execute("SELECT case_id FROM cases")} >= {"A1", "T1", "T2", "P1", "P2", "CIT"}
    assert exists_in_index(conn, KEY) == VerifyResult.IN_INDEX


def test_aliases_and_own_supp_keys_of_a_dropped_copy_follow_the_kept_row(tmp_path: Path) -> None:
    src, rows = _source(tmp_path)
    doc = _map(tmp_path, rows)
    build_slim_index(src, tmp_path / "m.sqlite3", own_supp=True, merge_map=doc)
    conn = sqlite3.connect(tmp_path / "m.sqlite3")
    assert {r[0] for r in conn.execute("SELECT case_id FROM citation_aliases WHERE citation = ?", (KEY,))} == {"CIT", "A1"}
    assert [r[0] for r in conn.execute("SELECT case_id FROM case_citations WHERE citation = ?", (SUPP,))] == ["A1"]
    plain = build_slim_index(src, tmp_path / "p.sqlite3", own_supp=True)
    assert plain["own_supp_cases"] == 1  # without the map the key belongs to the copy


def test_the_meta_records_the_map_and_a_build_without_it_is_unchanged(tmp_path: Path) -> None:
    src, rows = _source(tmp_path)
    doc = _map(tmp_path, rows)
    build_slim_index(src, tmp_path / "with.sqlite3", merge_map=doc)
    meta = read_meta(sqlite3.connect(tmp_path / "with.sqlite3")) or {}
    assert meta["dedupe_ext"] == "dedupe-508-v1" and len(meta["merge_map_sha256"]) == 64
    assert json.loads(meta["merge_counts"]) == {"case_ids_followed": 1, "rows_dropped": 1}
    build_slim_index(src, tmp_path / "x.sqlite3")
    build_slim_index(src, tmp_path / "y.sqlite3")
    assert (tmp_path / "x.sqlite3").read_bytes() == (tmp_path / "y.sqlite3").read_bytes()
    assert "dedupe_ext" not in (read_meta(sqlite3.connect(tmp_path / "x.sqlite3")) or {})


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d["case_ids"].__setitem__("2", "WRONG"),  # another index's map
        lambda d: d["titles"].__setitem__("1", "Another Title"),
        lambda d: d["dropped_to_kept"].__setitem__("99", 1),  # names a row the map has no case id for
        lambda d: d.__setitem__("schema", "other"),
        lambda d: d["dropped_to_kept"].__setitem__("1", 2),  # keeps a row it also drops
        lambda d: d.pop("case_ids"),
    ],
)
def test_a_map_that_does_not_match_the_source_refuses_and_leaves_no_output(tmp_path: Path, mutate: Any) -> None:
    src, rows = _source(tmp_path)
    doc = _map(tmp_path, rows)
    mutate(doc)
    with pytest.raises(SlimBuildError):
        build_slim_index(src, tmp_path / "bad.sqlite3", merge_map=doc)
    assert not (tmp_path / "bad.sqlite3").exists()


def test_a_map_and_a_source_filter_cannot_be_combined(tmp_path: Path) -> None:
    src, rows = _source(tmp_path)
    with pytest.raises(SlimBuildError, match="cannot be combined"):
        build_slim_index(src, tmp_path / "c.sqlite3", sources=["sc_pdf"], merge_map=_map(tmp_path, rows))
