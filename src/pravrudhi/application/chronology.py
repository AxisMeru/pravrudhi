"""A3: the chronology of ONE matter's papers (flag `wf_a3_chronology`, default off). Core: plain span records in, a table out.

Built and tested on court judgment recitals and invented fixtures only, not on the merits of any case. No lawyer has used this on
a live matter. Finding differing dates between your own papers has not been tested on real matters.

Contract: A3 v4.1 (d8cc8c6e231d) + addenda 2 (fd4e3eb0fec5), 3 (b683866c908d) + the v4.2 note (7e6843c93fbb). A row is a SENTENCE
that mentions a date: the date as written, how it was read, the sentence quoted, and where it is (document, page, offsets, the
passage and its hash). What a sentence mentions is a phrase match from a closed list, never a statement that the thing happened,
and the date written in it may not be the date of the event the sentence names. No model reads or writes anything; nothing is
decided that the papers do not settle; nothing is joined across a passage cut (see `chronology_dates`).

The core has no store and no web code: the route (a later commit) reads A2's passages and hands plain records to
`build_chronology`.
"""

from __future__ import annotations

import calendar
import hashlib
import json
import re
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from pravrudhi.application.chronology_config import ChronologyConfig
from pravrudhi.application.chronology_dates import Hit, scan_document

ASSET = Path(__file__).resolve().parent.parent / "assets" / "chronology" / "event_types.json"
ROUTE = "/api/v1/chronology"
MAX_QUOTE = 400

STANDING_LINE = (
    "Events we read from the papers you added. This is not the chronology of the case: it may be incomplete. Each row is a "
    "sentence that mentions a date; the date written there may not be the date of the event the sentence names."
)
DATA_STATEMENT = (
    "Built and tested on court judgment recitals and invented fixtures only, not on the merits of any case. No lawyer has used "
    "this on a live matter. Finding differing dates between your own papers has not been tested on real matters."
)
SCOPE_LINE = (
    "Topics we recognise: custody and process steps only. Papers of other kinds (for example a cheque case) show dates only."
)
LANGUAGE_LINE = "English-language papers only; other languages are not read and are listed as not read."
HEADERS = {
    "date": "Date as written, and how we read it",
    "mentions": "The sentence mentions",
    "sentence": "The sentence",
    "source": "Source",
}
HEADINGS = {
    "undated": "Sentences that mention a topic but no date",
    "less_than_a_day": "Partial dates: only a month, a year, or a day and month",
    "scheduled": "Dates given for a listing",
    "not_read": "Lines with a date we could not read",
    "printed": "Lines that look printed",
    "number_like": "Looks like a case or document number",
    "ambiguous": "Day or month first? Check",
    "approximate": "Dates written as approximate",
}
SEVERAL = "several things in one sentence: each date has its own row; check which date goes with which."
PRINTED_LINE = "Looks like a printed line (header, footer or print stamp), not part of the paper's text."
DATE_ONLY = "a date only"
REFERENCE_NOTE = "The date is given as the date of a paper the sentence refers to."
SCHEDULED_NOTE = 'Date given for a listing (for example "listed on", "adjourned to"): not placed as an event.'
BAIL_NOTE = "Bail: we do not say whether it was granted or refused."
BOTH_CONVENTIONS = "This file uses both day-first and month-first dates, so we do not guess."
GROUP_HEADING = "Rows that may be the same event with different dates"
GROUP_LINE = "May be the same event or different events: check. We show both and do not choose between them."
POSSIBLY_RELATED = "Possibly related; no name appears in both sentences, so we do not group them: check."
FILE_NAME_BASIS = "Paper type taken from the file name."
NO_PAPERS = "There are no papers to read. Add papers first."
NOT_READ_LINE = "We did not read this date, so it is not placed. Check it yourself."
UNDATED_LINE = "We list these apart and do not place them in the table."
AMBIGUOUS_QUESTION = "Day or month first? Check"
MAX_POSSIBLY_RELATED = 20


def load_asset() -> dict[str, Any]:
    asset: dict[str, Any] = json.loads(ASSET.read_text(encoding="utf-8"))
    return asset


# ---- inputs -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class SpanRec:
    """One stored passage of a document (A2's span): verbatim text with its page and offsets into the document."""

    doc_id: str
    name: str
    page: int
    start: int
    end: int
    text: str
    span_id: int | str = 0


@dataclass(frozen=True)
class DocMeta:
    doc_id: str
    name: str
    n_pages: int = 0
    pages_without_text: tuple[int, ...] = ()
    #: False for a source with no page marks (pasted text): the page line then says so
    pages_known: bool = True


# ---- phrases ----------------------------------------------------------------------------------


class _Phrases:
    def __init__(self, asset: dict[str, Any]) -> None:
        self.asset = asset
        items: list[tuple[str, str]] = [(p, t["id"]) for t in asset["types"] for p in t["phrases"]]
        items.sort(key=lambda x: -len(x[0]))
        self.items = [(self._rx(p), p, tid) for p, tid in items]
        self.types = {t["id"]: t for t in asset["types"]}
        self.cue_tokens = frozenset(asset["cue_words"])
        self.cue_phrases = tuple(asset["cue_phrases"])
        self.cue_prefix = [re.compile(p, re.I) for p in asset["cue_prefix_patterns"]]
        self.legal = [re.compile(p, re.I) for p in asset["legal_discussion_patterns"]]
        self.reg_phrases = [p.lower() for p in asset["fir_registration_phrases"]]
        self.filing_words = frozenset(asset["charge_sheet_filing_words"])
        self.roles = tuple(asset["role_literals"])
        self.name_stop = frozenset(asset["name_stop"])
        self.abbr = frozenset(asset["abbreviations_for_cue_window"])
        self.items_words = frozenset(asset["surrender_item_words"])

    @staticmethod
    def _rx(phrase: str) -> re.Pattern[str]:
        flags = 0 if phrase in ("FIR", "F.I.R.") else re.I
        return re.compile(r"(?<![\w])" + re.escape(phrase) + r"(?![\w])", flags)

    def types_in(self, sentence: str) -> tuple[list[str], list[str]]:
        """(distinct type ids in first-seen order, the phrases matched), longest phrase first so 'judicial custody' is not also an
        arrest."""
        claimed: list[tuple[int, int]] = []
        ids: list[str] = []
        hit: list[str] = []
        for rx, phrase, tid in self.items:
            for m in rx.finditer(sentence):
                if any(m.start() < e and m.end() > s for s, e in claimed):
                    continue
                claimed.append(m.span())
                hit.append(phrase)
                if tid not in ids:
                    ids.append(tid)
        return ids, hit

    def cue_word(self, masked: str) -> str | None:
        low = masked.lower().replace("’", "'").replace("‘", "'")
        for ph in self.cue_phrases:
            if ph in low:
                return str(ph)
        for rx in self.legal:
            if rx.search(low):
                return "section provides"
        for m in re.finditer(r"[a-z][a-z'\-]*", low):
            w = m.group(0).strip("'-")
            if w == "no":
                if (
                    re.match(r"\.\s*[\d(\-]", low[m.end() : m.end() + 6])
                    or re.match(r"[:\s]?\s*[\d(]", low[m.end() : m.end() + 3])
                    and low[m.end() : m.end() + 1] == ":"
                ):
                    continue  # "No. 12", "No.12", "No: 12": the number sign
                return "no"
            if w in self.cue_tokens or any(rx.match(w) for rx in self.cue_prefix):
                return w
        return None


_MONTH_NAMES = frozenset(m.lower() for m in calendar.month_name if m) | frozenset(m.lower() for m in calendar.month_abbr if m)
_DAY_NAMES = frozenset(d.lower() for d in calendar.day_name)
_NAME_RUN = re.compile(r"\b(?:[A-Z][a-z]{2,}(?:[ ][A-Z][a-z]{2,}){0,2}|[A-Z]{3,}(?:[ ][A-Z]{3,}){0,2})\b")
_FIR_NO = re.compile(
    r"(?<![A-Za-z])(?:FIR|Crime|Cr\.?)[ ]?(?:No\.?)?[ ]?[-:(]*[ ]?([0-9]{1,6})"
    r"(?:[ ]?[/-][ ]?([0-9]{2,4})|[ ]+of[ ]+([0-9]{4}))",
    re.I,
)
_ROLE_RE = re.compile(r"\bthe (accused|petitioner|complainant|appellant|victim)\b", re.I)


def _name_candidates(sentence: str, ph: _Phrases, lower_words: frozenset[str]) -> list[str]:
    out: list[str] = []
    for m in _NAME_RUN.finditer(sentence):
        toks = m.group(0).split()
        keep = [
            t for t in toks if t.lower() not in ph.name_stop and t.lower() not in _MONTH_NAMES and t.lower() not in _DAY_NAMES
        ]
        keep = [t for t in keep if t.lower() not in lower_words]
        if not keep:
            continue
        name = " ".join(keep)
        if len(keep) >= 2 or len(keep[0]) >= 4:
            out.append(name)
    return out


_NAME_PATTERNS: dict[str, tuple[str, ...]] = {
    "arrest": (r"\b(?:arrested|apprehended)[ ]+{n}", r"{n}[ ]+(?:was|were|has been|had been)[ ]+(?:arrested|apprehended)"),
    "fir": (r"\bregistered[ ]+against[ ]+{n}", r"\bagainst[ ]+{n}[^.;]{{0,40}}\bregistered"),
    "charge_sheet": (r"\bagainst[ ]+{n}",),
    "surrender": (r"{n}[ ]+(?:surrendered|has surrendered|had surrendered)", r"\bsurrender of[ ]+{n}"),
}


def _grouping_name(type_id: str, sentence: str, ph: _Phrases, lower_words: frozenset[str]) -> str | None:
    """The ONE person the sentence is about for this type, or None (two candidates, or the person is not the object of the verb:
    "Inspector Sharma arrested Ravi" yields Ravi only when Sharma is not a candidate; a sentence with both names yields none)."""
    cands = _name_candidates(sentence, ph, lower_words)
    if len(cands) != 1:
        return None
    name = cands[0]
    for pat in _NAME_PATTERNS.get(type_id, ()):
        if re.search(pat.format(n=re.escape(name)), sentence):
            return name.casefold()
    return None


# ---- rows -------------------------------------------------------------------------------------


@dataclass
class _Row:
    scan_doc: int
    hit: Hit | None
    sent: int
    unit: int
    category: str
    types: list[str]
    several: bool
    cue: str | None
    name: str | None = None
    fir_numbers: frozenset[str] = frozenset()
    roles: frozenset[str] = frozenset()
    items: frozenset[str] = frozenset()
    registration: bool = False
    filing: bool = False
    group: int | None = None
    id: int = 0


def _fmt(d: date) -> str:
    return f"{d.day} {calendar.month_name[d.month]} {d.year}"


def how_text(h: Hit, both_conventions: bool) -> str:
    if h.not_read_reason:
        return NOT_READ_LINE
    if h.category == "approximate":
        return f'Written as approximate ("{h.text}"): not used to order or count'
    how = h.how
    if how == "written_month" and h.value:
        return f"Read as {_fmt(h.value)} (written month)"
    if how == "iso" and h.value:
        return f"Read as {h.value.isoformat()} (year-month-day)"
    if how == "only_valid_reading" and h.value:
        return f"Read as {_fmt(h.value)} (only one order of day and month is possible)"
    if how == "day_equals_month" and h.value:
        return f"Read as {_fmt(h.value)} (day and month are the same number)"
    if how in ("day_first_by_document_evidence", "month_first_by_document_evidence") and h.value and h.evidence:
        side = "day" if how.startswith("day") else "month"
        return (
            f"Read as {_fmt(h.value)} ({side} first: another date in this paper only works {side} first: "
            f"{h.evidence['text']}, page {h.evidence['page']}). Check it."
        )
    if how == "range" and h.value and h.end_value:
        return f"Read as a range: {_fmt(h.value)} to {_fmt(h.end_value)}"
    if how == "two_digit_with_century_evidence" and h.value:
        return f"Read as {_fmt(h.value)} (two-digit year: another date in this paper gives the century). Check it."
    if how == "ambiguous" and h.readings:
        a, b = h.readings
        extra = f" {BOTH_CONVENTIONS}" if both_conventions else ""
        return f"{AMBIGUOUS_QUESTION}: {_fmt(a)} or {_fmt(b)}? Not decided.{extra}"
    if h.category == "less_than_a_day":
        return f"Written as a partial date ({h.precision}): not ordered, not counted as a day"
    return ""


# ---- the report -------------------------------------------------------------------------------


def _paper_type(name: str, asset: dict[str, Any]) -> tuple[str, bool]:
    toks = [t for t in re.split(r"[^a-z0-9]+", name.lower()) if t]
    joined = " ".join(toks)
    matched = []
    for key, label in asset["paper_types_from_file_name"]:
        k = key.lower().split()
        if all(t in toks for t in k) and re.search(r"\b" + r"\s+".join(map(re.escape, k)) + r"\b", joined):
            matched.append(label)
    if len(set(matched)) == 1:
        return matched[0], True
    return "paper type not stated", False


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def build_chronology(
    spans: list[SpanRec],
    docs: list[DocMeta],
    cfg: ChronologyConfig,
    today: date | None = None,
    asset: dict[str, Any] | None = None,
) -> dict[str, Any]:
    today = today or date.today()
    asset = asset or load_asset()
    ph = _Phrases(asset)
    t0 = time.monotonic()
    by_doc: dict[str, list[SpanRec]] = defaultdict(list)
    for sp in spans:
        by_doc[sp.doc_id].append(sp)
    stopped: str | None = None
    searched = chars = 0
    scans: list[tuple[DocMeta, Any, frozenset[str]]] = []
    for meta in docs:
        dspans = sorted(by_doc.get(meta.doc_id, []), key=lambda s: s.start)
        if not dspans:
            continue
        n = sum(len(s.text) for s in dspans)
        if searched + len(dspans) > cfg.max_passages or chars + n > cfg.max_chars or time.monotonic() - t0 > cfg.max_seconds:
            stopped = "cap"
            continue
        sc = scan_document([(s.doc_id, s.page, s.start, s.text, s.span_id) for s in dspans], today)
        lower = frozenset(w for s in dspans for w in re.findall(r"\b[a-z]{3,}\b", s.text))
        scans.append((meta, sc, lower))
        searched += len(dspans)
        chars += n
    rows: list[_Row] = []
    for di, (_meta, sc, lower) in enumerate(scans):
        sent_hits: dict[int, list[Hit]] = defaultdict(list)
        for h in sc.hits:
            sent_hits[h.sent].append(h)
        for si, st in enumerate(sc.sents):
            if st.unread_language:
                continue
            u = sc.units[st.unit]
            text = u.text[st.start : st.end]
            hs = sent_hits.get(si, [])
            masked = list(text)
            for h in hs:
                for k in range(max(h.start, st.start), min(h.end, st.end)):
                    masked[k - st.start] = "\x00"
            ms = "".join(masked)
            ids, _hit = ph.types_in(ms)
            cue = ph.cue_word(ms)
            if cue is None and st.cut_fragment:
                cue = "cut_fragment"
            elif cue is None and st.other_language:
                cue = "other_language"
            low = ms.lower()
            real = [h for h in hs if h.category not in ("printed", "number_like")]
            several = len(real) > 1 or len(ids) > 1
            base = dict(
                scan_doc=di,
                sent=si,
                unit=st.unit,
                types=ids,
                several=several,
                cue=cue,
                registration=any(p in low for p in ph.reg_phrases),
                filing=any(re.search(rf"\b{w}\b", low) for w in ph.filing_words),
                roles=frozenset(r for r in ph.roles if r in low),
                items=frozenset(w for w in ph.items_words if re.search(rf"\b{w}\b", low)),
                fir_numbers=frozenset(f"{m.group(1)}/{m.group(2) or m.group(3)}" for m in _FIR_NO.finditer(text)),
            )
            if not hs:
                if ids:
                    row = _Row(hit=None, category="undated", **base)
                    cands = _name_candidates(text, ph, lower)
                    row.name = cands[0] if cands else None  # a person named: more likely a real event
                    rows.append(row)
                continue
            for h in hs:
                r = _Row(hit=h, category=h.category, **base)
                if len(ids) == 1 and not several and not h.reference and cue is None and h.category == "exact":
                    r.name = _grouping_name(ids[0], text, ph, lower)
                rows.append(r)
    for i, r in enumerate(rows):
        r.id = i + 1
    groups, related = _group(rows, ph)
    for gi, g in enumerate(groups, 1):
        for r in g:
            r.group = gi

    order = {m.doc_id: i for i, (m, _s, _l) in enumerate(scans)}

    def where(r: _Row) -> tuple[int, int, int]:
        meta, sc, _l = scans[r.scan_doc]
        u = sc.units[r.unit]
        at = r.hit.start if r.hit else sc.sents[r.sent].start
        return order[meta.doc_id], u.page, u.start + at

    def by_date(r: _Row) -> tuple[date, int, int, int]:
        assert r.hit is not None and r.hit.value is not None
        return (r.hit.value, *where(r))

    cats: dict[str, list[_Row]] = {
        c: []
        for c in (
            "exact",
            "ambiguous",
            "approximate",
            "less_than_a_day",
            "scheduled",
            "undated",
            "not_read",
            "printed",
            "number_like",
        )
    }
    for r in rows:
        cats[r.category].append(r)
    cats["exact"].sort(key=by_date)
    for c in cats:
        if c == "undated":
            cats[c].sort(key=lambda r: (0 if r.name and r.cue is None else 1, 0 if r.name else 1, *where(r)))
        elif c != "exact":
            cats[c].sort(key=where)
    found = len(rows)
    assert found == sum(len(v) for v in cats.values())

    def row_out(r: _Row) -> dict[str, Any]:
        meta, sc, _l = scans[r.scan_doc]
        u = sc.units[r.unit]
        st = sc.sents[r.sent]
        h = r.hit
        s, e = st.start, st.end
        while s < e and u.text[s].isspace():
            s += 1
        while e > s and u.text[e - 1].isspace():
            e -= 1
        if e - s > MAX_QUOTE:
            centre = h.start if h else s
            s = max(s, min(centre - MAX_QUOTE // 2, e - MAX_QUOTE))
            e = s + MAX_QUOTE
        ptype, from_name = _paper_type(meta.name, asset)
        if r.category == "printed":
            label = PRINTED_LINE
        elif h is not None and (h.reference or r.category in ("number_like", "not_read")):
            label = DATE_ONLY
        elif r.several:
            label = SEVERAL
        elif r.types:
            label = str(ph.types[r.types[0]]["label"])
        else:
            label = DATE_ONLY
        notes: list[str] = []
        if h is not None and h.reference:
            notes.append(REFERENCE_NOTE)
        if h is not None and h.scheduled:
            notes.append(SCHEDULED_NOTE)
        if r.types and "bail" in r.types and not r.several:
            notes.append(BAIL_NOTE)
        out: dict[str, Any] = {
            "id": r.id,
            "date_as_written": h.text if h else None,
            "normalised": h.value.isoformat() if h and h.value else None,
            "range_end": h.end_value.isoformat() if h and h.end_value else None,
            "how": (h.how if h and not h.not_read_reason else "not_read") if h else None,
            "how_text": how_text(h, h.both_conv) if h else None,
            "not_read_reason": h.not_read_reason if h else None,
            "evidence": h.evidence if h else None,
            "readings": [d.isoformat() for d in h.readings] if h and h.readings else None,
            "precision": h.precision if h else None,
            "approximate": bool(h and h.approximate),
            "reference": bool(h and h.reference),
            "scheduled": bool(h and h.scheduled),
            "quoted": bool(h and h.quoted),
            "mentions": label,
            "event_type": None if r.several or not r.types or (h is not None and h.reference) else r.types[0],
            "cue_word": r.cue,
            "notes": notes,
            "quote": u.text[s:e],
            "doc_id": meta.doc_id,
            "document_name": _esc(meta.name),
            "paper_type": ptype,
            "paper_type_note": FILE_NAME_BASIS if from_name else None,
            "page": u.page,
            "start": u.start + s,
            "end": u.start + e,
            "mention_start": u.start + h.start if h else None,
            "mention_end": u.start + h.end if h else None,
            "span_id": u.span_id,
            "span_sha256": hashlib.sha256(u.text.encode("utf-8", "surrogatepass")).hexdigest(),
            "group_id": r.group,
        }
        return out

    shown = {c: [row_out(r) for r in v[: cfg.max_rows]] for c, v in cats.items()}
    little = sum(len(m.pages_without_text) for m in docs)
    lang_unread = sum(sc.counts["sentences_not_read_language"] for _m, sc, _l in scans)
    like_unread = sum(sc.counts["like_in_unread_sentences"] for _m, sc, _l in scans)
    total = len(spans)
    cnt = {c: len(v) for c, v in cats.items()}
    cut_note = f" Showing the first {cfg.max_rows} of each list." if any(x > cfg.max_rows for x in cnt.values()) else ""
    pages_clause = (
        f"{little} pages had little or no text and may not have been searched in full."
        if all(m.pages_known for m in docs)
        else "Pages not known: this file has no page marks."
    )
    lang = f" {lang_unread} sentences in a language other than English were not read." if lang_unread else ""
    line = (
        f"{found} lines listed: {cnt['exact']} exact dates; {cnt['ambiguous']} with day or month first to check; "
        f"{cnt['approximate']} approximate; {cnt['less_than_a_day']} partial dates; {cnt['scheduled']} listing dates; "
        f"{cnt['undated']} sentences that mention a topic but no date; {cnt['not_read']} with a date we could not read; "
        f"{cnt['printed']} printed lines; {cnt['number_like']} case or document numbers. "
        f"Searched {searched} of {total} passages; {pages_clause}{lang}{cut_note}"
    )
    papers = [
        {
            "doc_id": m.doc_id,
            "name": _esc(m.name),
            "paper_type": _paper_type(m.name, asset)[0],
            "pages": m.n_pages,
            "pages_with_little_or_no_text": list(m.pages_without_text),
        }
        for m in docs
    ]
    toks_all = {t for m in docs for t in re.split(r"[^a-z0-9]+", m.name.lower()) if t}
    not_added = [p for p in asset["standard_papers"] if p.split()[0].lower().rstrip("s") not in toks_all]
    return {
        "statement": DATA_STATEMENT,
        "standing_line": STANDING_LINE,
        "scope_line": SCOPE_LINE,
        "language_line": LANGUAGE_LINE,
        "headers": HEADERS,
        "headings": HEADINGS,
        "denominator": {
            "lines": found,
            **cnt,
            "passages_searched": searched,
            "passages_total": total,
            "pages_with_little_or_no_text": little,
            "sentences_not_read_language": lang_unread,
            "like_in_unread_sentences": like_unread,
            "ambiguous_in_files_with_both_conventions": sum(
                1 for r in cats["ambiguous"] if r.hit is not None and r.hit.both_conv
            ),
            "rows_shown": {c: len(v) for c, v in shown.items()},
        },
        "denominator_line": line,
        "events": shown["exact"],
        "ambiguous": shown["ambiguous"],
        "approximate": shown["approximate"],
        "partial_dates": shown["less_than_a_day"],
        "scheduled": shown["scheduled"],
        "undated": shown["undated"],
        "undated_line": UNDATED_LINE,
        "date_not_read": shown["not_read"],
        "date_not_read_line": NOT_READ_LINE,
        "printed_lines": shown["printed"],
        "number_like": shown["number_like"],
        "groups": [
            {"heading": GROUP_HEADING, "line": GROUP_LINE, "row_ids": [r.id for r in g], "rows": [row_out(r) for r in g]}
            for g in groups
        ],
        "possibly_related": [{"line": POSSIBLY_RELATED, "row_ids": [a.id, b.id]} for a, b in related[:MAX_POSSIBLY_RELATED]],
        "possibly_related_more": max(0, len(related) - MAX_POSSIBLY_RELATED),
        "papers_read": papers,
        "papers_not_added_yet_that_may_matter": [f"No file whose name looks like: {p}" for p in not_added],
        "custody_days": "cannot say yet",
        "stopped": stopped,
    }


# ---- grouping ---------------------------------------------------------------------------------


def _groupable(r: _Row, ph: _Phrases) -> bool:
    h = r.hit
    if (
        h is None
        or r.several
        or len(r.types) != 1
        or r.cue is not None
        or r.category != "exact"
        or h.reference
        or h.how == "range"
    ):
        return False
    t = ph.types[r.types[0]]
    if not t["groupable"]:
        return False
    if t["id"] == "fir":
        return r.registration
    if t["id"] == "charge_sheet":
        return r.filing
    return True


def _group(rows: list[_Row], ph: _Phrases) -> tuple[list[list[_Row]], list[tuple[_Row, _Row]]]:
    """Rows that may be the same event with different dates: the same groupable type, the SAME single person named in both
    sentences (the object of the verb), different documents, different dates, no cue, no differing FIR number or item. Hashed
    on (type, name), never all-pairs. A role-literal-only pair is a non-claim line (capped by the caller)."""
    cands = [r for r in rows if _groupable(r, ph)]
    buckets: dict[tuple[str, str], list[_Row]] = defaultdict(list)
    for r in cands:
        if r.name:
            buckets[(r.types[0], r.name)].append(r)
    groups: list[list[_Row]] = []
    for g in buckets.values():
        # O(n) per bucket: within one compatibility key a row is a member iff some OTHER row has a different document AND a
        # different date: (rows) - (same document) - (same date) + (same document and same date) > 0.
        by_key: dict[tuple[frozenset[str], frozenset[str]], list[_Row]] = defaultdict(list)
        for r in g:
            if r.hit is not None:
                by_key[_compat_key(r)].append(r)
        members: list[_Row] = []
        for rows in by_key.values():
            n_doc = Counter(r.scan_doc for r in rows)
            n_val = Counter(r.hit.value for r in rows if r.hit is not None)
            n_both = Counter((r.scan_doc, r.hit.value) for r in rows if r.hit is not None)
            for r in rows:
                assert r.hit is not None
                if len(rows) - n_doc[r.scan_doc] - n_val[r.hit.value] + n_both[(r.scan_doc, r.hit.value)] > 0:
                    members.append(r)
        if len(members) > 1 and len({m.scan_doc for m in members}) > 1:
            groups.append(members)
    related: list[tuple[_Row, _Row]] = []
    by_type: dict[tuple[str, str], list[_Row]] = defaultdict(list)
    for r in cands:
        if not r.name:
            for role in r.roles:
                by_type[(r.types[0], role)].append(r)
    for g in by_type.values():
        for i in range(len(g)):
            for j in range(i + 1, min(len(g), i + 8)):
                a, b = g[i], g[j]
                if a.scan_doc != b.scan_doc and a.hit and b.hit and a.hit.value != b.hit.value:
                    related.append((a, b))
    return groups, related


def _compat_key(r: _Row) -> tuple[frozenset[str], frozenset[str]]:
    """Rows group only with rows of the same key: the same FIR number(s) (a number in one and none in the other does not group;
    neither carrying one is fine) and, for a surrender, the same item."""
    return (r.fir_numbers if r.types[0] == "fir" else frozenset(), r.items if r.types[0] == "surrender" else frozenset())


