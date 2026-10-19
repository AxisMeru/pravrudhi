"""B3: a checklist of the three dates of an NI Act s.138 cheque case (flag `wf_b3_s138_checklist`, default off).

A CHECKLIST, NOT A DECISION. It computes ONLY the three steps pinned from the held primary copy of s.138 (the proviso's clauses
(a) presentation, (b) notice, (c) payment; `assets/s138/ni_act_s138_139.json`, with sha256 and the exact text), from dates a
person typed, each shown back with its source and CONFIRMED by them before anything is computed. Nothing here reads a document,
picks "the first date" of a text, calls a model or the network.

Built and tested on recitals; the complaint window, holidays and the Limitation Act conventions are REFER-only until a lawyer
confirms (the held corpus does not contain the sections they come from). Every period is computed under BOTH counting
conventions; when the answer differs, the step answers REFER with both counts. Whatever cannot be read, confirmed or told apart
is REFER, with the reason: a checklist that guesses would be worse than none.

The module's wording is scanned by a test: a step is only ever 'inside' or 'outside' the stated period, never given the labels a
court uses for its own finding.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr

from pravrudhi.application.date_parse import ParsedDate, add_months, parse_date
from pravrudhi.application.s138_checklist_config import S138ChecklistConfig

ROUTE = "/api/v1/s138-checklist"
ASSET = Path(__file__).resolve().parent.parent / "assets" / "s138" / "ni_act_s138_139.json"

STATEMENT = "A checklist, not a decision"
DESCRIPTION = (
    "Built and tested on court judgment recitals only, not on the merits of any case. No lawyer has used this on a live "
    "matter. The complaint window, holidays and the Limitation Act counting rules are not computed: they are referred to a "
    "lawyer."
)
#: fixed lines in EVERY response: a checklist that was silent on these would read as "the notice is in order"
NOT_ASSESSED = (
    "Whether the underlying debt is legally enforceable, or still recoverable in time",
    "Whether the cheque was drawn for the discharge of a debt or other liability (s.138 requires it)",
    "The reason the bank gave for returning the cheque (s.138 names insufficiency of funds, or an amount over the "
    "arrangement with the bank)",
    "Whether the notice demands more than is due",
    "How the notice was served and whether it was received",
    "Territorial jurisdiction",
    "Notices to a company and its directors (s.141)",
    "The standing of the payee or holder",
)
HOLIDAY_NOTE = (
    "Holiday check not performed: if a last day was a court holiday, the next working day may apply (a lawyer sets the rule)"
)
COMPLAINT_REASON = (
    "The complaint window comes from s.142, which is not held: it is not computed here; a lawyer sets it. Nothing on this "
    "list says whether a complaint is in time"
)
#: One plain line saying WHY a pandemic-era date or period answers REFER (PENDING R1's wording; the window is `PANDEMIC` below).
PANDEMIC_NOTE = (
    "Some dates or periods here touch 15 March 2020 to 31 May 2022, the stretch this tool treats as a possible pandemic-era "
    "exclusion. It does not work out limitation for that stretch: it answers REFER, and a lawyer decides how limitation applies."
)
NOTICE_MODES = ("registered_post", "speed_post", "courier", "email", "fax", "hand_delivery", "other")
PANDEMIC = (date(2020, 3, 15), date(2022, 5, 31))
ORDER = ("cheque", "presentation", "memo", "information", "notice", "receipt")  # the order the dates cannot break
OUTCOME_WORDS = {
    "inside": "The dates you entered fall inside the stated period",
    "outside": "The dates you entered fall outside the stated period; refer to a lawyer",
}


class ChecklistRefused(Exception):
    def __init__(self, code: str, message: str, status_code: int = 422) -> None:
        super().__init__(message)
        self.code, self.status_code = code, status_code


class DateIn(BaseModel):
    """One date as a person typed it, where it comes from, and whether they confirm it."""

    model_config = ConfigDict(extra="forbid")
    value: StrictStr = Field(max_length=120)
    source: StrictStr = Field(min_length=1, max_length=500)
    confirmed: StrictBool = False
    #: the reading (ISO yyyy-mm-dd) the person was shown and confirms: it must equal the server's own reading, so "confirmed" is
    #: never a flag a client can set blind; a day/month-ambiguous date is accepted only with the exact echoed reading
    confirmed_as: StrictStr | None = Field(default=None, max_length=20)
    #: the same date also appears in other text the user gave (a template line copied from another file)
    also_in_other_text: StrictBool = False


class ChecklistRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cheque_date: DateIn | None = None
    #: the cheque's validity in months, if shorter than six months (a bank's rule, entered by the user, not in the section):
    #: absent = the presentation step is REFER
    validity_months: StrictInt | None = Field(default=None, ge=1, le=60)
    presentations: list[DateIn] = Field(default_factory=list, max_length=100)
    return_memos: list[DateIn] = Field(default_factory=list, max_length=100)
    information_received: list[DateIn] = Field(default_factory=list, max_length=100)
    notices: list[DateIn] = Field(default_factory=list, max_length=100)
    notice_receipts: list[DateIn] = Field(default_factory=list, max_length=100)
    notice_mode: Literal["registered_post", "speed_post", "courier", "email", "fax", "hand_delivery", "other"] | None = None


def load_asset() -> dict[str, Any]:
    asset: dict[str, Any] = json.loads(ASSET.read_text(encoding="utf-8"))
    return asset


def open_route_lists() -> None:
    """Add the route to the two classification lists that must name it while it exists (as the draft check does): called only
    when the route is registered, so with the flag off every list is exactly the released one."""
    from pravrudhi.api import identity, roles

    identity.PARTNER_KEY_ROUTES = identity.PARTNER_KEY_ROUTES | {("POST", ROUTE)}
    identity.PARTNER_KEY_PATHS = identity.PARTNER_KEY_PATHS | {ROUTE}
    roles.USER_FACING = roles.USER_FACING | {ROUTE}


# ---- reading the inputs ---------------------------------------------------------------------------------------------------


@dataclass
class _Item:
    role: str
    raw: DateIn
    parsed: ParsedDate
    problems: list[str]
    min_year: int = 1989


def _in_pandemic(d: date | None) -> bool:
    return d is not None and PANDEMIC[0] <= d <= PANDEMIC[1]


def _century(items: list[tuple[str, DateIn]]) -> int | None:
    """The century of the full-year dates the person gave, when they all agree (it resolves a two-digit year, nothing else)."""
    cents = {p.value.year // 100 * 100 for _r, di in items if (p := parse_date(di.value)).value is not None and not p.two_digit}
    return cents.pop() if len(cents) == 1 else None


def _read(role: str, di: DateIn, century: int | None, min_year: int, today: date) -> _Item:
    p = parse_date(di.value, century)
    problems: list[str] = []
    if p.issue == "approximate":
        problems.append("approximate_date")
    elif p.issue == "range":
        problems.append("date_range")
    elif p.issue == "two_digit_year":
        problems.append("two_digit_year")
    elif p.issue is not None:
        problems.append("date_not_read")
    if not di.confirmed:
        problems.append("not_confirmed")
    elif p.value is not None:
        if di.confirmed_as is None:
            problems.append("confirmation_without_reading")
        elif di.confirmed_as.strip() != p.value.isoformat():
            problems.append("confirmation_does_not_match")
    if p.value is not None and not min_year <= p.value.year <= today.year + 1:
        problems.append("year_not_plausible")
    if di.also_in_other_text:
        problems.append("date_in_other_text")
    if _in_pandemic(p.value):
        problems.append("pandemic_window")
    return _Item(role, di, p, problems, min_year)


_PROBLEM_TEXT = {
    "approximate_date": "an approximate date ('on or about', 'around') cannot start or end a period",
    "date_range": "a range of dates cannot start or end a period",
    "two_digit_year": "a two-digit year whose century no full-year date you gave fixes",
    "date_not_read": "the date could not be read",
    "not_confirmed": "not yet confirmed by you; it was read as {read}: confirm it by sending confirmed_as with that reading",
    "confirmation_without_reading": "confirmed without the reading you were shown ({read}): send confirmed_as with it",
    "confirmation_does_not_match": (
        "the reading you confirmed is not the reading made ({read}): confirm that reading or correct the date"
    ),
    "year_not_plausible": (
        "The year looks wrong: it is before {min_year}{why} or after the year after this one, or a two-digit year was read "
        "into a year outside that range. Check it"
    ),
    "date_in_other_text": "the same date appears in other text you gave us",
    "pandemic_window": "the date falls in the pandemic-era limitation-exclusion window, which a lawyer decides how to apply",
}
_ROLE_NAME = {
    "cheque": "cheque date",
    "presentation": "presentation",
    "memo": "return memo",
    "information": "information from the bank",
    "notice": "notice",
    "receipt": "receipt of the notice",
}


# ---- the steps ------------------------------------------------------------------------------------------------------------


def _cap(text: str) -> str:
    return text[:1].upper() + text[1:]


def _weekend(d: date) -> bool:
    return d.weekday() >= 5


def _step(name: str, clause: str, title: str) -> dict[str, Any]:
    return {"step": name, "clause": clause, "title": title, "status": "REFER", "refer": [], "result": "", "counts": {}}


def _need(step: dict[str, Any], items: list[_Item], role: str, ask: str, many: str) -> _Item | None:
    """The one clean item of `role`, or None with the reason(s) on the step."""
    if not items:
        step["refer"].append({"code": f"{role}_missing", "text": ask})
        return None
    if len(items) > 1:
        step["refer"].append({"code": f"{role}_several", "text": many, "candidates": [i.raw.value for i in items]})
        return None
    item = items[0]
    for code in item.problems:
        read = item.parsed.value.isoformat() if item.parsed.value else "no date"
        why = " (s.138 was inserted into the Act with effect from 1 April 1989)" if item.min_year == 1989 else ""
        text = _PROBLEM_TEXT[code].format(read=read, min_year=item.min_year, why=why)
        step["refer"].append({"code": f"{role}_{code}", "text": f"{_ROLE_NAME[role]} '{item.raw.value}': {text}"})
    return item if not item.problems else None


def _touches_pandemic(start: date, end: date) -> bool:
    """A period that starts in, ends in or spans the pandemic-era window: the typed dates may all be outside it."""
    return start <= PANDEMIC[1] and end >= PANDEMIC[0]


def _pandemic_period(step: dict[str, Any], start: date, end: date) -> None:
    if _touches_pandemic(start, end):
        step["refer"].append(
            {
                "code": "pandemic_period",
                "text": (
                    f"the period from {start.isoformat()} to {end.isoformat()} touches the pandemic-era limitation-exclusion "
                    "window, which a lawyer decides how to apply"
                ),
            }
        )


def _order_reasons(items: dict[str, list[_Item]]) -> list[dict[str, Any]]:
    """The dates cannot come in any order: cheque, presentation, return memo, information, notice, receipt, each on or after the
    one before it. Only clean single dates are compared (a missing, several, approximate or unread date is already REFER)."""
    singles = [
        (r, items[r][0].parsed.value)
        for r in ORDER
        if len(items[r]) == 1 and items[r][0].parsed.issue is None and items[r][0].parsed.value
    ]
    out = []
    for (ra, da), (rb, db) in zip(singles, singles[1:], strict=False):
        assert da is not None and db is not None
        if db < da:
            out.append(
                {
                    "code": "dates_in_impossible_order",
                    "text": (
                        "the dates are not in the order the steps follow: "
                        f"the {_ROLE_NAME[rb]} ({db.isoformat()}) comes before "
                        f"the {_ROLE_NAME[ra]} ({da.isoformat()}). Check them"
                    ),
                }
            )
    return out


def _status_word(inside: bool) -> str:
    return "inside" if inside else "outside"


def _decide(step: dict[str, Any], ends: dict[str, date], when: date, start: date, unit: str) -> None:
    """Fill `counts` and the status of a step with a date (`when`) tested against the end of a period under both conventions."""
    if when < start:
        step["refer"].append(
            {
                "code": "date_before_start",
                "text": f"the date {when.isoformat()} is before the date the period runs from ({start.isoformat()})",
            }
        )
        return
    statuses = {}
    for conv, end in ends.items():
        inside = when <= end
        statuses[conv] = _status_word(inside)
        step["counts"][conv] = {
            "convention": "first day excluded, last day included" if conv == "A" else "first day included",
            "period_ends": end.isoformat(),
            "days_after_start": (when - start).days,
            "status": statuses[conv],
        }
        if not inside and _weekend(end):
            step["refer"].append(
                {
                    "code": "last_day_is_a_weekend",
                    "text": (
                        f"the last day under convention {conv} ({end.isoformat()}) is a weekend day; the holiday rule is not set"
                    ),
                }
            )
    if len(set(statuses.values())) > 1:
        step["refer"].append(
            {
                "code": "conventions_differ",
                "text": "the answer depends on the counting convention: "
                + "; ".join(f"{c}: {s}" for c, s in statuses.items())
                + f" ({unit})",
            }
        )
        return
    if not step["refer"]:
        step["status"] = statuses["A"].upper()
        step["result"] = OUTCOME_WORDS[statuses["A"]] + f" (both counts agree: {unit})"


def check(req: ChecklistRequest, cfg: S138ChecklistConfig, run_id: str = "", today: date | None = None) -> dict[str, Any]:
    """`today` is the clock for the year ceiling (next year is the latest a date may have); tests inject it."""
    roles: dict[str, list[DateIn]] = {
        "presentation": req.presentations,
        "memo": req.return_memos,
        "information": req.information_received,
        "notice": req.notices,
        "receipt": req.notice_receipts,
    }
    for role, lst in roles.items():
        if len(lst) > cfg.max_dates_per_role:
            raise ChecklistRefused("too_many_dates", f"at most {cfg.max_dates_per_role} {role} dates", 413)
    everything = [("cheque", req.cheque_date)] if req.cheque_date else []
    everything += [(r, d) for r, lst in roles.items() for d in lst]
    century = _century([(r, d) for r, d in everything if d is not None])
    items: dict[str, list[_Item]] = {"cheque": [], **{r: [] for r in roles}}
    for r, d in everything:
        assert d is not None
        items[r].append(_read(r, d, century, cfg.min_year, today or date.today()))

    chains = {r: [i.raw.value for i in lst] for r, lst in items.items() if len(lst) > 1}

    def chain_reason(step: dict[str, Any]) -> None:
        if chains:
            step["refer"].append(
                {
                    "code": "more_than_one_candidate",
                    "text": "more than one date was given for the same step of one cheque: say which is the statutory one",
                    "candidate_chains": chains,
                }
            )

    order = _order_reasons(items)

    def order_reason(step: dict[str, Any]) -> None:
        step["refer"].extend(dict(o) for o in order)

    # (a) presentation
    pres = _step(
        "presentation", "(a)", "presented to the bank within six months of the date drawn, or within the cheque's validity"
    )
    cheque = _need(pres, items["cheque"], "cheque", "the date the cheque was drawn is missing", "several cheque dates")
    presented = _need(
        pres,
        items["presentation"],
        "presentation",
        "the date of presentation to the bank is missing",
        "several presentation dates",
    )
    if req.validity_months is None:
        pres["refer"].append(
            {
                "code": "validity_missing",
                "text": (
                    "the cheque's validity period is not given; the section names it without stating it, "
                    "and six months is only the outer limit"
                ),
            }
        )
    chain_reason(pres)
    order_reason(pres)
    if req.validity_months is not None:
        pres["notes"] = [
            f"the cheque validity of {req.validity_months} months was entered by you; it is not in the section. A validity "
            "shorter than six months ends the period earlier; a longer one does not extend it past six months"
        ]
    if (
        cheque
        and presented
        and cheque.parsed.value
        and presented.parsed.value
        and req.validity_months is not None
        and not pres["refer"]
    ):
        six = add_months(cheque.parsed.value, 6)
        valid = add_months(cheque.parsed.value, req.validity_months)
        end_a = min(six, valid)
        _pandemic_period(pres, cheque.parsed.value, end_a)
        pres["period_basis"] = {
            "six_months_end": six.isoformat(),
            "validity_end": valid.isoformat(),
            "earlier_used": "validity" if valid < six else "six_months",
        }
        if not pres["refer"]:
            _decide(
                pres,
                {"A": end_a, "B": end_a - timedelta(days=1)},
                presented.parsed.value,
                cheque.parsed.value,
                "months from the date drawn",
            )
    # (b) notice
    note = _step(
        "notice", "(b)", "notice in writing to the drawer within thirty days of receiving the bank's information of the return"
    )
    memo = _need(
        note,
        items["memo"],
        "memo",
        "the date of the bank's return memo is missing",
        "several return memos (several cheques or returns)",
    )
    info = _need(
        note,
        items["information"],
        "information",
        "the date the payee or holder received the bank's information is missing",
        "several dates of receiving information",
    )
    given = _need(note, items["notice"], "notice", "the date the notice was given is missing", "several notices")
    chain_reason(note)
    order_reason(note)
    if memo and info and given and info.parsed.value and given.parsed.value and not note["refer"]:
        end_a = info.parsed.value + timedelta(days=30)
        _pandemic_period(note, info.parsed.value, end_a)
        if not note["refer"]:
            _decide(
                note,
                {"A": end_a, "B": end_a - timedelta(days=1)},
                given.parsed.value,
                info.parsed.value,
                "days from the information",
            )
    # (c) payment
    pay = _step("payment", "(c)", "the drawer's fifteen days to pay run from receiving the notice")
    got = _need(
        pay,
        items["receipt"],
        "receipt",
        "the date the drawer received the notice is missing (a deemed date is a choice, so none is computed); "
        "say how it was sent: " + ", ".join(NOTICE_MODES),
        "several dates of receiving the notice",
    )
    chain_reason(pay)
    order_reason(pay)
    if got and got.parsed.value and not pay["refer"]:
        end_a = got.parsed.value + timedelta(days=15)
        end_b = end_a - timedelta(days=1)
        _pandemic_period(pay, got.parsed.value, end_a)
    if got and got.parsed.value and not pay["refer"]:
        for conv, end in (("A", end_a), ("B", end_b)):
            pay["counts"][conv] = {
                "convention": "first day excluded, last day included" if conv == "A" else "first day included",
                "period_ends": end.isoformat(),
            }
        pay["status"] = "COMPUTED"
        pay["result"] = (
            f"the fifteen days run to {end_a.isoformat()} (first day excluded) or {end_b.isoformat()} (first day included); "
            "whether the drawer paid is not assessed here"
        )
        weekend = [e for e in (end_a, end_b) if _weekend(e)]
        if weekend:
            pay["notes"] = ["a last day above falls on a weekend day; the holiday rule is not set"]
    if req.notice_mode is None and got is not None:
        pay.setdefault("notes", []).append("how the notice was sent is not stated")
    # (d) complaint window: never computed
    comp = _step("complaint_window", "s.142", "the complaint window")
    comp["refer"].append({"code": "complaint_window_not_computed", "text": COMPLAINT_REASON})

    steps = [pres, note, pay, comp]
    for s in steps:
        for r in s["refer"]:
            r["text"] = _cap(r["text"])
        s["notes"] = [_cap(n) for n in s.get("notes", [])] or None
        if s["notes"] is None:
            del s["notes"]
        if s["refer"]:
            s["status"] = "REFER"
            s["result"] = "REFER: " + " ".join(r["text"].rstrip(".") + "." for r in s["refer"])
        elif s["result"]:
            s["result"] = _cap(s["result"])
    inputs = [
        {
            "role": role,
            "text": i.raw.value,
            "read_as": i.parsed.value.isoformat() if i.parsed.value else None,
            "day_first_assumed": i.parsed.day_first_assumed,
            "source": i.raw.source,
            "confirmed": i.raw.confirmed,
            "confirmed_as": i.raw.confirmed_as,
            "problems": i.problems,
        }
        for role, lst in items.items()
        for i in lst
    ]
    pandemic = any(r.get("code") == "pandemic_period" for s_ in steps for r in s_["refer"]) or any(
        "pandemic_window" in i.problems for lst in items.values() for i in lst
    )
    return {
        "statement": STATEMENT,
        "description": DESCRIPTION,
        "lawyer_confirmed": False,
        "pandemic_note": PANDEMIC_NOTE if pandemic else None,
        "run_id": run_id,
        "conventions": {
            "A": "first day excluded, last day included (default)",
            "B": "first day included",
            "rule": "both are computed; when the answer differs the step answers REFER with both counts",
        },
        "inputs": inputs,
        "validity": {"months": req.validity_months, "entered_by_user": req.validity_months is not None},
        "steps": steps,
        "refer_count": sum(1 for s in steps if s["status"] == "REFER"),
        "not_assessed": list(NOT_ASSESSED),
        "notes": [HOLIDAY_NOTE],
        "source": {
            "act": "Negotiable Instruments Act, 1881, s.138 proviso (a) to (c), India Code copy captured 24 Sep 2026",
            "asset": "ni_act_s138_139.json",
        },
    }
