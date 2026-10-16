"""Dates as they are PRINTED in Indian court papers, read without guessing.

`parse_date` returns what it read and what it could not be sure of; a caller treats every `issue` as REFER, never as a value.
Order is day first (the Indian convention); the read is always shown back to the user for confirmation. Nothing here picks "the
first date" of a text: it parses ONE string a person gave for one role.
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date

MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_name) if m}
MONTHS.update({m.lower(): i for i, m in enumerate(calendar.month_abbr) if m})
MONTHS["sept"] = 9

_APPROX = re.compile(r"\b(on or about|about|around|approx(?:imately)?\.?|circa|c\.)\b", re.I)
_PREFIX = re.compile(r"^\s*(?:dated|dt\.?|d/d|on)\b[\s:.-]*", re.I)
_RANGE = re.compile(r"\b(?:between|from)\b|\s+(?:and|to)\s+|\s[–—-]\s", re.I)
_ISO = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_NUM = re.compile(r"^(\d{1,2})\s*[./-]\s*(\d{1,2})\s*[./-]\s*(\d{4}|\d{2})$")
_DMY_WORD = re.compile(r"^(\d{1,2})(?:st|nd|rd|th)?(?:\s+of)?[\s,]+([A-Za-z]+)\.?,?\s+(\d{4})$")
_MDY_WORD = re.compile(r"^([A-Za-z]+)\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})$")


@dataclass(frozen=True)
class ParsedDate:
    text: str
    value: date | None
    #: None, or why the read cannot be a value: approximate | range | two_digit_year | unparsed | not_a_date
    issue: str | None
    #: the year was written with two digits (the century is `value`'s guess only when a caller resolves it)
    two_digit: bool = False
    #: both day and month were 12 or less: the day-first order was assumed
    day_first_assumed: bool = False


def _mk(y: int, m: int, d: int) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def _one(text: str) -> tuple[date | None, bool, bool]:
    """(date, two_digit, day_first_assumed) for ONE date string, or (None, False, False)."""
    s = text.strip().strip(".,;:()[]{}„‚“”'\"").strip()
    m = _ISO.match(s)
    if m:  # yyyy-mm-dd is unambiguous
        return _mk(int(m.group(1)), int(m.group(2)), int(m.group(3))), False, False
    m = _NUM.match(s)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), m.group(3)
        two = len(y) == 2
        return _mk(int(y) if not two else 2000 + int(y), mo, d), two, (d <= 12 and mo <= 12 and d != mo)
    m = _DMY_WORD.match(s)
    if m and m.group(2).lower() in MONTHS:
        return _mk(int(m.group(3)), MONTHS[m.group(2).lower()], int(m.group(1))), False, False
    m = _MDY_WORD.match(s)
    if m and m.group(1).lower() in MONTHS:
        return _mk(int(m.group(3)), MONTHS[m.group(1).lower()], int(m.group(2))), False, False
    return None, False, False


def parse_date(text: str, century: int | None = None) -> ParsedDate:
    """Read one printed date. `century` (e.g. 2000) resolves a two-digit year ONLY when the caller has a full-year date that
    fixes it; without it a two-digit year is `two_digit_year`, never a guess."""
    raw = text if isinstance(text, str) else ""
    approx = bool(_APPROX.search(raw))
    s = _PREFIX.sub("", _APPROX.sub(" ", raw)).strip()
    if not s:
        return ParsedDate(raw, None, "not_a_date")
    if _RANGE.search(s) and sum(1 for p in _RANGE.split(s) if p and _one(p)[0] is not None) >= 2:
        return ParsedDate(raw, None, "range")
    value, two, dfa = _one(s)
    if value is None:
        return ParsedDate(raw, None, "unparsed")
    if two:
        if century is None:
            return ParsedDate(raw, None, "two_digit_year", two_digit=True, day_first_assumed=dfa)
        try:
            value = value.replace(year=century + value.year % 100)
        except ValueError:  # 29 February in a year that has none
            return ParsedDate(raw, None, "unparsed", two_digit=True, day_first_assumed=dfa)
    if approx:
        return ParsedDate(raw, value, "approximate", two_digit=two, day_first_assumed=dfa)
    return ParsedDate(raw, value, None, two_digit=two, day_first_assumed=dfa)


def add_months(d: date, months: int) -> date:
    """Calendar-month arithmetic: the same day, or the last day of a shorter month (31 Aug + 6 months = 28/29 Feb)."""
    idx = d.year * 12 + (d.month - 1) + months
    y, m = divmod(idx, 12)
    return date(y, m + 1, min(d.day, calendar.monthrange(y, m + 1)[1]))
