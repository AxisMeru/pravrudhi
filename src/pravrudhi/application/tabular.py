# ruff: noqa: E501
"""S2, slice 1: the matter tabular table. Pure: plain document and passage records in, a table out.

One row per paper, five fixed columns: Paper, Type as you labelled it (the user's own words, not checked), Dates as written, Sections mentioned, Court or police
station mentioned. Each cell holds exact substrings of that paper with page and place, or says nothing was found in those papers. No model, nothing inferred.

Dates are A3's own scanner, unchanged, called ONCE PER PAPER (it decides day-first or month-first from evidence across the whole document); a date cell carries the
date text as written, its category, page, offsets and span id and NO sentence. Sections and places come from `tabular_sections`. A form the date scanner has claimed is
excluded from the section scanner and counted `claimed_by_date`. A form that straddles two stored passages is NOT READ (`cut_at_storage_boundary`), never half-read.
Parties, paper types and file-name guessing are not part of slice 1. Imported only when the flag `wf_s2_tabular_fact_table` is on.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Any

from pravrudhi.application import chronology_dates as CD
from pravrudhi.application import tabular_sections as TS
from pravrudhi.application.tabular_config import TabularConfig

ROUTE = "/api/v1/matter-tabular"
WINDOW = 60  # edge rule C (as S1): characters taken from each side of a join
REPORT_VERSION = 1
CELL_TEXT_MAX = 200
LABEL_MAX = 80
LABELS_MAX = 500

# ---- the fixed strings (R1's table; every one is pinned to its hash in tests/test_tabular_strings.py) --------------------------------
HEADINGS = {
    "paper": "Paper",
    "type": "Type as you labelled it",
    "dates": "Dates as written",
    "sections": "Sections mentioned",
    "places": "Court or police station mentioned",
}
TYPE_NOTE = "Your own words, not checked."
STANDING_LINE = (
    "A table of what the papers you added say, in their words, with the page and place in each paper. It does not say what any paper means or what "
    "any date, section or court means for the case."
)
UNDER_TABLE = "Each cell lists what the paper mentions, including authorities it cites. It does not say which section or court the case is about."
EMPTY_CELL = "Not found in the papers you added."
NOT_ASSESSED = "Not assessed: what a date, section or court means for the case; anything not in the papers you added."
IPC_BNS_LINE = "IPC and BNS section numbers are shown as printed. We do not map between them."
NON_ENGLISH_LINE = "Papers in a language other than English are not read."
DATE_NOTE = "A date is the date a sentence mentions, not the date of an event or of the paper."
NO_PAPERS = "There are no papers to read. Add papers first."
STORE_OFF = "Stored papers are not available."
NOT_READ_LINES = {
    "act_not_named": ("{N} not read: a section number with no Act named beside it. Check it yourself.", "{N} not read: section numbers with no Act named beside them. Check them yourself."),
    "act_not_on_list": ("A section of an Act we do not list was not read: check it yourself.", "A section of an Act we do not list was not read: check it yourself."),
    "cut_at_storage_boundary": (
        "Something may have been split where the stored text is cut. It was not read: check it yourself.",
        "Something may have been split where the stored text is cut. It was not read: check it yourself.",
    ),
    "name_not_clear": ("{N} not read: a police station name with no clear end. Check it yourself.", "{N} not read: police station names with no clear end. Check them yourself."),
    "form_not_supported": ("{N} not read: a form we do not read. Check it yourself.", "{N} not read: forms we do not read. Check them yourself."),
    "non_english_passage": (NON_ENGLISH_LINE, NON_ENGLISH_LINE),
}
DATE_NOT_READ_LINE = "We did not read this date, so it is not placed. Check it yourself."  # A3's own ruled string, reused for a date the scanner could not read
PAPER_TOO_LARGE = "Too large to read: {name}. This paper is not in the table; check it yourself."
TOO_MANY_PAPERS = "This matter holds more than {cap} papers, so no table was made."
TOO_MANY_CELLS = "This matter's papers give more than {cap} entries, so no table was made."
TOO_BIG = "The table for this matter would be larger than {cap}, so no table was made."
TOO_SLOW = "The table took too long, so no table was made."
LABEL_TOO_LONG = "A label you typed is longer than {cap} characters, so no table was made."
LABEL_UNUSABLE = "A label could not be used, so no table was made."
# R1's rulings for the passage and character cap refusals and the busy line ({cap} renders as a plain integer from the config)
TOO_MANY_PASSAGES = "The papers you added hold more than {cap} stored passages, so no table was made."
TOO_LONG_TEXT = "The papers you added are longer than {cap} characters together, so no table was made."
BUSY = "Another table of these papers is being made. Try again when it ends."

STATES = ("read_papers_listed", "partly_read", "nothing_found")
COLUMNS = ("dates", "sections", "places")

BARRED = (
    "charged", "alleged", "chargesheeted", "filed", "complete", "completeness", "all", "good law", "fake", "equivalent", "corresponds", "verified",
    "confirmed", "in force", "applicable", "applies", "made out", "proved", "proves",
)  # fmt: skip
_BARRED_RX = re.compile(r"(?<![A-Za-z_])(?:" + "|".join(re.escape(b) for b in sorted(BARRED, key=len, reverse=True)) + r")(?![A-Za-z_])", re.I)


def barred_in(text: str) -> list[str]:
    return [m.group(0).lower() for m in _BARRED_RX.finditer(text)]


class TableRefused(Exception):  # noqa: N818 - control flow inside the route, never leaves it
    def __init__(self, code: str, message: str, status_code: int = 422) -> None:
        super().__init__(message)
        self.code, self.status_code = code, status_code


@dataclass(frozen=True)
class Doc:
    doc_id: str
    name: str
    n_pages: int
    pages_without_text: tuple[int, ...]


@dataclass(frozen=True)
class Passage:
    span_id: int
    doc_id: str
    page: int
    start: int
    end: int
    text: str


def size_text(n: int) -> str:
    """A cap as a plain size ('4 MB'), never raw bytes."""
    return f"{n // 1_000_000} MB" if n % 1_000_000 == 0 else f"{max(1, round(n / 1000))} KB"


def pages_note(blank: int, total: int) -> str | None:
    if blank <= 0:
        return None
    if blank == 1 and total == 1:
        return "1 page had no text: check it yourself."
    if blank == 1:
        return f"1 of {total} pages had no text: check it yourself."
    return f"{blank} of {total} pages had no text: check them yourself."


def not_read_line(reason: str, n: int) -> str:
    one, many = NOT_READ_LINES[reason]
    return (one if n == 1 else many).replace("{N}", str(n))


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()


# ---- edge rule C for the new scanners ---------------------------------------------------------------------------------------------------


def _scan_all(text: str) -> list[TS.Form]:
    return [*TS.scan_sections(text), *TS.scan_places(text)]


def _mask(text: str, intervals: list[tuple[int, int]]) -> str:
    if not intervals:
        return text
    chars = list(text)
    for a, b in intervals:
        for i in range(max(0, a), min(len(chars), b)):
            chars[i] = " "
    return "".join(chars)


# ---- the table ----------------------------------------------------------------------------------------------------------------------------


def run_table(
    docs: list[Doc],
    passages: list[Passage],
    cfg: TabularConfig,
    labels: dict[str, str],
    *,
    run_id: str,
    today: date,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    started = clock()
    # 1. caps that refuse the whole request, named
    if len(docs) > cfg.max_papers:
        raise TableRefused("too_many_papers", TOO_MANY_PAPERS.replace("{cap}", str(cfg.max_papers)))
    if len(passages) > cfg.max_passages:
        raise TableRefused("too_many_passages", TOO_MANY_PASSAGES.replace("{cap}", str(cfg.max_passages)))
    if sum(len(p.text) for p in passages) > cfg.max_chars:
        raise TableRefused("too_long", TOO_LONG_TEXT.replace("{cap}", str(cfg.max_chars)))
    by_doc: dict[str, list[Passage]] = {d.doc_id: [] for d in docs}
    for p in sorted(passages, key=lambda p: (p.doc_id, p.start)):
        if p.doc_id in by_doc:
            by_doc[p.doc_id].append(p)
    papers: list[dict[str, Any]] = []
    total_cells = 0
    refused_papers = 0
    sums: dict[str, Counter[str]] = {c: Counter() for c in COLUMNS}
    non_english_total = 0
    for d in docs:
        if clock() - started > cfg.max_seconds:
            raise TableRefused("too_slow", TOO_SLOW)
        paper = _paper(d, by_doc[d.doc_id], labels.get(d.doc_id), cfg, today)
        if paper.get("refused"):
            refused_papers += 1
        else:
            total_cells += paper["_cells"]
            for c in COLUMNS:
                for k, v in paper["columns"][c]["denominator"].items():
                    sums[c][k] += v
            non_english_total += paper["non_english_passages"]
        paper.pop("_cells", None)
        papers.append(paper)
    if total_cells > cfg.max_cells_per_matter:
        raise TableRefused("too_many_cells", TOO_MANY_CELLS.replace("{cap}", str(cfg.max_cells_per_matter)))
    pages_without = sum(len(d.pages_without_text) for d in docs)
    any_not_read = any(sums[c]["not_read"] for c in COLUMNS) or non_english_total
    any_cell = any(sums[c]["read"] for c in COLUMNS)
    if pages_without or refused_papers or any_not_read:
        state = "partly_read"
    elif any_cell:
        state = "read_papers_listed"
    else:
        state = "nothing_found"
    out = {
        "run_id": run_id,
        "client_data": True,
        "version": REPORT_VERSION,
        "state": state,
        "headings": HEADINGS,
        "type_note": TYPE_NOTE,
        "standing_line": STANDING_LINE,
        "under_table": UNDER_TABLE,
        "date_note": DATE_NOTE,
        "ipc_bns_line": IPC_BNS_LINE,
        "non_english_line": NON_ENGLISH_LINE,
        "papers": papers,
        "matter_denominator": {
            "papers": len(docs),
            "papers_refused": refused_papers,
            "pages_without_text": pages_without,
            "non_english_passages": non_english_total,
            "cells": total_cells,
            **{c: dict(sums[c]) for c in COLUMNS},
        },
        "not_assessed": NOT_ASSESSED,
    }
    size = len(json.dumps(out, ensure_ascii=False).encode("utf-8"))
    if size > cfg.max_response_bytes:
        raise TableRefused("too_big", TOO_BIG.replace("{cap}", size_text(cfg.max_response_bytes)))
    return out


def _paper(d: Doc, ps: list[Passage], label: str | None, cfg: TabularConfig, today: date) -> dict[str, Any]:
    base: dict[str, Any] = {
        "doc_id": d.doc_id,
        "name": d.name,
        "pages": d.n_pages,
        "pages_without_text": list(d.pages_without_text),
        "pages_note": pages_note(len(d.pages_without_text), d.n_pages),
        "type_label": label,
    }
    # dates: A3's scanner ONCE for the whole paper, with the injected `today`
    rows: list[tuple[str, int, int, str, int | str]] = [(p.doc_id, p.page, p.start, p.text, p.span_id) for p in ps]
    scan = CD.scan_document(rows, today) if rows else None
    hits = scan.hits if scan is not None else []
    claimed: dict[int, list[tuple[int, int]]] = {}
    date_entries: list[dict[str, Any]] = []
    for h in sorted(hits, key=lambda h: (h.unit, h.start)):
        p = ps[h.unit]
        claimed.setdefault(p.span_id, []).append((h.start, h.end))
        date_entries.append(
            {
                "kind": "date",
                "value": h.text[:CELL_TEXT_MAX],
                "text": h.text[:CELL_TEXT_MAX],
                "category": h.category,
                "how": h.how,
                "readings": [r.isoformat() for r in rd] if (rd := getattr(h, "readings", None)) else None,
                "reason": h.not_read_reason if h.category == "not_read" else None,
                "page": p.page,
                "start": h.start,
                "end": h.end,
                "doc_start": p.start + h.start,
                "span_id": p.span_id,
                "sha256": sha256_text(p.text),
            }
        )
    # edge rule C over every adjacent pair, for the section and place scanners
    masks: dict[int, list[tuple[int, int]]] = {}
    cut_entries: list[dict[str, Any]] = []
    for a, b in zip(ps, ps[1:], strict=False):
        tail, head = a.text[-WINDOW:], b.text[:WINDOW]
        sep = "" if b.start == a.end else ("\f" if a.page != b.page else " ")
        joined = tail + sep + head
        bpos, bend = len(tail), len(tail) + len(sep)
        first_after = bpos + (1 if sep else 0)
        forms = _scan_all(joined)
        cross_groups = {f.group for f in forms if f.start < first_after and f.end > bpos}
        for g in sorted(cross_groups):
            members = [f for f in forms if f.group == g]
            lo, hi = min(f.start for f in members), max(f.end for f in members)
            kind = members[0].kind
            for f in members:
                if f.start < bpos:
                    base_off = len(a.text) - len(tail)
                    masks.setdefault(a.span_id, []).append((base_off + f.start, base_off + min(f.end, bpos)))
                if f.end > bend:
                    masks.setdefault(b.span_id, []).append((max(0, f.start - bend), f.end - bend))
            cut_entries.append(
                {
                    "kind": kind,
                    "column": "sections" if kind == "section" else "places",
                    "reason": "cut_at_storage_boundary",
                    "text": " ".join(joined[lo:hi].split())[:CELL_TEXT_MAX],
                    "page": a.page,
                    "spans": [a.span_id, b.span_id],
                }
            )
    sec_entries: list[dict[str, Any]] = []
    place_entries: list[dict[str, Any]] = []
    sec_not_read: list[dict[str, Any]] = []
    place_not_read: list[dict[str, Any]] = []
    claimed_by_date = 0
    sec_recognised = place_recognised = 0
    non_english = 0
    for p in ps:
        text = _mask(p.text, masks.get(p.span_id, []))
        if TS.non_english(p.text):
            non_english += 1
        for f in TS.scan_sections(text):
            sec_recognised += 1
            if any(f.start < y and x < f.end for x, y in claimed.get(p.span_id, [])):
                claimed_by_date += 1
                continue
            if f.status == "read":
                sec_entries.append(_entry(p, f))
            else:
                sec_not_read.append({"reason": f.reason, "text": f.text, "page": p.page, "span_id": p.span_id, "start": f.start, "end": f.end, "doc_start": p.start + f.start})
        for f in TS.scan_places(text):
            place_recognised += 1
            if f.status == "read":
                place_entries.append(_entry(p, f))
            else:
                place_not_read.append({"reason": f.reason, "text": f.text, "page": p.page, "span_id": p.span_id, "start": f.start, "end": f.end, "doc_start": p.start + f.start})
    for c in cut_entries:
        if c["column"] == "sections":
            sec_recognised += 1
            sec_not_read.append({"reason": c["reason"], "text": c["text"], "page": c["page"], "spans": c["spans"]})
        else:
            place_recognised += 1
            place_not_read.append({"reason": c["reason"], "text": c["text"], "page": c["page"], "spans": c["spans"]})
    date_read = [e for e in date_entries if e["category"] != "not_read"]
    date_not_read = [e for e in date_entries if e["category"] == "not_read"]
    cols = {
        "dates": _column(date_read, date_not_read, len(date_entries), 0, dates=True),
        "sections": _column(sec_entries, sec_not_read, sec_recognised, claimed_by_date),
        "places": _column(place_entries, place_not_read, place_recognised, 0),
    }
    n_cells = sum(c["denominator"]["read"] for c in cols.values())
    base["non_english_passages"] = non_english
    if non_english:
        base["non_english_line"] = NON_ENGLISH_LINE
    if n_cells > cfg.max_cells_per_paper:
        base["refused"] = PAPER_TOO_LARGE.replace("{name}", d.name)
        base["columns"] = None
        base["_cells"] = 0
        return base
    base["columns"] = cols
    base["_cells"] = n_cells
    return base


def _entry(p: Passage, f: TS.Form) -> dict[str, Any]:
    assert p.text[f.start : f.end] == f.text, "a cell must equal the passage slice"  # noqa: S101 - a bug here must be loud
    e: dict[str, Any] = {
        "kind": f.kind,
        "value": f.text if f.kind == "place" else f"{f.text}|{f.act}",
        "text": f.text,
        "page": p.page,
        "start": f.start,
        "end": f.end,
        "doc_start": p.start + f.start,
        "span_id": p.span_id,
        "sha256": sha256_text(p.text),
    }
    if f.act is not None:
        e["act"] = f.act
    if f.place_kind is not None:
        e["place_kind"] = f.place_kind
    return e


def _column(read: list[dict[str, Any]], not_read: list[dict[str, Any]], recognised: int, claimed: int, dates: bool = False) -> dict[str, Any]:
    """Distinct values IN FIRST-APPEARANCE ORDER (never sorted by count), each with its mentions; the denominator and the empty/not-read words."""
    ordered = sorted(read, key=lambda e: (e["doc_start"], e["span_id"]))
    distinct: dict[str, dict[str, Any]] = {}
    for e in ordered:
        v = distinct.setdefault(e["value"], {"value": e["value"], "text": e["text"], "mentions": []})
        for k in ("act", "place_kind", "category", "how", "readings", "kind"):
            if k in e and k not in v:
                v[k] = e[k]
        v["mentions"].append({k: e[k] for k in ("page", "start", "end", "doc_start", "span_id", "sha256")})
    for v in distinct.values():
        v["count"] = len(v["mentions"])
        v["shown"] = v["mentions"][0]  # the display shows the FIRST place only; every place stays in `mentions` for the JSON/CSV (Track B: a list of places is a count by line count)
    reasons = Counter(n["reason"] for n in not_read)
    den = {"recognised": recognised, "read": len(read), "not_read": len(not_read), "claimed_by_date": claimed}
    if recognised != den["read"] + den["not_read"] + claimed:
        raise AssertionError("the column denominator does not add up")  # a bug here must be loud, never a quietly wrong table
    col: dict[str, Any] = {
        "cells": list(distinct.values()),
        "not_read": not_read,
        "denominator": den,
        "empty": EMPTY_CELL if recognised == 0 else None,
        "not_read_lines": ([DATE_NOT_READ_LINE] if (dates and not_read) else [] if dates else [not_read_line(r, n) for r, n in reasons.items()]),
    }
    return col
