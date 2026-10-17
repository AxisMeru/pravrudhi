# ruff: noqa: E501
"""Cheque-case check, the logic (A6 slice 1; no model, no network). One NI Act s.138 matter, two existing pieces side by side: the papers
agenda (A5) and the date checklist (B3). This module adds the one thing neither has: for each date B3 asks for (cheque, presentation,
return memo, bank's information, notice, receipt of the notice), the dates a matter's papers show NEXT TO that step's words, taken from
A3's chronology rows. They are CANDIDATES for a person to pick and confirm: nothing is filled in, nothing is decided here, and a date
written beside a word like "dated" may be the date of a document rather than of the step.

Pure functions on the pages the engine's own functions already return (A3 `build_chronology`, A5 `agenda_for_matter`, B3 `check`); the
route that runs them and the flag are the route layer's job. Candidates carry the paper's name, the page, a stable id (so a pick survives a
re-run) and, for a date that reads two ways (day first or month first), ALL readings as A3 gives them; none is chosen.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

ROLES = ("cheque", "presentation", "memo", "information", "notice", "receipt")
ROLE_LABELS = {
    "cheque": "the cheque's date",
    "presentation": "the cheque presented to the bank",
    "memo": "the bank's return memo",
    "information": "the bank's information reaching the person holding the cheque",
    "notice": "the notice given to the drawer",
    "receipt": "the drawer receiving the notice",
}
STATEMENT = (
    "These are the dates the papers show next to each step's words, offered as candidates only. A person picks and confirms each one; "
    "nothing here is filled in or decided. A date written beside a word such as 'dated' may be the date of a document, not of the step."
)
NO_CANDIDATE = "No date next to this step's words was found in the papers added. Enter it yourself if you hold it."
NOT_USABLE_LINE = "Dates the papers show that cannot start or end a period (approximate, a month only, or not read) are listed separately and are not offered."
CHECKLIST_PENDING = "Send the dates you confirmed to get the date checklist beside the agenda."
MAX_PER_ROLE = 5
MAX_QUOTE = 200
AFTER = 45  # characters after the date searched for a step's words, only when none came before it in its sentence

#: Step words per role. A pattern is matched in a window of text around the date; the nearest match to the date decides the role.
CUES: dict[str, tuple[str, ...]] = {
    "cheque": (
        r"cheque\s+(?:bearing\s+)?(?:no\.?\s*\S+\s+)?dated",
        r"dated\s+cheque",
        r"cheque\s+(?:was\s+)?issued\s+on",
        r"issued\s+(?:a\s+)?cheque\s+(?:dated|on)",
        r"post[- ]?dated\s+cheque",
        r"cheque\s+bearing\s+date",
    ),
    "presentation": (
        r"presented",
        r"presentation",
        r"deposited",
        r"tendered\s+for\s+(?:encashment|collection)",
        r"sent\s+for\s+(?:encashment|collection)",
    ),
    "memo": (
        r"return\s+memo",
        r"returning\s+memo",
        r"memo\s+dated",
        r"returned\s+unpaid",
        r"dishonou?red",
        r"bounced",
        r"returned\s+by\s+the\s+bank",
    ),
    "information": (
        r"intimation",
        r"information\s+(?:of\s+dishonou?r\s+)?(?:was\s+)?(?:received|reached)",
        r"informed\s+by\s+the\s+bank",
        r"bank'?s\s+information",
        r"came\s+to\s+know",
    ),
    "notice": (
        r"(?:demand|legal)\s+notice",
        r"notice\s+(?:in\s+writing\s+)?(?:was\s+)?(?:also\s+|then\s+|again\s+)?(?:sent|issued|given|dispatched|despatched)",
        r"(?:sent|issued|gave|given)\s+(?:a\s+)?(?:demand\s+|legal\s+)?notice",
        r"notice\s+dated",
    ),
    "receipt": (
        r"notice\s+(?:was\s+)?(?:also\s+|then\s+)?(?:received|served|delivered)",
        r"(?:received|served\s+with)\s+the\s+notice",
        r"served\s+on",
        r"delivered\s+on",
        r"deemed\s+(?:to\s+be\s+)?served",
    ),
}
_COMPILED = {r: [re.compile(p, re.I) for p in ps] for r, ps in CUES.items()}
#: A bare "received on" belongs to the notice's receipt when the sentence is about a notice, else to the bank's information.
RECEIVED = re.compile(r"\breceived\b", re.I)
NOTICE_WORD = re.compile(r"\bnotice\b", re.I)


def candidate_id(role: str, doc_id: str, start: int, mention_start: int | None) -> str:
    """Stable across re-runs on the same papers: role + document + where the date sits."""
    return "cc-" + hashlib.sha256(f"{role}|{doc_id}|{start}|{mention_start}".encode()).hexdigest()[:10]


BOUNDARY = re.compile(r"(?<=[.;!?])\s+(?=[A-Z0-9\[])|\n")


def _dists(role: str, before: str, after: str, sentence: str) -> tuple[int | None, int | None]:
    """(distance of the nearest cue of `role` ending before the date, distance of the nearest one starting after it); None when there is none."""
    pats = list(_COMPILED[role])
    if (role == "information" and not NOTICE_WORD.search(sentence)) or (role == "receipt" and NOTICE_WORD.search(sentence)):
        pats.append(RECEIVED)
    db = [len(before) - m.end() for rx in pats for m in rx.finditer(before)]
    da = [m.start() for rx in pats for m in rx.finditer(after)]
    return (min(db) if db else None, min(da) if da else None)


def _role_for(row: dict[str, Any]) -> tuple[str | None, list[str]]:
    """The role whose words sit nearest BEFORE the date in its own sentence ("presented to the bank on <date>"); only when no step word comes
    before it, the nearest after it ("<date>, the cheque was presented"). Equal distances name all the roles tied (the first is the role, the others are listed)."""
    quote = row.get("quote") or ""
    ms, me, st = row.get("mention_start"), row.get("mention_end"), row.get("start")
    if ms is None or me is None or st is None:
        return None, []
    a, b = max(0, ms - st), max(0, me - st)
    starts = [m.end() for m in BOUNDARY.finditer(quote[:a])]
    lo = starts[-1] if starts else 0
    nxt = BOUNDARY.search(quote, b)
    hi = nxt.start() if nxt else len(quote)
    before, after, sentence = quote[lo:a], quote[b : min(hi, b + AFTER)], quote[lo:hi]
    dist = {r: _dists(r, before, after, sentence) for r in ROLES}
    cand_b = {r: d[0] for r, d in dist.items() if d[0] is not None}
    pool = cand_b or {r: d[1] for r, d in dist.items() if d[1] is not None}
    if not pool:
        return None, []
    best = min(pool.values())
    top = [r for r in ROLES if pool.get(r) == best]
    return top[0], top[1:]


def _candidate(row: dict[str, Any], role: str, alts: list[str]) -> dict[str, Any]:
    readings = row.get("readings")
    return {
        "candidate_id": candidate_id(role, str(row.get("doc_id")), int(row.get("start") or 0), row.get("mention_start")),
        "role": role,
        "value": row.get("date_as_written"),
        "read_as": row.get("normalised"),
        "readings": list(readings) if readings else None,
        "ambiguous": bool(readings and len(readings) > 1),
        "document_name": row.get("document_name"),
        "page": row.get("page"),
        "source": f"{row.get('document_name')} p.{row.get('page')}",
        "doc_id": row.get("doc_id"),
        "quote": " ".join(str(row.get("quote") or "").split())[:MAX_QUOTE],
        "row_id": row.get("id"),
        "also_near_words_of": alts,
    }


def date_candidates(chronology: dict[str, Any]) -> dict[str, Any]:
    """Per B3 role, the candidate dates from an A3 chronology page. Rows from `events` (one reading) and `ambiguous` (two readings, all kept)
    are considered; approximate, month-only, scheduled and unread dates are listed under `not_usable` and never offered."""
    roles: dict[str, list[dict[str, Any]]] = {r: [] for r in ROLES}
    unassigned = 0
    seen: set[str] = set()
    for block in ("events", "ambiguous"):
        for row in chronology.get(block) or []:
            role, alts = _role_for(row)
            if role is None:
                unassigned += 1
                continue
            c = _candidate(row, role, alts)
            if c["candidate_id"] in seen:
                continue
            seen.add(c["candidate_id"])
            roles[role].append(c)
    cut = {r: max(0, len(v) - MAX_PER_ROLE) for r, v in roles.items()}
    for r in ROLES:
        roles[r] = roles[r][:MAX_PER_ROLE]
    not_usable = [
        {"value": row.get("date_as_written"), "block": b, "source": f"{row.get('document_name')} p.{row.get('page')}"}
        for b in ("approximate", "partial_dates", "scheduled", "date_not_read")
        for row in chronology.get(b) or []
    ]
    return {
        "roles": roles,
        "role_labels": dict(ROLE_LABELS),
        "no_candidate": {r: NO_CANDIDATE for r in ROLES if not roles[r]},
        "not_shown_over_limit": {r: n for r, n in cut.items() if n},
        "not_usable": not_usable,
        "not_usable_line": NOT_USABLE_LINE if not_usable else None,
        "dates_without_a_step_word": unassigned,
    }


def b3_date_object(candidate: dict[str, Any]) -> dict[str, Any]:
    """The B3 request object for a candidate the person picks (unconfirmed: the person confirms with the engine's reading)."""
    return {"value": candidate["value"], "source": candidate["source"], "confirmed": False}


def compose(
    candidates: dict[str, Any],
    agenda: dict[str, Any],
    checklist: dict[str, Any] | None,
    *,
    run_id: str,
    matter_id: str,
    chronology_not_assessed: list[str] | None = None,
) -> dict[str, Any]:
    """The one page. Every `not_assessed` list of the pieces is carried; nothing is dropped or reworded."""
    not_assessed: list[str] = []
    for src in (chronology_not_assessed or [], agenda.get("not_assessed") or [], (checklist or {}).get("not_assessed") or []):
        for x in src:
            if x not in not_assessed:
                not_assessed.append(x)
    return {
        "run_id": run_id,
        "matter_id": matter_id,
        "statement": STATEMENT,
        "candidates": candidates,
        "agenda": agenda,
        "checklist": checklist,
        "checklist_pending": CHECKLIST_PENDING if checklist is None else None,
        "not_assessed": not_assessed,
    }
