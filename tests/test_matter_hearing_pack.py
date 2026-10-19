# ruff: noqa: E501
"""Matter hearing pack logic (matter_hearing_pack.py): real A3 pages from invented text, S1/A5-shaped pages from the engine's own sample shapes."""

from __future__ import annotations

from datetime import date
from typing import Any

from pravrudhi.application import matter_hearing_pack as HP
from pravrudhi.application.chronology import DocMeta, SpanRec, build_chronology
from pravrudhi.application.chronology_config import ChronologyConfig

TODAY = date(2026, 10, 17)
PAPER = (
    "The accused was arrested on 12.06.2024 and produced before the Magistrate the next day. The FIR was registered on 18 July 2021 at the police station. "
    "The charge sheet was filed in March 2022. The matter is listed on 5.9.2026. The notice was sent on 03/04/2013."
)


def chron(*docs: tuple[str, str]) -> dict[str, Any]:
    spans, metas = [], []
    for i, (name, body) in enumerate(docs):
        spans.append(SpanRec(f"d{i}", name, 1, 0, len(body), body, f"{i}-0"))
        metas.append(DocMeta(f"d{i}", name, 1, (), True))
    return build_chronology(spans, metas, ChronologyConfig(), TODAY)


AGENDA = {
    "not_assessed": ["a", "b"],
    "papers_read": {"heading": "Papers read (1)", "n": 1, "papers": [{"doc_id": "d0", "name": "note-A"}]},
    "not_added": {
        "heading": "Papers not added yet that may matter",
        "rows": [{"id": "cheque", "label": "The cheque", "basis": "statute_text"}],
        "none_line": None,
    },
}


def sweep(statuses_by_citation: dict[str, list[str]]) -> dict[str, Any]:
    dc = [
        {
            "key": k,
            "text": k,
            "statuses": st,
            "occurrences": [{"doc_id": "d0", "page": 1, "status": s, "reason": None} for s in st],
            "papers": [{"doc_id": "d0", "count": len(st)}],
        }
        for k, st in statuses_by_citation.items()
    ]
    return {
        "state": "complete",
        "summary": "Existence check only.",
        "distinct_citations": dc,
        "matter_denominator": {"papers_not_searched": 0},
        "not_assessed": ["b", "c"],
        "papers_read": [{"doc_id": "d0", "name": "note-A"}],
    }


def test_listing_dates_are_the_rows_a3_puts_under_scheduled_with_paper_and_page():
    c = chron(("note-A", PAPER))
    rows = HP.listing_dates(c)
    assert [r["date_as_written"] for r in rows] == ["5.9.2026"] and rows[0]["source"] == "note-A p.1"


def test_timeline_is_in_date_order_ambiguous_rows_after_and_all_readings_kept():
    t = HP.timeline(chron(("note-A", PAPER)))
    vals = [r["read_as"] for r in t["rows"] if not r["ambiguous"]]
    assert vals == sorted(vals) and t["rows"][-1]["ambiguous"] is True and len(t["rows"][-1]["readings"]) == 2


def test_timeline_is_cut_at_the_limit_and_says_so():
    body = (
        " ".join(f"The hearing was held on {d} March 2020." for d in range(1, 29))
        + " "
        + " ".join(f"The order was passed on {d} April 2020." for d in range(1, 29))
    )
    t = HP.timeline(chron(("many.pdf", body)), limit=10)
    assert len(t["rows"]) == 10 and t["cut_line"].startswith("Showing the first 10 of ")


def test_not_added_is_the_agendas_own_rows():
    assert HP.not_added(AGENDA)["rows"] == AGENDA["not_added"]["rows"]


def test_citations_to_check_lists_not_found_and_not_checked_and_counts_found():
    s = sweep(
        {
            "(2014) 8 SCC 273": ["found"],
            "(2099) 99 SCC 999": ["not_found"],
            "AIR 1978 SC 597": ["not_checked"],
            "(2016) 10 SCC 386": ["found", "found"],
        }
    )
    out = HP.citations_to_check(s, {"d0": "note-A"})
    assert [c["text"] for c in out["needs_attention"]] == ["(2099) 99 SCC 999"] and [c["text"] for c in out["not_checked"]] == [
        "AIR 1978 SC597".replace("SC597", "SC 597")
    ]
    assert (
        out["counts"]["distinct_found"] == 2
        and out["needs_attention"][0]["where"][0]["document_name"] == "note-A"
        and out["line"] is None
    )


def test_all_found_gives_the_none_line_and_no_sweep_gives_the_off_line():
    ok = HP.citations_to_check(sweep({"(2014) 8 SCC 273": ["found"]}), {})
    assert (
        ok["line"] == HP.CITES_NONE and "does not say that any citation is right" in HP.CITES_NONE and not ok["needs_attention"]
    )
    off = HP.citations_to_check(None, {})
    assert off["switched_on"] is False and off["line"] == HP.CITES_OFF and off["counts"] is None


def test_compose_carries_every_not_assessed_line_and_echoes_a_given_date_without_inferring_one():
    c = chron(("note-A", PAPER))
    page = HP.compose(
        c, AGENDA, sweep({"(2014) 8 SCC 273": ["found"]}), run_id="hearing-abc", matter_id="m" * 32, hearing_date="2026-11-03"
    )
    assert (
        page["statement"] == HP.STATEMENT
        and page["hearing_date_given"] == "Hearing date you gave: 2026-11-03. The papers' own dates are listed below as they are."
    )
    assert set(c.get("not_assessed", [])) <= set(page["not_assessed"]) and {"a", "b", "c"} <= set(page["not_assessed"])
    assert page["next_dates"]["rows"] and page["citations"]["switched_on"] is True
    none = HP.compose(chron(("p.pdf", "The parties met on 5 May 2012.")), AGENDA, None, run_id="r", matter_id="m")
    assert (
        none["hearing_date_given"] is None
        and none["next_dates"]["none_line"] == HP.NO_NEXT_DATE
        and none["citations"]["line"] == HP.CITES_OFF
    )


def test_the_module_calls_no_model_and_imports_no_network_code():
    import inspect

    src = inspect.getsource(HP)
    for bad in ("anthropic", "urllib", "requests", "httpx", "socket", "subprocess"):
        assert bad not in src
