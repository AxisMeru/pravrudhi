# ruff: noqa: E501
"""S1: the matter citation sweep. Pure: plain document and passage records in, a report out.

A1's citation check (`draft_check.check_draft`), run over every passage of every paper in ONE A2 matter, with ONE memoised lookup per distinct
lookup text. Existence only: "found in our index" means the citation exists in the index, never that the case says what a paper says; "not found" is
not "fake"; a reporter the index does not hold is "not checked". The report says what was not read and not checked first, gives BOTH the number of
citations listed and the number of distinct citations, and never says "all", "complete" or "clean". Nothing is stored.

Passages are A2's paragraphs, so a citation can straddle two of them. Edge rule C (design addendum 2): for EVERY adjacent pair of passages of a paper
the last 60 and first 60 characters are joined (a page change with a form feed) and re-parsed; a citation or citation-like string that crosses the
join is NOT READ (`cut_at_storage_boundary`, both passage ids and pages listed) and the characters of it are masked in both passages BEFORE A1 reads
them, so no half of it is ever looked up. Imported only when the flag `wf_s1_matter_citation_sweep` is on.
"""

from __future__ import annotations

import dataclasses
import json
import re
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pravrudhi.application import draft_check as A1
from pravrudhi.application.citations import parse_citations
from pravrudhi.application.draft_check_config import DraftCheckConfig
from pravrudhi.application.matter_sweep_config import SweepConfig

ROUTE = "/api/v1/matter-citation-sweep"
WINDOW = 60  # edge rule C: characters taken from each side of a join
CONTEXT = 220  # A1's name look-back: the previous passage's tail, masked, passed as read-only context
REPORT_VERSION = 1

# ---- the fixed strings (R1's rulings, design addenda 1 and 3) -------------------------------------------------------------------
STORE_OFF = "Stored papers are not available."
NO_PAPERS = "There are no papers to read. Add papers first."
INDEX_OFF = "The citation index is not available here, so no citation was checked."
TOO_MANY = "This matter lists more than {cap} different citations, so the sweep does not run on it. Check them in smaller sets, or refer to a lawyer."
HEADING = "Papers you added"
HEADING_LINE = "It covers only the papers you added; it does not say who wrote them."
ORDER_LABEL = "In the order first cited in the papers you added; not ranked."
CUT_LINE = "A citation may have been split where the stored text is cut. It was not read: check it yourself."
SUMMARY_LEAD = "Existence check only, in the papers you added."
SUMMARY_TAIL = "This does not say what any case held."
DATA_STATEMENT = "Built and tested on invented text and public court judgments only. No lawyer has used this on a live matter."
SNAPSHOT_LINE = "Read as the papers were stored when the sweep started."
DUPLICATES_LINE = "Counts are of the citations listed in the papers as stored, duplicates included."
NOT_READY_ONE = "1 paper was not ready and is not included."
NOT_READY_MANY = "{k} papers were not ready and are not included."
NOT_SEARCHED_LINE = "The sweep reached its size limit. Papers not searched in full: {papers}. Check those yourself."
BUSY = "Another sweep of these papers is running. Try again when it ends."
# the summary clauses for the two statuses that resolve to an indexed case but are neither "found" nor "not found" (R1); the labels are the engine's own
MISMATCH_CLAUSE = "{m} marked \"{label}\"."
CONFLICT_CLAUSE = "{c} marked \"{label}\"."


def _engine_label(name: str) -> str:
    from pravrudhi.application.citation_status import _LABELS
    from pravrudhi.application.verify import VerifyResult

    return _LABELS[VerifyResult[name]][1]


def not_ready_line(k: int) -> str:
    return NOT_READY_ONE if k == 1 else NOT_READY_MANY.format(k=k)

STATES = ("none_listed", "nothing_checked", "unread_or_unchecked_remain", "listed_strings_looked_up")

# ---- the words this report must never use (a test scans every emitted string AND token) ---------------------------------------
BARRED = (
    "good law", "fake", "false", "invalid", "hallucinated", "hallucination", "clean", "complete", "all citations", "valid authority",
    "verified", "confirmed", "authentic", "genuine", "accurate", "reliable", "no problems", "nothing wrong", "all found",
    "every citation", "good authority",
)  # fmt: skip
_BARRED_RX = re.compile(
    r"(?<![A-Za-z_])(?:" + "|".join(re.escape(b) for b in sorted(BARRED, key=len, reverse=True)) + r")(?![A-Za-z_])", re.I
)


def barred_in(text: str) -> list[str]:
    """Barred words in one emitted string or token (A1's own result lines and not-assessed lines are reused unchanged and checked by the caller)."""
    return [m.group(0).lower() for m in _BARRED_RX.finditer(text)]


class SweepRefused(Exception):  # noqa: N818 - control flow inside the route, never leaves it
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


def count_line(n: int, m: int, d: int) -> str:
    """R1's count line: BOTH numbers, never merged."""
    return f"{n} {'citation' if n == 1 else 'citations'} listed in {m} {'paper' if m == 1 else 'papers'}; {d} distinct {'citation' if d == 1 else 'citations'}."


def pages_note(blank: int, total: int) -> str | None:
    if blank <= 0:
        return None
    if blank == 1 and total == 1:
        return "1 page had no text: check it yourself."
    if blank == 1:
        return f"1 of {total} pages had no text: check it yourself."
    return f"{blank} of {total} pages had no text: check them yourself."


# ---- edge rule C ----------------------------------------------------------------------------------------------------------------


def _crossing(joined: str, bpos: int, sep: str) -> list[tuple[int, int]]:
    """Intervals (in `joined`) of every citation read or citation-like string that crosses the join. The join is the separator at `bpos`
    (empty for a hard cut, where the next passage starts exactly where this one ends): an item crosses it when it starts before the end of
    the separator's first character (or before `bpos` for an empty separator) and ends after `bpos`."""
    first_after = bpos + (1 if sep else 0)

    def crosses(a: int, b: int) -> bool:
        return a < first_after and b > bpos

    parsed = sorted(parse_citations(joined), key=lambda c: c.span)
    cites = sorted([*parsed, *A1.find_other_citations(joined, [c.span for c in parsed])], key=lambda c: c.span)
    all_read = [(c.span[0], c.span[1]) for c in cites]
    read = [(a, b) for a, b in all_read if crosses(a, b)]
    like = []
    for g in A1.like_citation_regions(joined):
        # A1's own rule: a like-string whose anchors lie inside a citation that WAS read is not an unread string
        if any(a0 < b and a < a1 for a0, a1 in g.anchors for a, b in all_read):
            continue
        if crosses(g.start, g.end):
            like.append((g.start, g.end))
    return sorted([*read, *like])


def _mask(text: str, intervals: list[tuple[int, int]]) -> str:
    if not intervals:
        return text
    chars = list(text)
    for a, b in intervals:
        for i in range(max(0, a), min(len(chars), b)):
            chars[i] = " "
    return "".join(chars)


def _snip(text: str, a: int, b: int) -> str:
    return " ".join(text[a : min(b, a + 90)].split())


def _mask_context(s: str) -> str:
    """The previous passage's tail as read-only context for A1's name look-back: digits and reporter anchor words are masked, so nothing in it can
    be read as (or counted as) a citation; the letters of a case name stay."""
    s = re.sub(r"\d", "#", s)
    return re.sub(r"(?i)\b(?:SCC|SC|AIR|OnLine|Cri\s*L\s*J|SCR|SCALE|Supp)\b", lambda m: "x" * len(m.group(0)), s)


# ---- the memoised lookup --------------------------------------------------------------------------------------------------------


class _Capped(Exception):  # noqa: N818
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class _Memo:
    """ONE lookup per distinct lookup text (the text A1 builds: typed name + raw citation), for the whole sweep. A failure is remembered as a failure."""

    def __init__(self, lookup: Callable[[str], tuple[str, bool]], max_lookups: int) -> None:
        self.lookup, self.max_lookups = lookup, max_lookups
        self.answers: dict[str, tuple[str, bool] | None] = {}
        self.made = 0
        self.capped: dict[str, str] = {}

    def __call__(self, text: str) -> tuple[str, bool]:
        if text in self.answers:
            got = self.answers[text]
            if got is None:
                raise RuntimeError("lookup failed")
            return got
        if self.made >= self.max_lookups:
            self.capped[text] = "cap_reached"
            raise _Capped("cap_reached")
        self.made += 1
        try:
            got = self.lookup(text)
        except Exception:  # noqa: BLE001 - A1 labels it "not checked: lookup failed"; remembered so it is not retried
            self.answers[text] = None
            raise
        self.answers[text] = got
        return got


# ---- the sweep ---------------------------------------------------------------------------------------------------------------


_WORST_FIRST = ("conflict", "name_mismatch", "not_found", "not_checked", "found")


def _dkey(row: dict[str, Any]) -> str:
    if row["reporter"] in A1.CHECKABLE_REPORTERS:
        cites = parse_citations(row["text"])
        if cites:
            c = cites[0]
            return f"{c.reporter}|{c.year}|{c.volume}|{c.page}"
    return "raw|" + " ".join(
        row["text"].casefold().split()
    )  # a citation the index does not hold: the normalised text, never a (reporter, 0) bucket


def run_sweep(
    docs: list[Doc],
    passages: list[Passage],
    cfg: SweepConfig,
    lookup: Callable[[str], tuple[str, bool]],
    *,
    run_id: str,
    not_ready: int = 0,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    started = clock()
    # 1. caps on papers, passages and characters: what lies past a cap is "not searched", counted, never dropped
    searched = docs[: cfg.max_papers]
    by_doc: dict[str, list[Passage]] = {d.doc_id: [] for d in searched}
    n_pass = n_chars = 0
    capped = False
    not_searched_passages = 0
    partly_searched: set[str] = set()  # papers of which some passage was left unsearched at a cap
    for p in passages:
        if p.doc_id not in by_doc:
            not_searched_passages += 1
            continue
        if capped or n_pass >= cfg.max_passages or n_chars + len(p.text) > cfg.max_chars:
            capped = True
            not_searched_passages += 1
            partly_searched.add(p.doc_id)
            continue
        by_doc[p.doc_id].append(p)
        n_pass += 1
        n_chars += len(p.text)

    # 2. edge rule C over every adjacent pair of passages of a paper: masks and cut rows, BEFORE anything is read or looked up
    masks: dict[int, list[tuple[int, int]]] = {}
    cuts_by_doc: dict[str, list[dict[str, Any]]] = {d.doc_id: [] for d in searched}
    joins: dict[int, bool] = {}
    for d in searched:
        ps = by_doc[d.doc_id]
        for a, b in zip(ps, ps[1:], strict=False):
            tail, head = a.text[-WINDOW:], b.text[:WINDOW]
            sep = "" if b.start == a.end else ("\f" if a.page != b.page else " ")
            joined = tail + sep + head
            bpos, bend = len(tail), len(tail) + len(sep)
            joins[b.span_id] = True
            for lo, hi in _crossing(joined, bpos, sep):
                if lo < bpos:
                    base = len(a.text) - len(tail)
                    masks.setdefault(a.span_id, []).append((base + lo, base + bpos))
                if hi > bend:
                    masks.setdefault(b.span_id, []).append((0, hi - bend))
                cuts_by_doc[d.doc_id].append(
                    {
                        "text": _snip(joined, lo, hi),
                        "reason": "cut_at_storage_boundary",
                        "doc_id": d.doc_id,
                        "pages": [a.page, b.page],
                        "passages": [a.span_id, b.span_id],
                    }
                )
    masked = {p.span_id: _mask(p.text, masks.get(p.span_id, [])) for ps in by_doc.values() for p in ps}

    # 3. the distinct-citation cap, BEFORE any lookup: parse first, count distinct checkable citations across the matter
    distinct_keys: set[tuple[Any, ...]] = set()
    for ps in by_doc.values():
        for p in ps:
            for c in parse_citations(masked[p.span_id]):
                if c.reporter in A1.CHECKABLE_REPORTERS:
                    distinct_keys.add((c.reporter, c.year, c.volume, c.page))
    if len(distinct_keys) > cfg.max_distinct_citations:
        raise SweepRefused("too_many_citations", TOO_MANY.format(cap=cfg.max_distinct_citations), 422)

    # 4. A1 over every passage, with one memo for the matter and a deadline for the whole sweep
    memo = _Memo(lookup, max_lookups=max(cfg.max_distinct_citations * 3, 10))
    per_doc: list[dict[str, Any]] = []
    all_rows: list[tuple[str, dict[str, Any]]] = []
    for d in searched:
        rows: list[dict[str, Any]] = []
        unread: list[dict[str, Any]] = []
        ps = by_doc[d.doc_id]
        for i, p in enumerate(ps):
            ctx = ""
            if i > 0 and joins.get(p.span_id):
                ctx = _mask_context(masked[ps[i - 1].span_id][-CONTEXT:]) + " "
            text = ctx + masked[p.span_id]
            remaining = max(0.0, cfg.max_seconds - (clock() - started))
            call_cfg = dataclasses.replace(DraftCheckConfig(), max_citations=5_000, max_seconds=remaining)
            out = A1.check_draft(text, call_cfg, memo, clock=clock, run_id="sweep")["draft_check"]
            for r in out["citations"]:
                if r["span"][0] < len(ctx):
                    continue  # a row from the read-only context: never a citation of this passage
                ltext = f"{r['name']} {r['text']}" if r.get("name") else r["text"]
                row = dict(r)
                row["span"] = [p.start + r["span"][0] - len(ctx), p.start + r["span"][1] - len(ctx)]
                row["page"] = p.page
                row["doc_id"] = d.doc_id
                row["passage"] = p.span_id
                if row["status"] == "not_checked" and row["reason"] == "lookup_failed" and ltext in memo.capped:
                    row["reason"] = memo.capped[ltext]
                rows.append(row)
            for r in out["not_read"]:
                if r["span"][0] < len(ctx):
                    continue
                unread.append(
                    {
                        **r,
                        "span": [p.start + r["span"][0] - len(ctx), p.start + r["span"][1] - len(ctx)],
                        "page": p.page,
                        "doc_id": d.doc_id,
                        "passage": p.span_id,
                    }
                )
        for k, r in enumerate(rows, 1):
            r["n"] = k
        cuts = cuts_by_doc[d.doc_id]
        reasons = Counter(r["reason"] for r in rows if r["status"] == "not_checked")
        den = {
            "read": len(rows),
            "found": sum(r["status"] == "found" for r in rows),
            "not_found": sum(r["status"] == "not_found" for r in rows),
            "conflict": sum(r["status"] == "conflict" for r in rows),
            "name_mismatch": sum(r["status"] == "name_mismatch" for r in rows),
            "not_checked": sum(r["status"] == "not_checked" for r in rows),
            "not_checked_by_reason": dict(reasons),
            "not_read": len(unread) + len(cuts),
            "not_read_cut_at_storage_boundary": len(cuts),
        }
        den["checked"] = den["found"] + den["not_found"] + den["conflict"] + den["name_mismatch"]
        den["like_strings"] = den["read"] + den["not_read"]
        if den["read"] != den["checked"] + den["not_checked"] or den["like_strings"] != den["read"] + den["not_read"]:
            raise AssertionError("the paper denominator does not add up")  # a bug here must be loud, never a quietly wrong report
        note = pages_note(len(d.pages_without_text), d.n_pages)
        per_doc.append(
            {"doc_id": d.doc_id, "name": d.name, "pages": d.n_pages, "pages_without_text": list(d.pages_without_text), "pages_note": note,
             "denominator": den, "citations": rows, "not_read": unread, "cut_at_storage_boundary": cuts}
        )  # fmt: skip
        all_rows.extend((d.doc_id, r) for r in rows)

    # 5. the matter: distinct citations (per-occurrence statuses), denominators, state, summary
    distinct: dict[str, dict[str, Any]] = {}
    for doc_id, r in all_rows:
        key = _dkey(r)
        e = distinct.setdefault(key, {"key": key, "text": r["text"], "occurrences": [], "statuses": [], "papers": []})
        e["occurrences"].append(
            {"doc_id": doc_id, "page": r["page"], "status": r["status"], "reason": r["reason"], "name_state": r["name_state"]}
        )
    for e in distinct.values():
        seen = {o["status"] for o in e["occurrences"]}
        e["statuses"] = [s for s in _WORST_FIRST if s in seen]
        counts = Counter(o["doc_id"] for o in e["occurrences"])
        e["papers"] = [{"doc_id": k, "count": n} for k, n in counts.items()]
    sums: Counter[str] = Counter()
    for pd in per_doc:
        for k, v in pd["denominator"].items():
            if isinstance(v, int):
                sums[k] += v
    n_read, n_unread = sums["read"], sums["not_read"]
    m_papers = sum(1 for pd in per_doc if pd["denominator"]["read"] > 0)
    d_distinct = len(distinct)
    if d_distinct > n_read or sums["like_strings"] != n_read + n_unread or n_read != sums["checked"] + sums["not_checked"]:
        raise AssertionError("the matter denominator does not add up")
    unread_pages = sum(len(d.pages_without_text) for d in searched)
    not_searched_papers = len(docs) - len(searched)
    remain = bool(n_unread or sums["not_checked"] or unread_pages or not_searched_papers or not_searched_passages or not_ready)
    if n_read == 0 and n_unread == 0 and not remain:
        state = "none_listed"
    elif sums["checked"] == 0:
        state = "nothing_checked"
    elif remain:
        state = "unread_or_unchecked_remain"
    else:
        state = "listed_strings_looked_up"
    if n_read == 0 and n_unread == 0 and state == "none_listed":
        summary = A1._summary(
            {"read": 0, "checked": 0, "found": 0, "not_found": 0, "conflict": 0, "name_mismatch": 0}, "none_recognised", ""
        )
    elif n_read == 0 and n_unread == 0:
        # nothing was read AND material was not read or not searched: the summary leads with that, never with a clean-sounding "none found" (R2)
        lead = [pages_note(unread_pages, sum(d.n_pages for d in searched)) if unread_pages else None]
        lead.append(not_ready_line(not_ready) if not_ready else None)
        unsearched = not_searched_papers + len(partly_searched)
        lead.append(NOT_SEARCHED_LINE.format(papers=unsearched) if unsearched else None)
        summary = " ".join([x for x in lead if x] + [SUMMARY_LEAD, SUMMARY_TAIL])
    else:
        parts = [SUMMARY_LEAD]
        if n_unread:
            parts.append(f"{n_unread} not read.")
        if sums["not_checked"]:
            parts.append(f"{sums['not_checked']} not checked.")
        parts.append(count_line(n_read, m_papers, d_distinct))
        parts.append(f"{sums['found']} found in our index, {sums['not_found']} not found in our index.")
        if sums["name_mismatch"]:
            parts.append(MISMATCH_CLAUSE.format(m=sums["name_mismatch"], label=_engine_label("NAME_MISMATCH")))
        if sums["conflict"]:
            parts.append(CONFLICT_CLAUSE.format(c=sums["conflict"], label=_engine_label("CONFLICT")))
        parts.append(SUMMARY_TAIL)
        summary = " ".join(parts)

    # 6. size: a row cap and a bytes cap (counts stay exact; rows are dropped from the end and the flag says so)
    truncated = _truncate(per_doc, distinct, cfg.max_rows, cfg.max_bytes)
    notes = []
    if not_ready:
        notes.append(not_ready_line(not_ready))
    unsearched_papers = not_searched_papers + len(partly_searched)
    if unsearched_papers:
        notes.append(NOT_SEARCHED_LINE.format(papers=unsearched_papers))
    return {
        "run_id": run_id,
        "client_data": True,
        "version": REPORT_VERSION,
        "state": state,
        "summary": summary,
        "statement": DATA_STATEMENT,
        "heading": HEADING,
        "heading_line": HEADING_LINE,
        "snapshot_line": SNAPSHOT_LINE,
        "duplicates_line": DUPLICATES_LINE,
        "order_label": ORDER_LABEL,
        "cut_line": CUT_LINE,
        "notes": notes,
        "papers_read": per_doc,
        "distinct_citations": list(distinct.values()),
        "matter_denominator": {
            "papers": len(per_doc),
            "papers_with_citations": m_papers,
            "read": n_read,
            "distinct": d_distinct,
            "like_strings": sums["like_strings"],
            "not_read": n_unread,
            "checked": sums["checked"],
            "found": sums["found"],
            "not_found": sums["not_found"],
            "conflict": sums["conflict"],
            "name_mismatch": sums["name_mismatch"],
            "not_checked": sums["not_checked"],
            "lookups_made": memo.made,
            "pages_without_text": unread_pages,
            "papers_excluded_not_ready": not_ready,
            "papers_not_searched": not_searched_papers,
            "passages_not_searched": not_searched_passages,
        },
        "listing_truncated": truncated,
        "not_assessed": list(A1.NOT_ASSESSED),
        "wording": "found in our index means the citation exists in the index, not that the case says what a paper says",
    }


def _truncate(per_doc: list[dict[str, Any]], distinct: dict[str, dict[str, Any]], max_rows: int, max_bytes: int) -> bool:
    """Drop listed rows from the END until the row count and the byte size are within the caps. Counts are never touched."""
    truncated = False
    total = sum(len(pd["citations"]) for pd in per_doc)
    while total > max_rows:
        for pd in reversed(per_doc):
            if pd["citations"]:
                pd["citations"].pop()
                total -= 1
                truncated = True
                break
    size = len(json.dumps([per_doc, list(distinct.values())], ensure_ascii=False))
    while size > max_bytes:
        dropped = False
        for pd in reversed(per_doc):
            if pd["citations"]:
                size -= len(json.dumps(pd["citations"].pop(), ensure_ascii=False))
                dropped = truncated = True
                break
        if not dropped:
            for e in reversed(list(distinct.values())):
                if e["occurrences"]:
                    size -= len(json.dumps(e["occurrences"].pop(), ensure_ascii=False))
                    dropped = truncated = True
                    break
        if not dropped:
            break
    return truncated
