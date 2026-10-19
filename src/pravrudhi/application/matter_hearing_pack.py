# ruff: noqa: E501
"""Matter hearing pack, the logic (A7 slice 2; not the #415 decide-result pack in hearing_pack/; no model, no network). One page gathered from three pieces the engine already returns for ONE matter's papers:
the dates the papers mention (A3 chronology), the papers the ingredient agenda lists as not added yet (A5), and the citations in the papers
that the index could not match or could not check (S1 sweep, when it is switched on). It re-orders and re-labels nothing the pieces say: it
only puts them on one page for a person preparing a hearing. It does not say what matters, what is missing from the case or what any case held.

Pure functions on the pages `build_chronology`, `agenda_for_matter` and `sweep_for_matter` return; the route and the flag are the route layer's job.
"""

from __future__ import annotations

from typing import Any

STATEMENT = (
    "This page gathers, from the papers you added, the dates they mention, the papers the ingredient agenda lists as not added yet, and the "
    "citations in them that the index could not match or check. It does not say what matters for the hearing, what is missing from the case, "
    "or what any case held."
)
NEXT_DATES_HEADING = "Dates the papers give for a hearing or listing"
NO_NEXT_DATE = "No listing or hearing date was found in the papers added."
TIMELINE_HEADING = "Dates the papers mention, in date order"
TIMELINE_CUT = "Showing the first {shown} of {total} dates; the chronology page lists the dates it read."
NOT_ADDED_HEADING = "Papers not added yet that the agenda lists"
CITES_HEADING = "Citations to check"
CITES_NONE = "No citation in the papers added was reported as not found, unmatched or not checked. This does not say that any citation is right."
CITES_OFF = "The citation sweep is not switched on here, so no citation was checked."
CITES_NOTE = "Existence check only: 'found in our index' does not say what a case held, and a citation not found may still exist outside the index."
DECLARED_DATE_LINE = "Hearing date you gave: {d}. The papers' own dates are listed below as they are."
MAX_TIMELINE = 40
NEEDS_ATTENTION = ("not_found", "conflict", "name_mismatch")


def _paper_names(sources: list[dict[str, Any]]) -> dict[str, str]:
    """doc id -> paper name from whichever page lists the papers read (a list of papers, or A5's {heading, n, papers})."""
    names: dict[str, str] = {}
    for src in sources:
        pr = src.get("papers_read")
        items = pr.get("papers") if isinstance(pr, dict) else pr
        for p in items or []:
            if isinstance(p, dict) and p.get("doc_id"):
                names[str(p["doc_id"])] = str(p.get("name"))
    return names


def listing_dates(chronology: dict[str, Any]) -> list[dict[str, Any]]:
    """The rows A3 puts under `scheduled` (dates the papers give for a listing or hearing), as they are, with the paper and page."""
    return [_row(r) for r in chronology.get("scheduled") or []]


def _row(r: dict[str, Any]) -> dict[str, Any]:
    return {
        "row_id": r.get("id"),
        "date_as_written": r.get("date_as_written"),
        "read_as": r.get("normalised"),
        "readings": r.get("readings"),
        "ambiguous": bool(r.get("readings") and len(r["readings"]) > 1),
        "event_type": r.get("event_type"),
        "document_name": r.get("document_name"),
        "page": r.get("page"),
        "source": f"{r.get('document_name')} p.{r.get('page')}",
        "quote": " ".join(str(r.get("quote") or "").split())[:200],
    }


def timeline(chronology: dict[str, Any], limit: int = MAX_TIMELINE) -> dict[str, Any]:
    """A3's exact-date rows in date order (rows with two readings keep both and sort after the exact ones), at most `limit`."""
    exact = [_row(r) for r in chronology.get("events") or []]
    ambiguous = [_row(r) for r in chronology.get("ambiguous") or []]
    exact.sort(key=lambda r: (str(r["read_as"] or "9999"), str(r["source"])))
    rows = exact + ambiguous
    shown = rows[:limit]
    return {
        "heading": TIMELINE_HEADING,
        "rows": shown,
        "cut_line": TIMELINE_CUT.format(shown=len(shown), total=len(rows)) if len(rows) > limit else None,
        "approximate_or_unread": len(chronology.get("approximate") or [])
        + len(chronology.get("partial_dates") or [])
        + len(chronology.get("date_not_read") or []),
    }


def not_added(agenda: dict[str, Any]) -> dict[str, Any]:
    na = agenda.get("not_added") or {}
    return {"heading": NOT_ADDED_HEADING, "rows": list(na.get("rows") or []), "none_line": na.get("none_line")}


def citations_to_check(sweep: dict[str, Any] | None, names: dict[str, str]) -> dict[str, Any]:
    """From the S1 sweep page: every distinct citation reported not found, unmatched or name-mismatched, and every one not checked; the rest are only counted."""
    if sweep is None:
        return {
            "heading": CITES_HEADING,
            "switched_on": False,
            "line": CITES_OFF,
            "needs_attention": [],
            "not_checked": [],
            "counts": None,
            "note": CITES_NOTE,
        }
    need: list[dict[str, Any]] = []
    unchecked: list[dict[str, Any]] = []
    found = 0
    for c in sweep.get("distinct_citations") or []:
        sts = [str(s) for s in c.get("statuses") or []]
        where = [
            {
                "document_name": names.get(str(o.get("doc_id")), str(o.get("doc_id"))),
                "page": o.get("page"),
                "status": o.get("status"),
                "reason": o.get("reason"),
            }
            for o in c.get("occurrences") or []
        ]
        entry = {"text": c.get("text"), "statuses": sts, "where": where}
        if any(s in NEEDS_ATTENTION for s in sts):
            need.append(entry)
        elif any(s.startswith("not_checked") for s in sts):
            unchecked.append(entry)
        elif sts and all(s == "found" for s in sts):
            found += 1
    d = sweep.get("matter_denominator") or {}
    return {
        "heading": CITES_HEADING,
        "switched_on": True,
        "line": None if (need or unchecked) else CITES_NONE,
        "needs_attention": need,
        "not_checked": unchecked,
        "counts": {
            "distinct_found": found,
            "needs_attention": len(need),
            "not_checked": len(unchecked),
            "papers_without_citations_read": d.get("papers_not_searched"),
        },
        "sweep_state": sweep.get("state"),
        "sweep_summary": sweep.get("summary"),
        "note": CITES_NOTE,
    }


def compose(
    chronology: dict[str, Any],
    agenda: dict[str, Any],
    sweep: dict[str, Any] | None,
    *,
    run_id: str,
    matter_id: str,
    hearing_date: str | None = None,
) -> dict[str, Any]:
    """The one page. Every `not_assessed` line of the pieces is carried; nothing is dropped or reworded. `hearing_date` is the date the user typed (ISO), echoed back, never inferred."""
    names = _paper_names([sweep or {}, agenda, chronology])
    nxt = listing_dates(chronology)
    not_assessed: list[str] = []
    for src in (chronology.get("not_assessed") or [], agenda.get("not_assessed") or [], (sweep or {}).get("not_assessed") or []):
        for x in src:
            if x not in not_assessed:
                not_assessed.append(x)
    return {
        "run_id": run_id,
        "matter_id": matter_id,
        "statement": STATEMENT,
        "hearing_date_given": DECLARED_DATE_LINE.format(d=hearing_date) if hearing_date else None,
        "next_dates": {"heading": NEXT_DATES_HEADING, "rows": nxt, "none_line": None if nxt else NO_NEXT_DATE},
        "timeline": timeline(chronology),
        "not_added": not_added(agenda),
        "citations": citations_to_check(sweep, names),
        "papers": {"read": chronology.get("papers_read"), "agenda_papers_read": agenda.get("papers_read")},
        "not_assessed": not_assessed,
    }
