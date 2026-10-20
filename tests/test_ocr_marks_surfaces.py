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
from pravrudhi.application import quashing as QG
from pravrudhi.application import quashing_matter as QM
from pravrudhi.application import s138_checklist as B3
from pravrudhi.application import tabular_matter as TM
from pravrudhi.application.chronology_config import ChronologyConfig
from pravrudhi.application.chronology_matter import chronology_for_matter
from pravrudhi.application.matter_spans import MatterStore
from pravrudhi.application.matter_spans_config import MatterSpansConfig
from pravrudhi.application.matter_sweep_config import SweepConfig
from pravrudhi.application.tabular_config import TabularConfig
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


def test_b3_reads_only_what_the_user_types() -> None:
    src = inspect.getsource(B3)
    assert "matter_spans" not in src and "read_passages" not in src and "MatterStore" not in src  # not an OCR surface


def test_the_tabular_route_marks_every_mention_and_not_read_row_by_its_paper_and_page(tmp_path: Path) -> None:
    s = MatterStore(tmp_path / "s", MatterSpansConfig())
    m = s.create_matter(T)
    text = (
        "The FIR was registered at P.S. Karol Bagh, Delhi under sections 323, 324 and 34 IPC.\f"
        "Second page: FIR under section 420 IPC at P.S. Saket."
    )
    s.add_document(T, m, text, "scan.pdf", ocr_pages=[1])
    out = TM.table_for_matter(s, T, TM.TabularRequest(matter_id=m), "run-1", TabularConfig())
    items: list[dict[str, Any]] = []

    def collect(n: Any) -> None:
        if isinstance(n, dict):
            if isinstance(n.get("page"), int):
                items.append(n)
            for v in n.values():
                collect(v)
        elif isinstance(n, list):
            for v in n:
                collect(v)

    collect(out)
    assert items and all("from_ocr" in x for x in items)  # EVERY row that names a page says whether it was read from an image
    assert all(x["from_ocr"] is (x["page"] == 1) and ("ocr_marker" in x) is (x["page"] == 1) for x in items)
    assert assert_marked(out) >= 3


def test_b2_quashing_page_lists_paper_names_only_so_it_has_no_item_to_mark(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(QG, "pin_status_cached", lambda _p: None)
    s, m = _matter(tmp_path, [1])
    out = QM.agenda_for_matter(s, T, QM.QuashingRequest(matter_id=m), "run-1", None)
    assert assert_marked(out) == 0 and "ocr_marker" not in str(out)
    for sentence in PAGES:  # none of the scanned paper's own words is on the page
        assert sentence[3:30] not in str(out)


def test_every_consumer_of_read_passages_is_counted_so_the_question_needs_no_reading(tmp_path: Path) -> None:
    """The consumers of `read_passages` (chronology, A5, S1, tabular; A8 through the chronology) each show a marked item
    for a matter with a scanned page; B2 reads no passage and shows none."""
    import subprocess

    out = subprocess.run(
        ["git", "grep", "-l", "read_passages(", "--", "src"], capture_output=True, text=True, cwd=Path(__file__).parent.parent
    )
    users = {Path(x).stem for x in out.stdout.split() if x.endswith(".py")} - {"matter_spans", "matter_objectstore"}
    assert users == {"agenda_matter", "chronology_matter", "matter_sweep_matter", "tabular_matter"}, (
        users
    )  # a new consumer fails here
    s, m = _matter(tmp_path, [1, 2])
    counts = {
        "chronology": assert_marked(chronology_for_matter(s, T, m, ChronologyConfig(), "r")),
        "agenda": assert_marked(_agenda(s, m)),
        "sweep": assert_marked(_sweep(s, m)),
        "tabular": assert_marked(TM.table_for_matter(s, T, TM.TabularRequest(matter_id=m), "r", TabularConfig())),
    }
    assert all(n >= 1 for n in counts.values()), counts


@pytest.mark.parametrize("name", ["agenda_matter", "matter_sweep_matter"])
def test_the_wiring_calls_the_helper_after_the_tool_has_built_its_answer(name: str) -> None:
    import importlib

    assert "mark_tree" in inspect.getsource(importlib.import_module(f"pravrudhi.application.{name}"))


def test_a_distinct_citation_is_marked_when_any_occurrence_is_from_a_scanned_page(tmp_path: Path) -> None:
    s = MatterStore(tmp_path / "s", MatterSpansConfig())
    m = s.create_matter(T)
    both = f"Page one relies on {GOOD}.\fPage two relies on {GOOD} again and on (2086) 1 SCC 99."
    s.add_document(T, m, both, "scan.pdf", ocr_pages=[1])
    out = _sweep(s, m)
    entries = {e["text"]: e for e in out["distinct_citations"]}
    mixed = entries[GOOD]  # one occurrence on the scanned page, one on a text-layer page
    assert [o["from_ocr"] for o in mixed["occurrences"]] == [True, False]
    assert mixed["from_ocr"] is True and mixed["ocr_marker"] == MARKER
    text_only = entries["(2086) 1 SCC 99"]  # only on page 2 (text layer)
    assert text_only["from_ocr"] is False and "ocr_marker" not in text_only
    assert assert_marked(out) >= 3
    plain = MatterStore(tmp_path / "p", MatterSpansConfig())
    pm = plain.create_matter(T)
    plain.add_document(T, pm, both, "typed.pdf")
    assert "from_ocr" not in str(_sweep(plain, pm))  # no scanned page: nothing added, entries included


def test_the_hearing_pack_citation_rows_say_where_a_citation_was_read_from_a_scanned_page(tmp_path: Path) -> None:
    from pravrudhi.application import matter_hearing_pack_route as HPR

    s = MatterStore(tmp_path / "s", MatterSpansConfig())
    m = s.create_matter(T)
    scanned = "The applicant relied on (2011) 4 SCC 777 before the court."  # not in the index: needs attention
    typed = "The respondent relied on (2012) 5 SCC 888."
    s.add_document(T, m, f"{scanned}\f{typed}", "scan.pdf", ocr_pages=[1])
    s.add_document(T, m, "The reply cites (2013) 6 SCC 999.", "typed.pdf")

    def sweep(store: MatterStore, tenant: str, matter: str, run: str) -> dict[str, Any]:
        return SM.sweep_for_matter(store, tenant, SM.SweepRequest(matter_id=matter), run, SweepConfig(), Lookup())

    pack = HPR.pack_for_matter(s, T, HPR.MatterHearingPackRequest(matter_id=m), "run-1", sweep_runner=sweep)
    need = pack["citations"]["needs_attention"]
    where = {(w["document_name"], w["page"]): w for e in need for w in e["where"]}
    assert where[("scan.pdf", 1)]["from_ocr"] is True and where[("scan.pdf", 1)]["ocr_marker"] == MARKER  # the scanned citation
    assert where[("scan.pdf", 2)]["from_ocr"] is False and "ocr_marker" not in where[("scan.pdf", 2)]  # a text-layer page
    assert where[("typed.pdf", 1)]["from_ocr"] is False and "ocr_marker" not in where[("typed.pdf", 1)]
    entries = {e["text"]: e for e in need}
    assert entries["(2011) 4 SCC 777"]["from_ocr"] is True and entries["(2011) 4 SCC 777"]["ocr_marker"] == MARKER
    assert entries["(2013) 6 SCC 999"]["from_ocr"] is False and "ocr_marker" not in entries["(2013) 6 SCC 999"]
    assert assert_marked(pack["citations"]) >= 2
    assert all(e["from_ocr"] == any(w["from_ocr"] for w in e["where"]) for e in need)  # an entry is marked iff a where row is
    plain = MatterStore(tmp_path / "p", MatterSpansConfig())
    pm = plain.create_matter(T)
    plain.add_document(T, pm, f"{scanned}\f{typed}", "typed.pdf")
    cites = HPR.pack_for_matter(plain, T, HPR.MatterHearingPackRequest(matter_id=pm), "run-1", sweep_runner=sweep)["citations"]
    assert "from_ocr" not in str(cites)  # no scanned page: unchanged


def unmarked_rows(tree: Any, path: str = "$") -> list[str]:
    """R1's generic rule: in an answer for a matter with a scanned page, EVERY dict that names a page (page or pages) together
    with a paper (doc_id or document_name) must carry from_ocr. Returns the paths of those that do not (same rule as Track A's
    marks probe, no inheritance), so the next gap is found by a walk."""
    out: list[str] = []
    if isinstance(tree, dict):
        names_page = (
            ("page" in tree and isinstance(tree["page"], int))
            or isinstance(tree.get("pages"), list)
            and tree["pages"]
            and all(isinstance(p, int) for p in tree["pages"])
        )
        names_paper = isinstance(tree.get("doc_id"), str) or isinstance(tree.get("document_name"), str)
        if names_page and names_paper and "from_ocr" not in tree:
            out.append(path)
        for k, v in tree.items():
            out += unmarked_rows(v, f"{path}.{k}")
    elif isinstance(tree, list):
        for i, v in enumerate(tree):
            out += unmarked_rows(v, f"{path}[{i}]")
    return out


def test_the_unmarked_row_walker_finds_a_row_that_names_a_paper_and_page_without_from_ocr() -> None:
    assert unmarked_rows({"a": [{"document_name": "x.pdf", "page": 2}, {"doc_id": "d", "pages": [1, 2], "from_ocr": True}]}) == [
        "$.a[0]"
    ]
    assert unmarked_rows({"name": "x.pdf", "page": 2}) == [] and unmarked_rows({"doc_id": "d"}) == []


def test_in_a_matter_with_a_scanned_page_no_row_of_any_surface_names_a_paper_and_page_without_from_ocr(tmp_path: Path) -> None:
    from pravrudhi.application import cheque_case_route as CCR
    from pravrudhi.application import matter_hearing_pack_route as HPR

    s = MatterStore(tmp_path / "s", MatterSpansConfig())
    m = s.create_matter(T)
    text = (
        "The cheque dated 3 March 2024 was presented on 10 March 2024 and returned unpaid on 12 March 2024; "
        "the applicant relied on (2011) 4 SCC 777. A statutory notice was sent on 20 March 2024.\f"
        "The complaint was filed on 15 May 2024 under section 138 and relied on (2087) 4 SCC 512. "
        "The case is listed for hearing on 2 September 2026."
    )
    s.add_document(T, m, text, "scan.pdf", ocr_pages=[1])
    s.add_document(T, m, "The reply came on 1 July 2024; the cheque was returned unpaid; (2013) 6 SCC 999.", "typed.pdf")

    def sweep(store: MatterStore, tenant: str, matter: str, run: str) -> dict[str, Any]:
        return SM.sweep_for_matter(store, tenant, SM.SweepRequest(matter_id=matter), run, SweepConfig(), Lookup())

    chron = chronology_for_matter(s, T, m, ChronologyConfig(), "r")
    rows = [r for k in ("events", "ambiguous", "approximate", "partial_dates", "scheduled") for r in chron.get(k, [])]
    answers = {
        "a2_list": s.list_documents(T, m),
        "a2_query": s.query(T, m, "cheque"),
        "chronology": chron,
        "a8_table": synopsis_dates.table_rows(rows, 1),
        "a5_agenda": _agenda(s, m),
        "s1_sweep": sweep(s, T, m, "r"),
        "tabular": TM.table_for_matter(s, T, TM.TabularRequest(matter_id=m), "r", TabularConfig()),
        "cheque_case": CCR.check_for_matter(s, T, CCR.ChequeCaseRequest(matter_id=m), "r"),
        "hearing_pack": HPR.pack_for_matter(s, T, HPR.MatterHearingPackRequest(matter_id=m), "r", sweep_runner=sweep),
    }
    for name, answer in answers.items():
        assert assert_marked(answer) >= (0 if name == "a2_list" else 1), name  # each surface really showed a scanned item
        assert unmarked_rows(answer, name) == [], unmarked_rows(answer, name)


def test_when_the_listing_is_cut_for_size_an_entry_whose_scanned_occurrence_was_dropped_is_still_marked(tmp_path: Path) -> None:
    from dataclasses import replace

    s = MatterStore(tmp_path / "s", MatterSpansConfig())
    m = s.create_matter(T)
    many = " ".join(f"(2087) 4 SCC {500 + i}" for i in range(60))
    last = "(2090) 9 SCC 901"
    s.add_document(T, m, f"{many}\fThe last paper cites {last}.", "scan.pdf", ocr_pages=[2])
    cfg = replace(SweepConfig(), max_bytes=10_000)
    out = SM.sweep_for_matter(s, T, SM.SweepRequest(matter_id=m), "run-1", cfg, Lookup())
    assert out["listing_truncated"] is True
    entry = next(e for e in out["distinct_citations"] if e["text"] == last)
    assert entry["occurrences"] == []  # the size cap dropped its only (scanned-page) occurrence
    assert entry["from_ocr"] is True and entry["ocr_marker"] == MARKER  # still marked: its paper holds a scanned page
    first = next(e for e in out["distinct_citations"] if e["text"] == "(2087) 4 SCC 500")
    assert first["from_ocr"] is True  # conservative: the same paper; over-marking is the safe direction
    plain = MatterStore(tmp_path / "p", MatterSpansConfig())
    pm = plain.create_matter(T)
    plain.add_document(T, pm, f"{many}\fThe last paper cites {last}.", "typed.pdf")
    cut_plain = SM.sweep_for_matter(plain, T, SM.SweepRequest(matter_id=pm), "run-1", cfg, Lookup())
    assert cut_plain["listing_truncated"] is True and "from_ocr" not in str(cut_plain)  # no scanned page: nothing added
