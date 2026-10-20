"""#575 stanza 4, the remaining surfaces: A8's list of dates, A5's agenda pointers and S1's sweep rows mark each item read from a
scanned page with `from_ocr` and R1's one `ocr_marker`, and a row from a text layer never carries it; a matter with no scanned
page is unchanged. B2 (quashing: paper names only) and B3 (the checklist reads only the dates the user types, never A2) are not
OCR surfaces; the last tests pin both facts. Invented text."""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any

import pytest

from pravrudhi.application import agenda_matter as AM
from pravrudhi.application import matter_sweep_matter as SM
from pravrudhi.application import ocr_marks, synopsis_dates
from pravrudhi.application import s138_checklist as B3
from pravrudhi.application.chronology_config import ChronologyConfig
from pravrudhi.application.chronology_matter import chronology_for_matter
from pravrudhi.application.matter_spans import MatterStore
from pravrudhi.application.matter_spans_config import MatterSpansConfig
from pravrudhi.application.matter_sweep_config import SweepConfig
from tests.test_ocr_marks import MARKER, assert_marked

T = "org:acme"
GOOD = "(2087) 4 SCC 512"
PAGES = [
    "1. The cheque was returned unpaid on 3 March 2024 and the applicant relied on (2086) 1 SCC 99 before the court.",
    "2. The statutory notice was sent on 4 May 2024 by registered post; reliance was placed on " + GOOD + ".",
]
PAPER = "\f".join(PAGES)


class Lookup:
    def __call__(self, text: str) -> tuple[str, bool]:
        return ("IN_INDEX", False) if "512" in text else ("NOT_IN_INDEX", False)


def _matter(tmp: Path, ocr_pages: list[int] | None) -> tuple[MatterStore, str]:
    s = MatterStore(tmp / "s", MatterSpansConfig())
    m = s.create_matter(T)
    s.add_document(T, m, PAPER, "scan.pdf", ocr_pages=ocr_pages)
    s.add_document(T, m, "3. The reply came on 1 July 2024; the cheque was returned unpaid.", "typed.pdf")
    return s, m


def _sweep(s: MatterStore, m: str) -> dict[str, Any]:
    return SM.sweep_for_matter(s, T, SM.SweepRequest(matter_id=m), "run-1", SweepConfig(), Lookup())


def _agenda(s: MatterStore, m: str) -> dict[str, Any]:
    return AM.agenda_for_matter(s, T, AM.AgendaRequest(matter_id=m), "run-1")


# ---- A5 agenda pointers ------------------------------------------------------------------------


def test_agenda_pointers_into_a_scanned_page_are_marked_and_typed_ones_are_not(tmp_path: Path) -> None:
    s, m = _matter(tmp_path, [1])
    out = _agenda(s, m)
    ptrs = [p for i in out["ingredients"] for p in (i["where_mentioned"] or {}).get("pointers", [])]
    by_name = {(p["name"], p["page"]): p for p in ptrs}
    assert by_name[("scan.pdf", 1)]["from_ocr"] is True and by_name[("scan.pdf", 1)]["ocr_marker"] == MARKER
    assert by_name[("typed.pdf", 1)]["from_ocr"] is False and "ocr_marker" not in by_name[("typed.pdf", 1)]
    assert assert_marked(out) >= 1


def test_agenda_of_a_matter_without_a_scanned_page_is_unchanged(tmp_path: Path) -> None:
    s, m = _matter(tmp_path, None)
    out = _agenda(s, m)
    assert "from_ocr" not in str(out) and "ocr_marker" not in str(out)


# ---- S1 sweep rows -----------------------------------------------------------------------------


def test_sweep_rows_on_a_scanned_page_are_marked_in_citations_and_occurrences(tmp_path: Path) -> None:
    s, m = _matter(tmp_path, [1])
    out = _sweep(s, m)
    papers = out["papers_read"]
    scan = next(d for d in papers if d["name"] == "scan.pdf")
    rows = scan["citations"]
    assert {r["page"] for r in rows} == {1, 2}
    for r in rows:
        assert r["from_ocr"] is (r["page"] == 1) and ("ocr_marker" in r) is (r["page"] == 1)
        if r["page"] == 1:
            assert r["ocr_marker"] == MARKER
    typed = next(d for d in papers if d["name"] == "typed.pdf")
    assert all(r["from_ocr"] is False and "ocr_marker" not in r for r in typed["citations"])
    occ = [o for e in out["distinct_citations"] for o in e["occurrences"]]
    assert occ and all(o["from_ocr"] == ("ocr_marker" in o) for o in occ) and any(o["from_ocr"] for o in occ)
    assert assert_marked(out) >= 2


def test_sweep_of_a_matter_without_a_scanned_page_is_unchanged(tmp_path: Path) -> None:
    s, m = _matter(tmp_path, None)
    out = _sweep(s, m)
    assert "from_ocr" not in str(out) and "ocr_marker" not in str(out)


# ---- A8 list of dates --------------------------------------------------------------------------


def test_the_list_of_dates_rows_carry_the_marks_of_their_chronology_rows(tmp_path: Path) -> None:
    s, m = _matter(tmp_path, [2])
    chron = chronology_for_matter(s, T, m, ChronologyConfig(), "run-1")
    rows = [r for k in ("events", "ambiguous", "approximate", "partial_dates", "scheduled") for r in chron.get(k, [])]
    table = synopsis_dates.table_rows(rows, 1)
    by_page = {(t["document_name"], t["page"]): t for t in table}
    t_ocr, t_txt = by_page[("scan.pdf", 2)], by_page[("scan.pdf", 1)]
    assert t_ocr["from_ocr"] is True and t_ocr["ocr_marker"] == MARKER and t_ocr["dates"][0]["ocr_marker"] == MARKER
    assert t_txt["from_ocr"] is False and "ocr_marker" not in t_txt and "ocr_marker" not in t_txt["dates"][0]
    assert assert_marked(table) >= 2


def test_the_list_of_dates_of_a_matter_without_a_scanned_page_is_unchanged(tmp_path: Path) -> None:
    s, m = _matter(tmp_path, None)
    chron = chronology_for_matter(s, T, m, ChronologyConfig(), "run-1")
    rows = [r for k in ("events", "ambiguous", "approximate", "partial_dates", "scheduled") for r in chron.get(k, [])]
    assert "from_ocr" not in str(synopsis_dates.table_rows(rows, 1))


# ---- the shared helper, and the surfaces that are not OCR surfaces -----------------------------


def test_the_helper_marks_only_rows_that_name_a_known_paper_and_page() -> None:
    pages = {"d1": (2,)}
    tree = {
        "a": [
            {"doc_id": "d1", "page": 2},
            {"doc_id": "d1", "page": 1},
            {"doc_id": "d1", "pages": [1, 2]},
            {"doc_id": "d9", "page": 2},
            {"doc_id": "d1"},
        ]
    }
    ocr_marks.mark_tree(tree, pages)
    got = tree["a"]
    assert [r.get("from_ocr") for r in got] == [True, False, True, None, None]
    assert got[0]["ocr_marker"] == MARKER and "ocr_marker" not in got[1] and got[2]["ocr_marker"] == MARKER
    assert ocr_marks.mark_tree({"doc_id": "d1", "page": 2}, {}) == {"doc_id": "d1", "page": 2}  # no scanned page: untouched
    assert ocr_marks.ocr_page_map([{"doc_id": "d1", "from_ocr": False}]) == {}


def test_b3_reads_only_what_the_user_types_and_b2_lists_only_paper_names() -> None:
    src = inspect.getsource(B3)
    assert "matter_spans" not in src and "read_passages" not in src and "MatterStore" not in src  # not an OCR surface
    from pravrudhi.application import quashing_matter

    assert "read_passages" not in inspect.getsource(quashing_matter)  # it lists names and counts only, never passage text


@pytest.mark.parametrize("name", ["agenda_matter", "matter_sweep_matter"])
def test_the_wiring_calls_the_helper_after_the_tool_has_built_its_answer(name: str) -> None:
    import importlib

    assert "mark_tree" in inspect.getsource(importlib.import_module(f"pravrudhi.application.{name}"))
