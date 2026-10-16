"""The date scanner of the chronology (A3): date MENTIONS of a document's passages, how each was read, and the counts that prove
none was dropped. Contract: A3 v4.1 (d8cc8c6e231d) + addenda 2 (fd4e3eb0fec5), 3 (b683866c908d) + the v4.2 note (7e6843c93fbb).

Deterministic, no model, no network; it works on VERBATIM passage text and reports offsets into exactly that text. Nothing is
joined across a passage cut (A2 stores passages, not the gaps): a mention touching a cut is "a date we could not read", never a
guess. Every pattern is anchored by a lookbehind or a literal and uses BOUNDED repeats only, so the scan is linear.

A SEPARATE permissive detector counts every date-LIKE string; a like string no grammar read becomes a "date we could not read"
row, so `like = read + unread + printed + number_like` is not a tautology and nothing is dropped silently.
"""

from __future__ import annotations

import bisect
import calendar
import re
from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from typing import Any

CATEGORIES = ("exact", "ambiguous", "approximate", "less_than_a_day", "scheduled", "not_read", "printed", "number_like")
#: v4.2 F3 precedence, highest first
PRECEDENCE = ("printed", "number_like", "not_read", "scheduled", "approximate", "less_than_a_day", "ambiguous", "exact")
MIN_YEAR = 1800
SENTENCE_PUNCT = ".?!;:"
MAX_SENTENCE = 2_000

_MONTH = (
    r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?"
    r"|Nov(?:ember)?|Dec(?:ember)?)"
)
_MONTH_CI = rf"(?i:{_MONTH})"
_D = "[0-9]"
_SP = (
    r"(?:[ \t]{1,3}|[ \t]{0,2}\r?\n[ \t]{0,2})"  # between the parts of a written date: a line break counts, a blank line does not
)
_SPC = r"(?:[ ,\t]{1,3}|[ \t,]{0,2}\r?\n[ \t]{0,2})"
_W_DMY = re.compile(rf"(?<![\w/])({_D}{{1,2}})(?:st|nd|rd|th)?(?:{_SP}of)?{_SPC}({_MONTH_CI})\.?,?{_SP}?({_D}{{4}})(?!{_D})")
_W_NTH = re.compile(
    rf"(?<![\w/])({_D}{{1,2}})(?:st|nd|rd|th){_SP}day{_SP}of{_SP}({_MONTH_CI})\.?,?{_SP}?({_D}{{4}})(?!{_D})", re.I
)
_W_MDY = re.compile(rf"(?<!\w)({_MONTH_CI})\.?{_SP}({_D}{{1,2}})(?:st|nd|rd|th)?,?{_SP}({_D}{{4}})(?!{_D})")
_ISO = re.compile(rf"(?<![0-9-])({_D}{{4}})-({_D}{{2}})-({_D}{{2}})(?!{_D})")
_NUM = re.compile(rf"(?<![0-9.:/-])({_D}{{1,2}})[ ]?([./-])[ ]?({_D}{{1,2}})[ ]?\2[ ]?({_D}{{4}}|{_D}{{2}})(?!{_D}|[./-]{_D})")
_P_MY = re.compile(rf"(?<!\w)({_MONTH_CI})\.?,?{_SP}({_D}{{4}})(?!{_D})")
_P_DM = re.compile(rf"(?<![\w/])({_D}{{1,2}})(?:st|nd|rd|th)?(?:{_SP}of)?{_SP}({_MONTH_CI})\b\.?(?!{_SPC}?{_D})")
_P_Y = re.compile(rf"(?:\b(?:in|during|since)|\bthe year|\bof the year|\byear)[ ]+({_D}{{4}})(?!{_D})", re.I)
_NUMBER_LIKE = re.compile(
    rf"(?<![\w/.-]){_D}{{3,6}}/{_D}{{1,2}}/{_D}{{4}}(?!{_D})"
    r"|(?:No\.?|Nos\.?|Crime|FIR|Case|Cr\.?|Crl\.?|Crl\.O\.P\.|W\.P\.|S\.C\.|C\.C\.|Crl\.M\.P\.|O\.S\.|M\.C\.)"
    rf"[ ]?[-.:(]*[ ]?{_D}{{1,6}}[/-]{_D}{{1,4}}(?:[/-]{_D}{{1,4}})?"
    rf"|(?<![\w.:/-]){_D}{{1,2}}[.:]{_D}{{2}}(?:[.:]{_D}{{2}})?[ ]?(?:a\.?m\.?|p\.?m\.?|hrs|hours)(?!\w)",
    re.I,
)
_IDENT_BEFORE = re.compile(
    r"(?:No\.?|Nos\.?|Cr\.?|Crl\.?|Case|FIR|Crime|Crl\.O\.P\.|W\.P\.|S\.C\.|C\.C\.|Crl\.M\.P\.|O\.S\.|M\.C\.)\s*$", re.I
)
_TIME_AFTER = re.compile(r"^[ ]?(?:a\.?m\.?|p\.?m\.?|hrs|hours)(?!\w)", re.I)
_APPROX = re.compile(r"(?:on or about|about|around|approx\.?|approximately|circa)\s*$", re.I)
_RANGE_BEFORE = re.compile(r"(?:between|from)\s*$", re.I)
_RANGE_BETWEEN = re.compile(r"^\s{0,2}(?:and|to|till|until|-|–)\s{0,2}$", re.I)
_SHARED_MONTH = re.compile(
    rf"(?<![\w/])({_D}{{1,2}})(?:st|nd|rd|th)?[ ]?(?:-|–|to|and)[ ]?({_D}{{1,2}})(?:st|nd|rd|th)?[ ]"
    rf"({_MONTH_CI})\.?,?[ ]({_D}{{4}})(?!{_D})"
)
_REFERENCE = re.compile(
    r"(?:\b(?:FIR|F\.I\.R\.|order|judgment|complaint|notice|letter|report|memo|petition|application|summons|charge[- ]?sheet|"
    r"chargesheet|crime|case)\b[^.;]{0,25}?\b(?:dated|dt\.?|of)|"
    r"\b(?:as per|vide|according to|referred to in|in terms of)\b[^.;]{0,40})\s*$",
    re.I,
)
_SCHEDULED = re.compile(
    r"\b(?:listed (?:on|for)|adjourned (?:to|till)|posted (?:to|for)|next date(?: of hearing)?|to appear on|returnable (?:on|at)|"
    r"fixed for|put up on)\b[^.;]{0,25}$",
    re.I,
)
_STATUTE_BEFORE = re.compile(r"(?:Act|Code|Rules|Regulations|IPC|CrPC|Cr\.P\.C\.|BNS|BNSS|BSA|Constitution)[ ,]{0,3}$")
_CITATION_AROUND = re.compile(r"\(\d{4}\)[ ]?\d+[ ]?[A-Z]|\bAIR[ ]?\d{4}|\d{4}[ ]SCC|\[\d{4}\]")
_PRINTED_PHRASE = re.compile(
    r"printed on|downloaded on|downloaded from|print date|page\s+\d+\s+of\s+\d+|digitally signed|uploaded on|indian kanoon", re.I
)
_NON_ASCII_DIGIT = re.compile(r"[٠-٩۰-۹०-९０-９]")
_QUOTE_OPEN = "“„‟«□"
_QUOTE_CLOSE = "”»‚"
_LIKE = [
    re.compile(r"(?<![\w])(?:[\d|OoIl!]{1,4}[ ]?[./-][ ]?){2}[\d|OoIl!]{2,4}(?![\w])"),
    re.compile(rf"(?<![\w/])[\d|OoIl!]{{1,2}}(?:st|nd|rd|th)?(?:{_SP}of)?{_SPC}{_MONTH_CI}\b"),
    re.compile(rf"(?<![\w/])[\d|OoIl!]{{1,2}}(?:st|nd|rd|th){_SP}day{_SP}of{_SP}{_MONTH_CI}\b", re.I),
    re.compile(rf"(?<![\w]){_MONTH_CI}\b\.?,?{_SP}[\d|OoIl!]{{1,4}}"),
    re.compile(r"(?<![\d-])[\d|OoIl!]{4}-[\d|OoIl!]{2}-[\d|OoIl!]{2}(?!\d)"),
    re.compile(r"(?:\b(?:in|during|since)|\bthe year|\bof the year|\byear)[ ]+[\d|OoIl!]{4}(?![\d])", re.I),
]


@dataclass
class Unit:
    """One stored passage of a document, with what the cut rules need to know about its neighbours."""

    doc_id: str
    page: int
    start: int
    text: str
    span_id: int | str = 0
    #: document offset where the previous passage of the SAME document ends (None for the first), its last character
    prev_end: int | None = None
    prev_last: str = ""
    has_next: bool = False

    def __post_init__(self) -> None:
        self.end_nospace = len(self.text.rstrip())  # cached: the edge rules run once per mention
        self.first_nospace = len(self.text) - len(self.text.lstrip())


@dataclass
class Sent:
    unit: int
    start: int
    end: int
    cut_fragment: bool = False
    other_language: bool = False
    unread_language: bool = False


@dataclass
class Hit:
    unit: int
    sent: int
    start: int
    end: int
    text: str
    #: written | numeric | iso | range | partial_month_year | partial_day_month | partial_year | suspect | number_like
    kind: str
    category: str = "exact"
    #: written_month | iso | only_valid_reading | day_equals_month | day_first_by_document_evidence |
    #: month_first_by_document_evidence | range | approximate | two_digit_with_century_evidence | ambiguous | None
    how: str | None = None
    value: date | None = None
    end_value: date | None = None
    readings: tuple[date, date] | None = None
    precision: str | None = None
    evidence: dict[str, Any] | None = None
    not_read_reason: str | None = None
    approximate: bool = False
    reference: bool = False
    quoted: bool = False
    scheduled: bool = False
    printed: bool = False
    #: numeric parts as printed (for the day/month rule)
    a: int = 0
    b: int = 0
    sep: str = ""
    year_text: str = ""
    doc_start: int = 0
    doc_end: int = 0


@dataclass
class DocScan:
    units: list[Unit]
    sents: list[Sent]
    hits: list[Hit]
    counts: dict[str, Any] = field(default_factory=dict)


# ---- units -------------------------------------------------------------------------------------------


def prepare_units(passages: list[tuple[str, int, int, str, int | str]]) -> list[Unit]:
    """Units of ONE document from `(doc_id, page, start, text, span_id)` rows in document order: neighbours filled in."""
    rows = sorted(passages, key=lambda r: r[2])
    units: list[Unit] = []
    for i, (doc_id, page, start, text, span_id) in enumerate(rows):
        u = Unit(doc_id, page, start, text, span_id, has_next=i + 1 < len(rows))
        if units:
            u.prev_end = units[-1].start + len(units[-1].text)
            u.prev_last = units[-1].text.rstrip()[-1:]
        units.append(u)
    return units


# ---- sentences ---------------------------------------------------------------------------------------

_ABBR = frozenset(
    {
        "no",
        "nos",
        "cr",
        "crl",
        "dr",
        "mr",
        "mrs",
        "ms",
        "smt",
        "sh",
        "st",
        "shri",
        "sri",
        "vs",
        "v",
        "ltd",
        "pvt",
        "co",
        "hon",
        "sec",
        "u",
        "w",
        "ibid",
        "para",
        "paras",
        "etc",
        "viz",
        "ors",
        "anr",
        "m",
        "ex",
        "s",
        "ci",
        "si",
        "asi",
        "dsp",
        "adv",
    }
)
_BOUNDARY = re.compile(r"[.?!;\u0964\u0965][\"')\]”’]{0,2}[ \t\r\f]{1,4}|\n[ \t\r\f]*\n")
_LIST_MARKER = re.compile(r"^\(?[0-9A-Za-z]{1,3}\)?$")


def sentences(text: str) -> list[tuple[int, int]]:
    """`(start, end)` of every sentence: cut at . ? ! ; followed by white space (not after an abbreviation, a single capital, a
    list marker or before a lower-case letter) and at a blank line; a sentence is never longer than MAX_SENTENCE characters
    (it is cut at the last space before the limit). Linear."""
    out: list[tuple[int, int]] = []
    start = 0
    for m in _BOUNDARY.finditer(text):
        if text[m.start()] == "\n":
            end, nxt = m.start(), m.end()
        else:
            dot = m.start()
            word = re.search(r"([A-Za-z]+)$", text[max(start, dot - 12) : dot])
            if (
                text[dot] == "."
                and word
                and (word.group(1).lower() in _ABBR or (len(word.group(1)) == 1 and word.group(1).isupper()))
            ):
                continue
            if text[m.end() : m.end() + 1].islower():
                continue
            if text[dot] == "." and _LIST_MARKER.match(text[start:dot].strip()):
                continue
            end, nxt = dot + 1, m.end()
        if text[start:end].strip():
            out.append((start, end))
        start = nxt
    if text[start:].strip():
        out.append((start, len(text)))
    return _cap_sentences(text, out)


def _cap_sentences(text: str, sents: list[tuple[int, int]]) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for s, e in sents:
        while e - s > MAX_SENTENCE:
            cut = text.rfind(" ", s + MAX_SENTENCE // 2, s + MAX_SENTENCE)
            cut = cut + 1 if cut != -1 else s + MAX_SENTENCE
            out.append((s, cut))
            s = cut
        out.append((s, e))
    return out


# ---- quotation state ---------------------------------------------------------------------------------


def _quoted_ranges(text: str, carried: bool) -> tuple[list[tuple[int, int]], bool]:
    """Quoted character ranges of one passage and whether a quotation is still open at its end. Double and typographic double
    quotes, the corrupted glyphs and guillemets count; a single curly quote opens only when an unmatched closing one follows in
    the same sentence (an apostrophe is not a quote)."""
    ranges: list[tuple[int, int]] = []
    open_at: int | None = 0 if carried else None
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        if c == '"':
            if open_at is None:
                open_at = i
            else:
                ranges.append((open_at, i + 1))
                open_at = None
        elif c in _QUOTE_OPEN and open_at is None:
            open_at = i
        elif c in _QUOTE_CLOSE and open_at is not None:
            ranges.append((open_at, i + 1))
            open_at = None
        elif c == "‘" and open_at is None:
            j = text.find("’", i + 1, i + 400)
            if j != -1 and not text[j + 1 : j + 2].isalpha():
                ranges.append((i, j + 1))
                i = j
        i += 1
    if open_at is not None:
        ranges.append((open_at, n))
    return ranges, open_at is not None


# ---- mentions ----------------------------------------------------------------------------------------


def _line_bounds(text: str, pos: int) -> tuple[int, int]:
    s = text.rfind("\n", 0, pos) + 1
    e = text.find("\n", pos)
    return s, e if e != -1 else len(text)


def _raw_mentions(text: str) -> list[Hit]:
    """Mentions of ONE passage in order, non-overlapping: ranges first (they consume their characters), then written, ISO,
    numeric, number-like, partial and suspect shapes. `unit`/`sent` are filled by the caller."""
    found: list[Hit] = []
    mask = bytearray(len(text))  # characters already taken by a mention: O(length) per check, never O(mentions)

    def free(s: int, e: int) -> bool:
        return not any(mask[s:e])

    def add(h: Hit) -> None:
        mask[h.start : h.end] = b"\x01" * (h.end - h.start)
        found.append(h)

    def approx(s: int) -> bool:
        return bool(_APPROX.search(text[max(0, s - 16) : s]))

    # (2) ranges first: shared-month forms, then "between/from X and/to Y" of two single dates
    for m in _SHARED_MONTH.finditer(text):
        s, e = m.span()
        if free(s, e):
            add(Hit(0, 0, s, e, m.group(0), "range", approximate=approx(s)))
    singles: list[Hit] = []
    for rx, kind in ((_W_NTH, "written"), (_W_DMY, "written"), (_W_MDY, "written")):
        for m in rx.finditer(text):
            mon = m.group(2 if rx is not _W_MDY else 1)
            if mon.lower() == "may" and mon[0] != "M":
                continue
            singles.append(Hit(0, 0, m.start(), m.end(), m.group(0), kind))
    for m in _ISO.finditer(text):
        singles.append(Hit(0, 0, m.start(), m.end(), m.group(0), "iso"))
    for m in _NUM.finditer(text):
        s, e = m.span()
        if _IDENT_BEFORE.search(text[max(0, s - 14) : s]) or _TIME_AFTER.match(text[e : e + 8]):
            add(Hit(0, 0, s, e, m.group(0), "number_like"))
            continue
        singles.append(
            Hit(0, 0, s, e, m.group(0), "numeric", a=int(m.group(1)), b=int(m.group(3)), sep=m.group(2), year_text=m.group(4))
        )
    singles.sort(key=lambda h: (h.start, -(h.end - h.start)))
    # pair adjacent singles into a range ("between X and Y", "from X to Y")
    i = 0
    chosen: list[Hit] = []
    for h in singles:
        if chosen and h.start < chosen[-1].end:
            continue
        chosen.append(h)
    while i < len(chosen):
        h = chosen[i]
        nxt = chosen[i + 1] if i + 1 < len(chosen) else None
        if (
            nxt is not None
            and _RANGE_BEFORE.search(text[max(0, h.start - 14) : h.start])
            and _RANGE_BETWEEN.match(text[h.end : nxt.start])
            and free(h.start, nxt.end)
        ):
            add(Hit(0, 0, h.start, nxt.end, text[h.start : nxt.end], "range", approximate=approx(h.start)))
            i += 2
            continue
        if free(h.start, h.end):
            h.approximate = approx(h.start)
            add(h)
        i += 1
    for m in _NUMBER_LIKE.finditer(text):
        if free(m.start(), m.end()):
            add(Hit(0, 0, m.start(), m.end(), m.group(0), "number_like"))
    for m in _P_MY.finditer(text):
        if m.group(1).lower() == "may" and m.group(1)[0] != "M":
            continue
        if free(m.start(), m.end()):
            add(Hit(0, 0, m.start(), m.end(), m.group(0), "partial_month_year", precision="month"))
    for m in _P_DM.finditer(text):
        if m.group(2).lower() == "may" and m.group(2)[0] != "M":
            continue
        if free(m.start(), m.end()):
            add(Hit(0, 0, m.start(), m.end(), m.group(0), "partial_day_month", precision="day_month"))
    for m in _P_Y.finditer(text):
        s, e = m.start(1), m.end(1)
        ctx = text[max(0, s - 60) : e + 20]
        if _STATUTE_BEFORE.search(text[max(0, m.start() - 40) : m.start()]) or _CITATION_AROUND.search(ctx):
            continue
        if free(s, e):
            add(Hit(0, 0, s, e, m.group(1), "partial_year", precision="year", year_text=m.group(1)))
    found.sort(key=lambda h: h.start)
    return found


def _like_spans(text: str) -> list[tuple[int, int]]:
    """The SEPARATE permissive detector: every date-LIKE string (OCR look-alikes, non-ASCII digits, partial and year-only forms,
    identifier shapes). Independent of the grammar above."""
    spans: list[tuple[int, int]] = []
    for rx in _LIKE:
        for m in rx.finditer(text):
            spans.append(m.span(1) if rx.groups and rx is _LIKE[4] else m.span())
    for m in _NON_ASCII_DIGIT.finditer(text):
        spans.append((m.start(), m.end()))
    spans.sort()
    merged: list[tuple[int, int]] = []
    for s, e in spans:
        if merged and s < merged[-1][1]:
            merged[-1] = (merged[-1][0], max(e, merged[-1][1]))
        else:
            merged.append((s, e))
    return merged


# ---- fragments of a date cut at a passage edge --------------------------------------------------------------------------------
_MONTH_FULL = [m.lower() for m in calendar.month_name if m]
_MONTH_ALL = frozenset(_MONTH_FULL) | frozenset(m.lower() for m in calendar.month_abbr if m)
_END_NUM = re.compile(r"(?<![0-9./-])[0-9]{1,2}[./-](?:[0-9]{1,2}[./-]?(?:[0-9]{1,3})?)$")
_END_ISO = re.compile(r"(?<![0-9-])[0-9]{4}-(?:[0-9]{1,2}(?:-[0-9]?)?)?$")
_END_MONTH_PREFIX = re.compile(rf"(?<![\w])[0-9]{{1,2}}(?:st|nd|rd|th)?{_SP}(?:of{_SP})?([A-Za-z]{{1,8}})$")
_END_MONTH_YEAR = re.compile(rf"(?<![\w]){_MONTH_CI}\.?,?{_SP}[0-9]{{1,2}}(?:st|nd|rd|th)?,?(?:{_SP}[0-9]{{0,3}})?$")
_START_SUFFIX = re.compile(rf"^([A-Za-z]{{1,8}})\.?,?{_SP}(?:[0-9]{{1,2}}(?:st|nd|rd|th)?,?{_SP})?[0-9]{{1,4}}(?![0-9])")
_START_NUM = re.compile(
    r"^(?:[./-][0-9]{1,2}[./-][0-9]{2,4}|[0-9]{1,2}[./-][0-9]{2,4}|-?[0-9]{2}-[0-9]{2}"
    r"|[0-9]{1,2}(?:st|nd|rd|th)?,?[ ]+[0-9]{4})(?![0-9])"
)


def _boundary_fragments(u: Unit) -> list[tuple[int, int]]:
    """Pieces of a date a passage cut may have split, with no mention of their own: a trailing 'dd.m' or '13 Ju' at the end of a
    passage that a passage follows, a leading '.06.2024' or 'ne 2024' at the start of one that follows another. They are counted
    as dates we could not read, so a cut inside a date never makes the date vanish."""
    out: list[tuple[int, int]] = []
    text = u.text
    if u.has_next:
        end = u.end_nospace
        tail = text[max(0, end - 30) : end]
        base = max(0, end - 30)
        for rx in (_END_NUM, _END_ISO, _END_MONTH_YEAR):
            m = rx.search(tail)
            if m:
                out.append((base + m.start(), end))
        m = _END_MONTH_PREFIX.search(tail)
        if m:
            w = m.group(1).lower()
            if w not in _MONTH_ALL and any(mo.startswith(w) for mo in _MONTH_FULL):
                out.append((base + m.start(), end))
    if u.prev_end is not None:
        first = u.first_nospace
        head = text[first : first + 30]
        m = _START_SUFFIX.match(head)
        if m:
            w = m.group(1).lower()
            if w not in _MONTH_ALL and any(mo.endswith(w) for mo in _MONTH_FULL):
                out.append((first, first + m.end()))
        m = _START_NUM.match(head)
        if m:
            out.append((first, first + m.end()))
    return out


# ---- the document scan -------------------------------------------------------------------------------

_MONTH_NUM = {
    **{m.lower(): i for i, m in enumerate(calendar.month_name) if m},
    **{m.lower(): i for i, m in enumerate(calendar.month_abbr) if m},
    "sept": 9,
}


def _mk(y: int, m: int, d: int) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def _letters(s: str) -> tuple[int, int]:
    """(Latin letters, other letters) of a sentence."""
    latin = other = 0
    for ch in s:
        if ch.isalpha():
            if ch < "ɐ":
                latin += 1
            else:
                other += 1
    return latin, other


def _printed_ranges(units: list[Unit]) -> dict[int, list[tuple[int, int]]]:
    """Printed lines per unit: a date-bearing line among the first or last 3 non-blank lines of a page whose digit-masked form
    repeats on >= 3 pages of the document, or a line holding a print/stamp phrase. A repeated header/footer date is a template
    date: it is never evidence and never an event."""
    by_page: dict[int, list[tuple[int, int, int, str]]] = defaultdict(list)
    out: dict[int, list[tuple[int, int]]] = defaultdict(list)
    for ui, u in enumerate(units):
        pos = 0
        for line in u.text.split("\n"):
            s, e = pos, pos + len(line)
            pos = e + 1
            if line.strip():
                by_page[u.page].append((ui, s, e, line))
                if _PRINTED_PHRASE.search(line):
                    out[ui].append((s, e))
    pages_of: dict[str, set[int]] = defaultdict(set)
    cands: list[tuple[int, int, int, str]] = []
    for page, lines in by_page.items():
        for ui, s, e, line in lines[:3] + lines[-3:]:
            if re.search(r"[0-9]", line) and (_LIKE[0].search(line) or any(rx.search(line) for rx in _LIKE[1:4])):
                masked = re.sub(r"\s+", " ", re.sub(r"[0-9]", "#", line.strip().lower()))
                pages_of[masked].add(page)
                cands.append((ui, s, e, masked))
    for ui, s, e, masked in cands:
        if len(pages_of[masked]) >= 3:
            out[ui].append((s, e))
    return out


def _in(ranges: list[tuple[int, int]], s: int, e: int) -> bool:
    return any(s < re_ and e > rs for rs, re_ in ranges)


def scan_document(passages: list[tuple[str, int, int, str, int | str]], today: date | None = None) -> DocScan:
    """The scan of ONE document's passages (`(doc_id, page, start, text, span_id)` rows): units, sentences, every hit with its
    category, and the counts. Nothing is dropped: each date-like string ends in exactly one category."""
    today = today or date.today()
    units = prepare_units(passages)
    sents: list[Sent] = []
    sent_starts: list[list[int]] = []
    first_sent: dict[int, int] = {}
    for ui, u in enumerate(units):
        ss = sentences(u.text)
        first_sent[ui] = len(sents)
        sent_starts.append([s for s, _ in ss])
        for k, (s, e) in enumerate(ss):
            st = Sent(ui, s, e)
            if k == 0 and u.prev_end is not None and u.prev_last and u.prev_last not in SENTENCE_PUNCT:
                st.cut_fragment = True
            if k == len(ss) - 1 and u.has_next and u.text[s:e].rstrip()[-1:] not in SENTENCE_PUNCT:
                st.cut_fragment = True
            lat, oth = _letters(u.text[s:e])
            if oth and lat + oth >= 3:
                if oth / (lat + oth) > 0.5:
                    st.unread_language = True
                else:
                    st.other_language = True
            sents.append(st)
    printed = _printed_ranges(units)
    hits: list[Hit] = []
    carried = False
    carried_units = carried_chars = 0
    for ui, u in enumerate(units):
        qr, still = _quoted_ranges(u.text, carried)
        if still:
            carried_units += 1
            carried_chars += len(u.text)
            carried = carried_units < 8 and carried_chars < 3000
        else:
            carried, carried_units, carried_chars = False, 0, 0
        raw = _raw_mentions(u.text)
        like = _like_spans(u.text)
        for h in raw:
            h.unit = ui
            si = max(0, bisect.bisect_right(sent_starts[ui], h.start) - 1) if sent_starts[ui] else 0
            h.sent = first_sent[ui] + si if sents else 0
            h.doc_start, h.doc_end = u.start + h.start, u.start + h.end
            h.printed = _in(printed.get(ui, []), h.start, h.end)
            h.quoted = _in(qr, h.start, h.end)
            sstart = sents[h.sent].start if sents else 0
            before = u.text[max(sstart, h.start - 80) : h.start]
            h.reference = bool(_REFERENCE.search(before))
            h.scheduled = bool(_SCHEDULED.search(u.text[max(sstart, h.start - 60) : h.start]))
            hits.append(h)
        raw_starts = [h.start for h in raw]
        for fs, fe in _boundary_fragments(u):
            k = bisect.bisect_left(raw_starts, fe)
            if k > 0 and raw[k - 1].end > fs:
                continue  # a mention already covers it (rules E/S handle it)
            like.append((fs, fe))
        for ls, le in like:
            k = bisect.bisect_left(raw_starts, le)
            if k > 0 and raw[k - 1].end > ls:
                continue  # covered by a mention (raw is sorted and non-overlapping: the last one starting before `le` ends last)
            h = Hit(ui, 0, ls, le, u.text[ls:le], "suspect")
            si = max(0, bisect.bisect_right(sent_starts[ui], ls) - 1) if sent_starts[ui] else 0
            h.sent = first_sent[ui] + si if sents else 0
            h.doc_start, h.doc_end = u.start + ls, u.start + le
            h.not_read_reason = "not_recognised"
            h.printed = _in(printed.get(ui, []), ls, le)
            hits.append(h)
    _read_hits(units, sents, hits, today)
    scan = DocScan(units, sents, hits)
    scan.counts = _counts(scan)
    return scan


def _read_hits(units: list[Unit], sents: list[Sent], hits: list[Hit], today: date) -> None:
    def usable(h: Hit) -> bool:
        return not (h.printed or h.quoted or h.reference or sents[h.sent].unread_language)

    # the convention the document shows: unambiguous-by-itself numeric dates, outside quotes, reference clauses, printed lines
    day_ev: dict[str, Hit] = {}
    mon_ev: dict[str, Hit] = {}
    for h in hits:
        if h.kind == "numeric" and usable(h) and len(h.year_text) == 4 and not _edge_cut(units[h.unit], h):
            if h.a > 12 >= h.b:
                day_ev.setdefault(h.sep, h)
            elif h.b > 12 >= h.a:
                mon_ev.setdefault(h.sep, h)
    both = bool(day_ev) and bool(mon_ev)

    # full years of the document: all in one century, else two-digit years are not read
    years: list[int] = []
    for h in hits:
        if h.printed or h.kind in ("suspect", "number_like"):
            continue
        y = _full_year(h)
        if y is not None and MIN_YEAR <= y <= today.year + 1:
            years.append(y)
    centuries = {y // 100 * 100 for y in years}
    century = next(iter(centuries)) if len(centuries) == 1 else None

    def resolve_year(h: Hit) -> tuple[int | None, bool, str | None]:
        if len(h.year_text) == 4:
            return int(h.year_text), False, None
        if century is None:
            return None, True, "two_digit_year"
        y = century + int(h.year_text)
        nearest = min((abs(y - fy) for fy in years), default=99)
        if y < 1900 or y > today.year + 1 or nearest > 30:
            return None, True, "two_digit_year"
        return y, True, None

    for h in hits:
        u = units[h.unit]
        s = sents[h.sent] if sents else None
        cats: set[str] = set()
        if s is not None and s.unread_language:
            h.category, h.not_read_reason = "not_read", "language"
            continue
        if h.printed:
            cats.add("printed")
        if h.kind == "number_like":
            cats.add("number_like")
        if _edge_cut(u, h) and h.kind != "number_like":
            cats.add("not_read")  # rules E/S: it may be half of a date cut by A2: never read, never a guess
            h.not_read_reason = "cut_boundary"
        else:
            _read_one(h, resolve_year, day_ev, mon_ev, both, today, units)
            if h.not_read_reason:
                cats.add("not_read")
        if "not_read" not in cats:
            if h.scheduled:
                cats.add("scheduled")
            if h.approximate:
                cats.add("approximate")
            if h.kind.startswith("partial_"):
                cats.add("less_than_a_day")
            if h.how == "ambiguous":
                cats.add("ambiguous")
            if h.value is not None and h.how not in ("ambiguous",):
                cats.add("exact")
        if not cats:
            cats.add("not_read")
            h.not_read_reason = h.not_read_reason or "date_not_read"
        for c in PRECEDENCE:
            if c in cats:
                h.category = c
                break
        if h.category == "approximate":
            h.how = "approximate"


def _full_year(h: Hit) -> int | None:
    if h.kind == "numeric":
        return int(h.year_text) if len(h.year_text) == 4 else None
    m = re.search(r"(?<![0-9])([0-9]{4})(?![0-9])", h.text)
    return int(m.group(1)) if m and h.kind in ("written", "iso", "range", "partial_month_year") else None


def _edge_cut(u: Unit, h: Hit) -> bool:
    """Rules E and S (addenda 2 and 3): a mention touching a passage edge may be half of a date cut by A2."""
    if u.has_next and h.end == u.end_nospace:
        return True  # rule E: ends the passage with no sentence punctuation after it
    first = u.first_nospace
    if u.prev_end is not None and h.start == first:
        if u.start == u.prev_end:
            return True  # rule S, gap 0: the first token may be the end of a longer one
        if h.kind.startswith("partial_") or (h.kind == "numeric" and len(h.year_text) == 2):
            return True  # rule S, gap >= 1: a partial-type mention starting a passage
    return False


def _read_one(
    h: Hit,
    resolve_year: Callable[[Hit], tuple[int | None, bool, str | None]],
    day_ev: dict[str, Hit],
    mon_ev: dict[str, Hit],
    both: bool,
    today: date,
    units: list[Unit],
) -> None:
    if h.kind == "suspect":
        h.not_read_reason = h.not_read_reason or "ocr_like"
        return
    if h.kind == "number_like":
        return
    if h.kind == "range":
        if _SHARED_MONTH.search(h.text):
            ra, rb = _range_values(h, today)
        else:
            subs = [x for x in _raw_mentions(h.text) if x.kind in ("written", "iso", "numeric")]
            if len(subs) != 2:
                h.not_read_reason = "range_not_read"
                return
            for x in subs:
                x.unit, x.sent = h.unit, h.sent
                _read_one(x, resolve_year, day_ev, mon_ev, both, today, units)
            if any(x.not_read_reason for x in subs):
                h.not_read_reason = "range_not_read"
                return
            amb = next((x for x in subs if x.how == "ambiguous"), None)
            if amb is not None:
                h.how, h.readings = "ambiguous", amb.readings
                return
            ra, rb = subs[0].value, subs[1].value
        if ra is None or rb is None or rb < ra:
            h.not_read_reason = "range_not_read"
            return
        h.value, h.end_value, h.how = ra, rb, "range"
        return
    if h.kind in ("written", "iso"):
        v = _written_value(h)
        if v is None:
            h.not_read_reason = "date_not_read"
        elif not MIN_YEAR <= v.year <= today.year + 1:
            h.not_read_reason = "year_not_plausible"
        else:
            h.value, h.how = v, "written_month" if h.kind == "written" else "iso"
        return
    if h.kind.startswith("partial_"):
        m = re.search(r"([0-9]{4})", h.text)
        if m and not MIN_YEAR <= int(m.group(1)) <= today.year + 1:
            h.not_read_reason = "year_not_plausible"
        return
    # numeric
    y, two, why = resolve_year(h)
    if y is None:
        h.not_read_reason = why or "date_not_read"
        return
    if not MIN_YEAR <= y <= today.year + 1:
        h.not_read_reason = "year_not_plausible"
        return
    a, b = h.a, h.b
    if a > 12 and b > 12:
        h.not_read_reason = "date_not_read"
        return
    if a > 12 or b > 12:
        v = _mk(y, b, a) if a > 12 else _mk(y, a, b)
        if v is None:
            h.not_read_reason = "date_not_read"
            return
        h.value, h.how = v, "two_digit_with_century_evidence" if two else "only_valid_reading"
        return
    if a == b:
        v = _mk(y, b, a)
        if v is None:
            h.not_read_reason = "date_not_read"
            return
        h.value, h.how = v, "two_digit_with_century_evidence" if two else "day_equals_month"
        return
    dfirst, mfirst = _mk(y, b, a), _mk(y, a, b)
    if dfirst is None or mfirst is None:
        v = dfirst or mfirst
        if v is None:
            h.not_read_reason = "date_not_read"
        else:
            h.value, h.how = v, "only_valid_reading"
        return
    if both:
        h.how, h.readings = "ambiguous", (dfirst, mfirst)
        return
    e_day, e_mon = day_ev.get(h.sep), mon_ev.get(h.sep)
    if e_day is not None and e_mon is None:
        h.value, h.how = dfirst, "day_first_by_document_evidence"
        h.evidence = {"text": e_day.text, "page": units[e_day.unit].page}
    elif e_mon is not None and e_day is None:
        h.value, h.how = mfirst, "month_first_by_document_evidence"
        h.evidence = {"text": e_mon.text, "page": units[e_mon.unit].page}
    else:
        h.how, h.readings = "ambiguous", (dfirst, mfirst)


def _written_value(h: Hit) -> date | None:
    t = h.text
    m = _ISO.search(t) if h.kind == "iso" else None
    if m:
        return _mk(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    for rx, order in ((_W_NTH, "dmy"), (_W_DMY, "dmy"), (_W_MDY, "mdy")):
        m = rx.search(t)
        if m:
            if order == "dmy":
                d, mo, y = m.group(1), m.group(2), m.group(3)
            else:
                mo, d, y = m.group(1), m.group(2), m.group(3)
            num = _MONTH_NUM.get(mo.lower().rstrip("."))
            return _mk(int(y), num, int(d)) if num else None
    return None


def _range_values(h: Hit, today: date) -> tuple[date | None, date | None]:
    m = _SHARED_MONTH.search(h.text)
    if m:
        num = _MONTH_NUM.get(m.group(3).lower().rstrip("."))
        y = int(m.group(4))
        if not num or not MIN_YEAR <= y <= today.year + 1:
            return None, None
        return _mk(y, num, int(m.group(1))), _mk(y, num, int(m.group(2)))
    subs = [x for x in _raw_mentions(h.text) if x.kind in ("written", "iso", "numeric")]
    if len(subs) != 2:
        return None, None
    vals: list[date | None] = []
    for x in subs:
        if x.kind == "numeric":
            yr = int(x.year_text) if len(x.year_text) == 4 else None
            if yr is None or x.a > 12 and x.b > 12:
                return None, None
            if x.a <= 12 and x.b <= 12 and x.a != x.b:
                return None, None  # an ambiguous end makes the range unreadable here
            vals.append(_mk(yr, x.b, x.a) if x.a > 12 or x.a == x.b else _mk(yr, x.a, x.b))
        else:
            vals.append(_written_value(x))
    if vals[0] is not None and not MIN_YEAR <= vals[0].year <= today.year + 1:
        return None, None
    return vals[0], vals[1]


def _counts(scan: DocScan) -> dict[str, Any]:
    cat = Counter(h.category for h in scan.hits if h.not_read_reason != "language")
    lang = [h for h in scan.hits if h.not_read_reason == "language"]
    return {
        "hits": len(scan.hits),
        "by_category": dict(cat),
        "like_in_unread_sentences": len(lang),
        "sentences_not_read_language": sum(1 for s in scan.sents if s.unread_language),
        "sentences_other_language": sum(1 for s in scan.sents if s.other_language),
        "passages": len(scan.units),
    }
