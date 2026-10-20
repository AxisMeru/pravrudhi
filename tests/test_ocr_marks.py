"""#575 stanza 3 and 4: text read from a page image is MARKED wherever it is shown. A matter that holds an image-read page
says so on its paper list (`from_ocr`, `ocr_pages`), on every passage a query returns (`from_ocr`, `source`) and on the
chronology, cheque-case and hearing-pack date rows (`from_ocr`), each with R1's one marker line; a matter that holds no such
page is unchanged, byte for byte (no key, no column, no import). Invented text."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from pravrudhi.application import cheque_case
from pravrudhi.application import matter_hearing_pack as hp
from pravrudhi.application import matter_objectstore as O
from pravrudhi.application import scanned_intake_strings as S
from pravrudhi.application.chronology_config import ChronologyConfig
from pravrudhi.application.chronology_matter import chronology_for_matter
from pravrudhi.application.matter_spans import DB_NAME, MatterStore
from pravrudhi.application.matter_spans_config import MatterSpansConfig
from tests.test_ocr_store_holds_text_only import _add_scanned, _files, _hits, env  # noqa: F401  (the fixture and helpers)

T = "org:acme"
MARKER = "Read from a scanned page: check this against the paper."  # R1 00e62215531c5b12


def assert_marked(tree: Any) -> int:
    """R1's rule over any answer: EVERY item read from a scanned page carries the marker (one unmarked is a BLOCK), and an item
    from a text layer never does. Returns how many marked items it saw, so a test cannot pass by finding none."""
    seen = 0
    if isinstance(tree, dict):
        if tree.get("from_ocr") is True or tree.get("source") == "ocr":
            assert tree.get("ocr_marker") == MARKER, tree
            seen += 1
        elif "from_ocr" in tree or tree.get("source") == "text_layer":
            assert "ocr_marker" not in tree, tree
        for v in tree.values():
            seen += assert_marked(v)
    elif isinstance(tree, list):
        for v in tree:
            seen += assert_marked(v)
    return seen


PAGES = [
    "1. The cheque was presented on 3 March 2024 and returned unpaid.",
    "2. The statutory notice was sent on 4 May 2024 by registered post.",
    "3. The complaint was filed on 9 June 2024 before the Magistrate.",
]
PAPER = "\f".join(PAGES)


def _store(tmp: Path) -> MatterStore:
    return MatterStore(tmp / "s", MatterSpansConfig())


def _schema(tmp: Path, matter: str) -> list[str]:
    db = next((tmp / "s").rglob(f"{matter}/{DB_NAME}"))
    with sqlite3.connect(db) as c:
        return [r[1] for r in c.execute("PRAGMA table_info(document)")]


# ---- the paper list and the passages ---------------------------------------------------------------------


def test_a_matter_with_an_image_page_marks_its_papers_and_passages(tmp_path: Path) -> None:
    s = _store(tmp_path)
    m = s.create_matter(T)
    s.add_document(T, m, PAPER, "scan.pdf", ocr_pages=[2])
    s.add_document(T, m, "4. The reply came on 1 July 2024.", "typed.pdf")
    docs = {d["name"]: d for d in s.list_documents(T, m)["documents"]}
    assert docs["scan.pdf"]["from_ocr"] is True and docs["scan.pdf"]["ocr_pages"] == [2]
    assert docs["typed.pdf"]["from_ocr"] is False and "ocr_pages" not in docs["typed.pdf"]
    spans = {sp["page"]: sp for sp in s.query(T, m, "notice")["spans"]}
    assert spans[2]["from_ocr"] is True and spans[2]["source"] == "ocr"
    cheque = {sp["page"]: sp for sp in s.query(T, m, "cheque")["spans"]}
    assert cheque[1]["from_ocr"] is False and cheque[1]["source"] == "text_layer"
    typed = s.query(T, m, "reply")["spans"][0]
    assert typed["from_ocr"] is False and typed["source"] == "text_layer"
    assert s.query(T, m, "notice")["documents"][0]["from_ocr"] is True  # the query's own paper list says it too
    assert spans[2]["ocr_marker"] == MARKER and docs["scan.pdf"]["ocr_marker"] == MARKER and "ocr_marker" not in typed
    assert assert_marked(s.query(T, m, "notice")) >= 2 and assert_marked(s.list_documents(T, m)) == 1
    assert assert_marked(s.query(T, m, "cheque")) >= 1 and assert_marked(s.query(T, m, "reply")) >= 1  # the paper list inside


def test_a_matter_without_an_image_page_is_unchanged_no_key_no_column(tmp_path: Path) -> None:
    s = _store(tmp_path)
    m = s.create_matter(T)
    s.add_document(T, m, PAPER, "typed.pdf")
    s.add_document(T, m, PAPER, "also.pdf", ocr_pages=[])
    got = s.query(T, m, "notice")
    assert all(set(sp) == {"span_id", "doc_id", "name", "page", "start", "end", "text"} for sp in got["spans"])
    assert all("from_ocr" not in d and "ocr_pages" not in d for d in s.list_documents(T, m)["documents"])
    assert "ocr_pages" not in _schema(tmp_path, m)  # the released schema: reading and writing plain papers never adds the column
    docs, spans = s.read_passages(T, m)
    assert all("ocr_pages" not in d and "from_ocr" not in d for d in docs)


def test_the_column_appears_when_an_image_page_is_first_stored_and_older_papers_read_as_false(tmp_path: Path) -> None:
    s = _store(tmp_path)
    m = s.create_matter(T)
    s.add_document(T, m, PAPER, "old.pdf")
    assert "ocr_pages" not in _schema(tmp_path, m)
    s.add_document(T, m, PAPER, "new.pdf", ocr_pages=[3, 1, 3])
    assert "ocr_pages" in _schema(tmp_path, m)
    docs = {d["name"]: d for d in s.list_documents(T, m)["documents"]}
    assert docs["old.pdf"]["from_ocr"] is False and docs["new.pdf"]["ocr_pages"] == [1, 3]  # sorted, no duplicate


def test_deleting_the_scanned_paper_takes_its_marks_with_it(tmp_path: Path) -> None:
    s = _store(tmp_path)
    m = s.create_matter(T)
    d = s.add_document(T, m, PAPER, "scan.pdf", ocr_pages=[1])["doc_id"]
    s.add_document(T, m, PAPER, "typed.pdf")
    s.delete_document(T, m, d)
    assert all(not x.get("from_ocr") for x in s.list_documents(T, m)["documents"])


def test_the_object_backed_store_carries_the_marks_to_a_cold_worker(tmp_path: Path) -> None:
    objects = O.MemoryObjects()
    w = O.ObjectBackedMatterStore(tmp_path / "w1", MatterSpansConfig(), objects, "pfx")
    m = w.create_matter(T)
    w.add_document(T, m, PAPER, "scan.pdf", ocr_pages=[2])
    cold = O.ObjectBackedMatterStore(tmp_path / "w2", MatterSpansConfig(), objects, "pfx")
    assert cold.list_documents(T, m)["documents"][0]["ocr_pages"] == [2]
    assert {sp["page"]: sp["from_ocr"] for sp in cold.query(T, m, "notice")["spans"]} == {2: True}


# ---- the date rows ---------------------------------------------------------------------------------------


def _rows(chron: dict[str, Any]) -> list[dict[str, Any]]:
    return [r for k in ("events", "ambiguous", "approximate", "partial_dates", "scheduled") for r in chron.get(k, [])]


def test_chronology_rows_say_which_dates_were_read_from_an_image(tmp_path: Path) -> None:
    s = _store(tmp_path)
    m = s.create_matter(T)
    s.add_document(T, m, PAPER, "scan.pdf", ocr_pages=[2])
    chron = chronology_for_matter(s, T, m, ChronologyConfig(), "run-1")
    by_page = {r["page"]: r["from_ocr"] for r in _rows(chron)}
    assert by_page == {1: False, 2: True, 3: False}
    assert assert_marked(chron) == 1 and S.OCR_MARKER_LINE == MARKER
    assert all(("ocr_marker" in r) == r["from_ocr"] for r in _rows(chron))
    cands = cheque_case._candidate(next(r for r in _rows(chron) if r["page"] == 2), "notice_sent", [])
    assert cands["from_ocr"] is True and cands["ocr_marker"] == MARKER and assert_marked(cands) == 1
    assert hp._row(next(r for r in _rows(chron) if r["page"] == 2))["ocr_marker"] == MARKER
    assert hp._row(next(r for r in _rows(chron) if r["page"] == 1))["from_ocr"] is False
    assert "ocr_marker" not in hp._row(next(r for r in _rows(chron) if r["page"] == 1))  # a text-layer row never carries it


def test_chronology_of_a_matter_without_an_image_page_has_no_mark_and_imports_no_ocr_module(tmp_path: Path) -> None:
    import sys

    sys.modules.pop("pravrudhi.application.scanned_intake_strings", None)
    s = _store(tmp_path)
    m = s.create_matter(T)
    s.add_document(T, m, PAPER, "typed.pdf")
    chron = chronology_for_matter(s, T, m, ChronologyConfig(), "run-1")
    assert _rows(chron) and all("from_ocr" not in r for r in _rows(chron)) and "ocr_marker" not in str(chron)
    assert "pravrudhi.application.scanned_intake_strings" not in sys.modules
    row = _rows(chron)[0]
    assert "from_ocr" not in cheque_case._candidate(row, "notice_sent", []) and "from_ocr" not in hp._row(row)


# ---- through the route, as a customer adds a scanned PDF -------------------------------------------------


def test_a_scanned_pdf_added_through_the_route_is_marked_on_the_list_and_the_passages(env: Any) -> None:  # noqa: F811
    c, root = env
    m, doc, _b64 = _add_scanned(c)
    assert [p["source"] for p in doc["pages"]] == ["ocr", "ocr"]
    listing = c.post("/api/v1/matter-spans/list", json={"matter_id": m}).json()["documents"]
    assert listing[0]["from_ocr"] is True and listing[0]["ocr_pages"] == [1, 2]
    spans = c.post("/api/v1/matter-spans/query", json={"matter_id": m, "q": "zephyrquillon"}).json()["spans"]
    assert spans and all(sp["from_ocr"] is True and sp["source"] == "ocr" for sp in spans)
    assert assert_marked(listing) == 1 and assert_marked(spans) == len(spans)
    assert not _hits(root, b"image_base64") and _files(root)


def test_the_marker_walker_fails_an_ocr_row_without_the_marker_and_a_text_row_with_it() -> None:
    with pytest.raises(AssertionError):
        assert_marked({"spans": [{"page": 1, "from_ocr": True, "source": "ocr"}]})  # an OCR row with no marker
    with pytest.raises(AssertionError):
        assert_marked([{"from_ocr": True, "ocr_marker": "Read from a page image."}])  # the wrong words
    with pytest.raises(AssertionError):
        assert_marked({"from_ocr": False, "source": "text_layer", "ocr_marker": MARKER})  # a text-layer row carrying it
    assert assert_marked({"from_ocr": True, "source": "ocr", "ocr_marker": MARKER}) == 1
