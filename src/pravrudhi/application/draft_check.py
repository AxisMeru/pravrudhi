"""A1: the whole-draft citation check, first slice (flag `wf_a1_draft_citation_check`, default off).

A brief, petition or memo goes in (pasted text, or a text-layer PDF); every citation in it is checked against the citation
index, and the answer says, in numbers, how much of the draft it could read. Existence only: "found in our index" never means
"the case says what the draft says it says".

The report is built so that it can never look clean by omission:

* a separate SCANNER counts every string that LOOKS like a citation (a reporter token such as SCC, AIR, SCR, MANU, SCC OnLine
  or Cri LJ with a number beside it, in any damaged spelling: a hyphen at a line break, a zero-width character, an OCR letter
  for a digit, a bracketed year). `like = read + not_read` holds for every input; a string the parser did not read is LISTED
  as not read;
* a scanned page, an unreadable page, a cap that was hit and a lookup that failed all raise the "not read" / "not checked"
  counts and turn the state to `incomplete`; the summary never says "no problems" without saying how much was not read;
* a citation of a reporter the index does not hold (AIR, SCC OnLine, ...) is "not checked: reporter not in the index", never
  "not found".

This module is pure apart from the lookup it is handed (`lookup(text) -> (result, name_compared)`) and the PDF text layer it
reads with `pypdf` (already a dependency). It writes nothing and calls no model. The route that serves it lives in
`api/partner.py`, registered only when the flag is on.

Audit: the route writes the existing audit mode "intake" with a run id starting "draft-check-" (the audit contract's modes are
public and unchanged); that prefix tells a reader of the audit log a draft check from an intake.
"""

from __future__ import annotations

import base64
import binascii
import bisect
import io
import os
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field, StrictStr, model_validator

from pravrudhi.application.a0_assist import intake
from pravrudhi.application.citations import Citation, parse_citations
from pravrudhi.application.draft_check_config import DraftCheckConfig

REPORT_VERSION = 1
#: reporters whose citations the index can answer (SCC family); every other parsed reporter is "not checked: reporter not in the
#: index"
CHECKABLE_REPORTERS = frozenset({"SCC", "SCC Supp"})
MAX_LISTED = 5_000  # items listed in one response; the counts stay exact beyond it
MAX_NAME_CHARS = 300  # a typed name longer than this is dropped (the lookup text is held to 500 characters)

NOT_ASSESSED = (
    "whether a case says what the draft says it says",
    "whether a case is still good law",
    "citations of a kind the index does not hold (shown as not checked)",
)


ROUTE = "/api/v1/draft-citation-check"


def open_route_lists() -> None:
    """Add the route to the two classification lists that must name it while it exists: the whole-surface gate's key-only routes
    (`identity.PARTNER_KEY_ROUTES` / `PARTNER_KEY_PATHS`) and `roles.USER_FACING`. Called only when the route is registered (flag
    on), so with the flag off every list is exactly the released one."""
    from pravrudhi.api import identity, roles

    identity.PARTNER_KEY_ROUTES = identity.PARTNER_KEY_ROUTES | {("POST", ROUTE)}
    identity.PARTNER_KEY_PATHS = identity.PARTNER_KEY_PATHS | {ROUTE}
    roles.USER_FACING = roles.USER_FACING | {ROUTE}


class DraftRefused(Exception):
    """The draft cannot be read at all (a 422 or 413 to the caller); `code` is a stable machine token."""

    def __init__(self, code: str, message: str, status_code: int = 422) -> None:
        super().__init__(message)
        self.code, self.status_code = code, status_code


class DraftCheckRequest(BaseModel):
    """Exactly one of `text` and `pdf_base64`. Nothing is stored."""

    text: StrictStr | None = Field(default=None, max_length=1_000_000)
    pdf_base64: StrictStr | None = Field(default=None, max_length=11_000_000)

    @model_validator(mode="after")
    def _one_source(self) -> DraftCheckRequest:
        if (self.text is None) == (self.pdf_base64 is None):
            raise ValueError("send exactly one of text and pdf_base64")
        if self.text is not None and not self.text.strip():
            raise ValueError("text must not be blank")
        return self


# ---- reading the draft ----------------------------------------------------------------------------------------------------


def pdf_text(
    data: bytes, *, max_pages: int, max_seconds: float | None = None, clock: Callable[[], float] = time.monotonic
) -> str:
    """The text layer of a PDF, pages joined by form feed (the intake convention). A page that cannot be read gives "". An
    encrypted or unreadable file, or one over the page cap, is refused with a stated reason. Pages not extracted before
    `max_seconds` run out give "" (so they are counted as pages with no text, never silently dropped)."""
    started = clock()
    from pypdf import PdfReader

    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise DraftRefused("pdf_encrypted", "the PDF is encrypted; send the text instead")
        n_pages = len(reader.pages)
    except DraftRefused:
        raise
    except Exception as e:  # noqa: BLE001 - any parser failure is "not a PDF we can read"
        raise DraftRefused("pdf_unreadable", "the PDF cannot be read") from e
    if n_pages == 0:
        raise DraftRefused("pdf_unreadable", "the PDF has no pages")
    if n_pages > max_pages:
        raise DraftRefused("pdf_too_many_pages", f"the PDF has more than {max_pages} pages", 413)
    pages: list[str] = []
    for page in reader.pages:
        if max_seconds is not None and clock() - started > max_seconds:
            pages.append("")
            continue
        try:
            pages.append(page.extract_text() or "")
        except Exception:  # noqa: BLE001 - an unreadable page is "not read", counted below
            pages.append("")
    return "\f".join(pages)


def read_draft(req: DraftCheckRequest, cfg: DraftCheckConfig) -> str:
    if req.pdf_base64 is not None:
        if len(req.pdf_base64) > (cfg.max_pdf_bytes * 4) // 3 + 16:
            raise DraftRefused("pdf_too_large", f"the PDF is larger than {cfg.max_pdf_bytes} bytes", 413)
        try:
            data = base64.b64decode(req.pdf_base64, validate=True)
        except (binascii.Error, ValueError) as e:
            raise DraftRefused("pdf_bad_encoding", "pdf_base64 is not valid base64") from e
        if len(data) > cfg.max_pdf_bytes:
            raise DraftRefused("pdf_too_large", f"the PDF is larger than {cfg.max_pdf_bytes} bytes", 413)
        text = pdf_text(data, max_pages=cfg.max_pdf_pages, max_seconds=cfg.max_seconds)
    else:
        text = req.text or ""
    if len(text) > cfg.max_chars:
        raise DraftRefused("too_long", f"the draft is longer than {cfg.max_chars} characters", 413)
    if not text.strip():
        raise DraftRefused("no_text", "the draft has no text to read (a scanned PDF has no text layer)")
    return text


# ---- the scanner: strings that LOOK like citations ------------------------------------------------------------------------

#: a gap inside a token: line-break hyphen, soft hyphen, zero-width characters, a few spaces
_GAP = r"[\s­​-‍⁠﻿\-]{0,3}"


def _spaced(word: str) -> str:
    return _GAP.join(re.escape(c) for c in word)


#: Upper-case reporter tokens (case-sensitive: "air" and "scc" in prose are not anchors) and the forms the parser does not read.
_ANCHOR_CS = re.compile(
    rf"(?<![A-Za-z]){_spaced('SCC')}(?![A-Za-z])|(?<![A-Za-z]){_spaced('SCR')}(?![A-Za-z])"
    rf"|(?<![A-Za-z]){_spaced('AIR')}(?![A-Za-z])"
    r"|(?<![A-Za-z])MANU\s{0,2}/|(?<![A-Za-z])INSC(?![A-Za-z])|(?<![A-Za-z])JT(?![A-Za-z])|(?<![A-Za-z])SCALE(?![A-Za-z])"
)
_ANCHOR_CI = re.compile(r"\bOnLine\b|(?<![A-Za-z])Cri\.?\s{0,2}L\.?\s{0,2}J\b", re.IGNORECASE)
_DIGIT = re.compile(r"\d")
#: a year-like run with at least one real digit (OCR may put the letters O, o, l, I in a number)
_YEARISH = re.compile(r"(?=[0-9OolI]*\d)[0-9OolI]{4}")
_STRIP = re.compile(r"[\s\u00ad\u200b-\u200d\u2060\ufeff\-]+")
_REACH_AFTER = 24
_REACH_BEFORE = 16


@dataclass(frozen=True)
class LikeRegion:
    """One string that looks like a citation: its display window, its first anchor token and the spans of its anchors (an
    anchor is the reporter token itself; the region is READ when any anchor lies inside a citation the parser read)."""

    start: int
    end: int
    anchor: str
    anchors: tuple[tuple[int, int], ...]


_ANCHOR_JOIN = 8  # anchors this close are one citation ("SCC" and "OnLine" in "2014 SCC OnLine SC 99")


def like_citation_regions(text: str) -> list[LikeRegion]:
    """Strings of `text` that look like a citation: a reporter token with a number beside it. Anchors within a few characters
    of each other are one string; separate citations stay separate however close they are. Linear: every pattern has bounded
    gaps."""
    found: list[tuple[int, int, int, int, str]] = []  # anchor start, end, window start, window end, token
    n = len(text)
    for rx in (_ANCHOR_CS, _ANCHOR_CI):
        for m in rx.finditer(text):
            after = text[m.end() : m.end() + _REACH_AFTER]
            before = text[max(0, m.start() - _REACH_BEFORE) : m.start()]
            digit_after = _DIGIT.search(after) is not None
            yms = list(_YEARISH.finditer(before))
            ym = yms[-1] if yms else None  # the year nearest the anchor
            if not (digit_after or ym):
                continue
            w0 = m.start()
            if ym:  # the window opens at the year (and its bracket), never earlier: it must not reach into the text before
                w0 = max(0, m.start() - _REACH_BEFORE) + ym.start()
                if w0 > 0 and text[w0 - 1] in "([":
                    w0 -= 1
            w1 = min(n, m.end() + _REACH_AFTER) if digit_after else m.end()
            found.append((m.start(), m.end(), w0, w1, _STRIP.sub("", m.group(0))[:12]))
    found.sort()
    out: list[LikeRegion] = []
    for a0, a1, w0, w1, tok in found:
        if out and a0 - out[-1].anchors[-1][1] <= _ANCHOR_JOIN:
            prev = out[-1]
            out[-1] = LikeRegion(min(prev.start, w0), max(prev.end, w1), prev.anchor, (*prev.anchors, (a0, a1)))
        else:
            out.append(LikeRegion(w0, w1, tok, ((a0, a1),)))
    # a display window never reaches into a neighbouring citation
    clipped: list[LikeRegion] = []
    for i, reg in enumerate(out):
        lo = max(reg.start, out[i - 1].anchors[-1][1] + 1) if i else reg.start
        hi = min(reg.end, out[i + 1].anchors[0][0] - 1) if i + 1 < len(out) else reg.end
        clipped.append(LikeRegion(min(lo, reg.anchors[0][0]), max(hi, reg.anchors[-1][1]), reg.anchor, reg.anchors))
    return clipped


# ---- the typed name in front of a citation --------------------------------------------------------------------------------

_SEP = re.compile(r"\s(?:v\.?|vs\.?|v/s\.?|versus)\s", re.IGNORECASE)
_CONNECT = frozenset({"of", "and", "the", "&", "for", "re", "another", "others", "ors", "ors.", "anr", "anr."})
#: capitalised words that open a sentence in front of a name ("See A v. B", "In A v. B"): never part of the name
_LEAD_WORDS = frozenset(
    {"see", "also", "cf", "per", "vide", "as", "in", "held", "citing", "following", "under", "but", "however", "thus", "hence"}
)
_RIGHT_LOWER_OK = _CONNECT | {"in", "on", "to", "by"}


def extract_name_before(text: str, start: int, floor: int = 0) -> str | None:
    """The "A v. B" typed immediately in front of a citation that starts at `start`, or None (name not read). Fails closed: a
    name that cannot be bounded with confidence (prose words inside the right party, a sentence break, too many words) is None,
    never a guess. `floor` is the offset the name may not reach back past (the end of the previous citation)."""
    seg = text[max(floor, start - 220) : start]
    seg = re.sub(r"[ \t]*\n[ \t]*", " ", seg.rstrip(" \t\r\n,;:(["))
    if "\f" in seg or seg.count("  ") > 3:
        return None
    seps = list(_SEP.finditer(seg))
    if not seps:
        return None
    sep = seps[-1]
    right = seg[sep.end() :].strip(" \"'“”‘’")
    left = seg[: sep.start()]
    r_words = right.split()
    if not r_words or len(r_words) > 12 or re.search(r"[;:]", right):
        return None
    for w in r_words:
        core = w.strip(".,&()'\"")
        if core and core[0].islower() and core.lower() not in _RIGHT_LOWER_OK:
            return None  # prose inside the right party: not a name
    taken: list[str] = []
    for w in reversed(left.split()):
        if len(taken) >= 9:
            break
        core = w.strip(".,&()'\"\u201c\u201d")
        if taken and w[-1:] in ",;:)\"\u201d":  # the previous clause ends here
            break
        if core.lower() in _LEAD_WORDS and core.lower() not in _CONNECT:
            break
        if not core or core[0].isupper() or core[0].isdigit() or core.lower() in _CONNECT or w == "&":
            taken.append(w)
        else:
            break
    if not taken:
        return None
    lead = " ".join(reversed(taken)).lstrip("\"\u201c(").strip()
    name = f"{lead} v. {right}"
    return name if len(name) <= MAX_NAME_CHARS else None


# ---- grouping and quotation flags -----------------------------------------------------------------------------------------

_PARALLEL_GAP = re.compile(r"^[\s:;,=]{1,4}$")


def _quoted(text: str, start: int) -> bool:
    """True when the citation sits inside an open quotation: more opening than closing quotation marks in its paragraph so far."""
    para = text[max(text.rfind("\n\n", 0, start) + 1, start - 2_000) : start]
    opens = para.count("“") - para.count("”")
    straight = para.count('"')
    return opens > 0 or straight % 2 == 1


# ---- the report -----------------------------------------------------------------------------------------------------------

Lookup = Callable[[str], tuple[str, bool]]


@dataclass
class _Occ:
    n: int
    cite: Citation
    raw: str
    name: str | None
    quoted: bool
    group: int | None = None
    status: str = "not_checked"
    reason: str | None = None
    name_state: str = "name_not_read"


def _page_starts(text: str) -> list[int] | None:
    """Offsets where each page starts (pages are form-feed separated), or None for a text with no form feed."""
    if "\f" not in text:
        return None
    return [0] + [i + 1 for i, c in enumerate(text) if c == "\f"]


def _page_of(starts: list[int] | None, pos: int) -> int | None:
    return None if starts is None else bisect.bisect_right(starts, pos)


def _snippet(text: str, s: int, e: int, width: int = 90) -> str:
    return " ".join(text[s : min(e, s + width)].split())


def check_draft(
    text: str,
    cfg: DraftCheckConfig,
    lookup: Lookup | None,
    *,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    """The report for one draft. `lookup(text)` returns `(result, name_compared)` for a citation text (the engine's own existence
    answer: IN_INDEX, NOT_IN_INDEX, CONFLICT, NAME_MISMATCH, ...); an exception from it is one citation "not checked: lookup
    failed"."""
    started = clock()
    parsed = sorted(parse_citations(text), key=lambda c: c.span)
    pages = text.split("\f") if "\f" in text else None
    starts = _page_starts(text)
    unread_pages = [i + 1 for i in intake.unread_pages(pages)] if pages is not None else []

    # one record per parsed citation occurrence
    occs: list[_Occ] = []
    prev_end = 0
    next_gid = 0
    for i, c in enumerate(parsed, 1):
        raw = text[c.span[0] : c.span[1]]
        name = extract_name_before(text, c.span[0], prev_end) if c.reporter in CHECKABLE_REPORTERS else None
        occ = _Occ(i, c, raw, name, _quoted(text, c.span[0]))
        if occs and _PARALLEL_GAP.match(text[prev_end : c.span[0]]):  # a parallel citation of the previous one
            if occs[-1].group is None:
                next_gid += 1
                occs[-1].group = next_gid
            occ.group = occs[-1].group
        occs.append(occ)
        prev_end = c.span[1]

    # lookups, each distinct text once, under the caps
    answers: dict[str, tuple[str, bool] | str] = {}
    looked_up = 0
    for occ in occs:
        if occ.cite.reporter not in CHECKABLE_REPORTERS:
            occ.status, occ.reason = "not_checked", "reporter_not_in_index"
            continue
        if lookup is None:  # this deployment has no citation index: nothing can be checked, and the report says so
            occ.status, occ.reason = "not_checked", "no_index"
            continue
        ltext = f"{occ.name} {occ.raw}" if occ.name else occ.raw
        if len(ltext) > 500:
            ltext = occ.raw
            occ.name = None
        if ltext not in answers:
            if looked_up >= cfg.max_citations:
                answers[ltext] = "cap_reached"
            elif clock() - started > cfg.max_seconds:
                answers[ltext] = "time_cap"
            else:
                looked_up += 1
                try:
                    answers[ltext] = lookup(ltext)
                except Exception:  # noqa: BLE001 - one failed lookup is one "not checked", never a lost report
                    answers[ltext] = "lookup_failed"
        got = answers[ltext]
        if isinstance(got, str):
            occ.status, occ.reason = "not_checked", got
            continue
        result, compared = got
        if result == "IN_INDEX":
            occ.status = "found"
            occ.name_state = "name_matched" if compared else ("name_not_confirmed" if occ.name else "name_not_read")
        elif result == "NOT_IN_INDEX":
            occ.status = "not_found"
        elif result == "CONFLICT":
            occ.status = "conflict"
        elif result == "NAME_MISMATCH":
            occ.status, occ.name_state = "name_mismatch", "name_mismatch"
        else:
            occ.status, occ.reason = "not_checked", "lookup_failed"

    # the scanner: strings that look like citations but were not read
    covered = [c.span for c in parsed]
    unread = [
        reg
        for reg in like_citation_regions(text)
        if not any(cs < a1 and a0 < ce for a0, a1 in reg.anchors for cs, ce in covered)
    ]

    def count(st: str) -> int:
        return sum(1 for o in occs if o.status == st)

    nc = [o for o in occs if o.status == "not_checked"]
    nc_reporter = sum(1 for o in nc if o.reason == "reporter_not_in_index")
    nc_cap = sum(1 for o in nc if o.reason in ("cap_reached", "time_cap"))
    nc_failed = sum(1 for o in nc if o.reason == "lookup_failed")
    nc_no_index = sum(1 for o in nc if o.reason == "no_index")
    found, not_found, conflict, mismatch = count("found"), count("not_found"), count("conflict"), count("name_mismatch")
    read = len(occs)
    checkable = sum(1 for o in occs if o.cite.reporter in CHECKABLE_REPORTERS)
    denominator: dict[str, Any] = {
        "like_strings": read + len(unread),
        "read": read,
        "not_read": len(unread),
        "looked_up": looked_up,
        "found": found,
        "found_name_matched": sum(1 for o in occs if o.status == "found" and o.name_state == "name_matched"),
        "found_name_not_confirmed": sum(1 for o in occs if o.status == "found" and o.name_state != "name_matched"),
        "not_found": not_found,
        "conflict": conflict,
        "name_mismatch": mismatch,
        "not_checked": len(nc),
        "not_checked_reporter_not_in_index": nc_reporter,
        "not_checked_cap": nc_cap,
        "not_checked_lookup_failed": nc_failed,
        "not_checked_no_index": nc_no_index,
        "quoted": sum(1 for o in occs if o.quoted),
        "pages_total": len(pages) if pages is not None else None,
        "pages_not_read": len(unread_pages),
    }
    assert denominator["like_strings"] == denominator["read"] + denominator["not_read"]
    assert read == found + not_found + conflict + mismatch + len(nc)
    problems = not_found + conflict + mismatch
    incomplete = bool(unread) or bool(unread_pages) or nc_cap > 0 or nc_failed > 0 or nc_no_index > 0
    one = len(unread) == 1
    not_read_note = (
        f"{len(unread)} string{'' if one else 's'} that look{'s' if one else ''} like a citation "
        f"{'was' if one else 'were'} not read"
    )
    if pages is not None and unread_pages:
        not_read_note += f"; {len(unread_pages)} of {len(pages)} pages have no text layer"
    why = not_read_note if (unread or unread_pages) else f"{nc_cap + nc_failed + nc_no_index} citations were not checked"
    if problems:
        summary = (
            f"{problems} of the {read} citations read need attention: {not_found} not found in our index, "
            f"{conflict} ambiguous, {mismatch} with a case name that did not match."
        )
        if incomplete:
            summary += f" The check is incomplete: {why}."
    elif read == 0 and incomplete:
        summary = f"No citation could be read from this draft, and the check is incomplete: {why}."
    elif read == 0:
        summary = "No citation was found in this draft."
    elif incomplete:
        summary = f"No problems found among the {read} citations read, but the check is incomplete: {why}."
    else:
        summary = f"No problems found among the {read} citations read."
    listed = [
        {
            "n": o.n,
            "text": " ".join(o.raw.split())[:200],
            "span": [o.cite.span[0], o.cite.span[1]],
            "page": _page_of(starts, o.cite.span[0]),
            "reporter": o.cite.reporter,
            "status": o.status,
            "reason": o.reason,
            "name": o.name,
            "name_state": o.name_state if o.cite.reporter in CHECKABLE_REPORTERS else None,
            "quoted": o.quoted,
            "group": o.group,
        }
        for o in occs[:MAX_LISTED]
    ]
    unread_listed = [
        {"text": _snippet(text, g.start, g.end), "span": [g.start, g.end], "page": _page_of(starts, g.start), "anchor": g.anchor,
         "reason": "form_not_read"}
        for g in unread[:MAX_LISTED]
    ]
    return {
        "run_id": f"draft-check-{os.urandom(5).hex()}",
        "client_data": True,
        "draft_check": {
            "version": REPORT_VERSION,
            "state": "incomplete" if incomplete else "complete",
            "summary": summary,
            "denominator": denominator,
            "checked": {"checked": checkable - nc_cap - nc_failed - nc_no_index, "of": checkable},
            "citations": listed,
            "not_read": unread_listed,
            "unread_pages": unread_pages,
            "listing_truncated": len(occs) > MAX_LISTED or len(unread) > MAX_LISTED,
            "not_assessed": list(NOT_ASSESSED),
            "wording": "found in our index means the citation exists in the index, not that the case says what the draft says",
        },
    }

