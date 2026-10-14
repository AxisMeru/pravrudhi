"""#908: the slim, existence-only citation index (src/pravrudhi/application/case_index_slim.py,
scripts/build_slim_citation_index.py).
Invented parties and passages only. Both arms of every rule: all sources and the one-source fallback; text and paths
absent; the source untouched;
an existing output refused; the lookups on the slim file equal those on the full file; the existence-only reader fails closed."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

from pravrudhi.application.case_index import CaseRecord, insert_case, open_index
from pravrudhi.application.case_index_slim import SlimBuildError, build_slim_index, is_existence_only, read_meta
from pravrudhi.application.verify import VerifyResult, exists_in_index

SENTINEL_A = "zorbathian-sentinel-passage"
SENTINEL_B = "quillfeather-sentinel-passage"
KEY_A = "(2090) 2 SCC 100"
KEY_B = "(2091) 3 SCC 200"


def _case(conn: sqlite3.Connection, cid: str, title: str, year: int, source: str, text: str, path: str = "") -> None:
    insert_case(
        conn,
        CaseRecord(
            case_id=cid,
            title=title,
            court="Supreme Court",
            year=year,
            source=source,
            path_or_url=path or f"/private/{cid}.pdf",
            text=text,
        ),
    )


@pytest.fixture
def full(tmp_path: Path) -> Path:
    p = tmp_path / "full.sqlite3"
    c = open_index(p)
    # two real cases (one per source) and two citing judgments (one per source) whose text mines the citation strings
    _case(c, "A", "Zorbatha Mendelsohn vs Quillfeather Aerospace", 2090, "sc_pdf", f"Held: {SENTINEL_A}.")
    _case(
        c,
        "B",
        "Brandolini Vexworth vs Thistledown Estates",
        2091,
        "injudgements",
        f"Held: {SENTINEL_B}.",
        "https://example.invalid/doc/1",
    )
    _case(
        c,
        "cit1",
        "Citer One vs Other",
        2015,
        "sc_pdf",
        f"This Court in Zorbatha Mendelsohn v. Quillfeather Aerospace {KEY_A} held that",
    )
    _case(
        c,
        "cit2",
        "Citer Two vs Other",
        2016,
        "injudgements",
        f"This Court in Brandolini Vexworth v. Thistledown Estates {KEY_B} held that",
    )
    c.commit()
    c.close()
    return p


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def test_all_sources_keeps_every_case_and_alias_and_writes_no_text_or_path(tmp_path: Path, full: Path) -> None:
    out = tmp_path / "slim.sqlite3"
    res = build_slim_index(full, out, provenance_sha256="ab" * 32)
    assert res["cases"] == 4 and res["citation_strings"] == 2 and res["by_source"] == {"injudgements": 2, "sc_pdf": 2}
    s = sqlite3.connect(out)
    assert s.execute("SELECT COUNT(*) FROM cases WHERE text != '' OR path_or_url != ''").fetchone()[0] == 0
    raw = out.read_bytes()
    assert (
        SENTINEL_A.encode() not in raw
        and SENTINEL_B.encode() not in raw
        and b"/private/" not in raw
        and b"example.invalid" not in raw
    )
    # the title, court, year and source survive
    row = s.execute("SELECT title, court, year, source FROM cases WHERE case_id = 'A'").fetchone()
    assert row == ("Zorbatha Mendelsohn vs Quillfeather Aerospace", "Supreme Court", 2090, "sc_pdf")


def test_the_meta_table_says_what_the_file_is(tmp_path: Path, full: Path) -> None:
    out = tmp_path / "slim.sqlite3"
    build_slim_index(full, out, provenance_sha256="cd" * 32)
    m = read_meta(sqlite3.connect(out))
    assert m is not None and m["existence_only"] == "1" and m["schema"] == "slim-v1" and m["provenance_sha256"] == "cd" * 32
    assert m["sources"] == "injudgements,sc_pdf" and m["source_filter"] == "all"
    assert json.loads(m["counts"])["cases"] == 4
    assert m["source_mtime_utc"].endswith("Z") and "built_utc" not in m


def test_one_source_keeps_only_its_cases_and_the_aliases_mined_from_its_own_judgments(tmp_path: Path, full: Path) -> None:
    out = tmp_path / "inj.sqlite3"
    res = build_slim_index(full, out, sources=["injudgements"])
    assert res["cases"] == 2 and res["by_source"] == {"injudgements": 2} and res["citation_strings"] == 1
    s = sqlite3.connect(out)
    assert {r[0] for r in s.execute("SELECT citation FROM citation_aliases")} == {KEY_B}
    assert {r[0] for r in s.execute("SELECT case_id FROM cases")} == {"B", "cit2"}
    assert read_meta(s)["source_filter"] == "only" and read_meta(s)["sources"] == "injudgements"
    # the citation mined from the other source's judgment is not served: not_in_index, never found
    assert exists_in_index(s, KEY_A) == VerifyResult.NOT_IN_INDEX
    assert exists_in_index(s, KEY_B) == VerifyResult.IN_INDEX


def test_lookups_on_the_slim_file_equal_those_on_the_full_file(tmp_path: Path, full: Path) -> None:
    out = tmp_path / "slim.sqlite3"
    build_slim_index(full, out)
    f, s = sqlite3.connect(full), sqlite3.connect(out)
    for cite in (KEY_A, KEY_B, "(2099) 9 SCC 999", "(2090) 2 SCC 101", "not a citation"):
        assert exists_in_index(f, cite) == exists_in_index(s, cite)


def test_the_source_index_is_not_changed(tmp_path: Path, full: Path) -> None:
    before = _sha(full)
    build_slim_index(full, tmp_path / "slim.sqlite3")
    assert _sha(full) == before


def test_the_build_is_deterministic_for_one_source(tmp_path: Path, full: Path) -> None:
    a, b = tmp_path / "a.sqlite3", tmp_path / "b.sqlite3"
    ra, rb = build_slim_index(full, a), build_slim_index(full, b)
    assert ra["sha256"] == rb["sha256"] == _sha(a) == _sha(b)


def test_an_existing_output_is_never_overwritten(tmp_path: Path, full: Path) -> None:
    out = tmp_path / "slim.sqlite3"
    out.write_bytes(b"keep me")
    with pytest.raises(SlimBuildError, match="already exists"):
        build_slim_index(full, out)
    assert out.read_bytes() == b"keep me"


@pytest.mark.parametrize("sources, msg", [([], "empty"), ([" "], "empty"), (["nonesuch"], "unknown source")])
def test_bad_source_selections_refuse_and_leave_no_output(tmp_path: Path, full: Path, sources: list[str], msg: str) -> None:
    out = tmp_path / "slim.sqlite3"
    with pytest.raises(SlimBuildError, match=msg):
        build_slim_index(full, out, sources=sources)
    assert not out.exists()


def test_a_missing_source_refuses(tmp_path: Path) -> None:
    with pytest.raises(SlimBuildError, match="does not exist"):
        build_slim_index(tmp_path / "nope.sqlite3", tmp_path / "out.sqlite3")


def test_the_existence_only_reader_fails_closed(tmp_path: Path) -> None:
    db = sqlite3.connect(tmp_path / "x.sqlite3")
    assert is_existence_only(db) is False and read_meta(db) is None  # no meta table = a full index
    db.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    assert is_existence_only(db) is True  # a meta table without the key reads as existence-only
    for value, expect in (("1", True), ("0", False), ("yes", True), ("", True)):
        db.execute("DELETE FROM meta")
        db.execute("INSERT INTO meta VALUES ('existence_only', ?)", (value,))
        assert is_existence_only(db) is expect, value


def test_the_script_builds_and_prints_the_counts(tmp_path: Path, full: Path, capsys: pytest.CaptureFixture[str]) -> None:
    spec = importlib.util.spec_from_file_location(
        "slim_cli", Path(__file__).resolve().parent.parent / "scripts" / "build_slim_citation_index.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    prov = tmp_path / "PROV.md"
    prov.write_text("provenance")
    out = tmp_path / "slim.sqlite3"
    assert mod.main(["--src", str(full), "--out", str(out), "--provenance", str(prov)]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["cases"] == 4 and printed["sha256"] == _sha(out)
    assert read_meta(sqlite3.connect(out))["provenance_sha256"] == hashlib.sha256(b"provenance").hexdigest()
    assert mod.main(["--src", str(full), "--out", str(out)]) == 2  # the existing output is refused with a code, no traceback
