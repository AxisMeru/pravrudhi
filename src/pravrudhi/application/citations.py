"""Typed parsers for Indian legal citations (P3, `docs/decisions/LEG-PLAN-2026-09-23.md`).

Each reporter family below is a regex plus a small builder that turns the match into a `Citation`. The
regexes were written against strings MINED from real judgment text -- see `tests/test_citations.py`'s module
docstring for exactly where each fixture came from and which one (HC-neutral) has no real hit in the two
corpora available here and is built from the published circular format instead.

Deliberately conservative: a malformed or partial citation (e.g. "AIR 1958 SC" with no page) is not matched
rather than guessed at, because a wrong parse that still "looks like" a citation is worse for this feature
than an unparsed string that `verify()` can report as MALFORMED from a different path.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

from pravrudhi.application.nyaya import CITE as _BRACKET_CITE


@dataclass(frozen=True)
class Citation:
    """One parsed citation. `span` is the (start, end) character offset in the input text it came from."""

    reporter: str
    year: int | None
    volume: int | None
    page: int
    court: str | None
    span: tuple[int, int]


#: A page is at most this many digits and a volume at most four (every real citation is far below both). A longer digit run is not
#: a page, and `int()` raises ValueError on a run of more than 4,300 digits, which in text pasted into a route (intake takes up to
#: 200,000 characters) was an uncaught error. The patterns below bound their digit runs, so such a run simply does not match.
_MAX_PAGE_DIGITS = 9

# Real judgment text carries a stray newline or extra space wherever a PDF's line wrapped mid-citation (see
# test_insc_embedded_newline_mined) -- every pattern below uses `\s+` (not a literal space) between tokens for
# exactly that reason, and `\s*` where the mined fixtures show both "SCR197" and "SCR 197" for the same
# reporter.

_AIR = re.compile(r"\bAIR\s+(?P<year>\d{4})\s+(?P<court>[A-Z]{2,4}|[A-Z][a-z]+)\s+(?P<page>\d{1,9})\b")

# SCC citations appear in the corpus under three different bracket placements for the same citation --
# found by a real verify() miss (P3 audit, 2026-09-24): a case's own "Equivalent citations" line cited
# itself as "1996 SCC (3) 709", not the "(1996) 3 SCC 709" form the parser only handled before. All three
# are MINED (see tests/test_citations.py for the exact real strings each was built and checked against).
_SCC = re.compile(r"\((?P<year>\d{4})\)\s*(?P<volume>\d{1,4})\s*SCC\s+(?P<page>\d{1,9})\b")
_SCC_YEAR_SCC_PAREN_VOLUME = re.compile(r"\b(?P<year>\d{4})\s*SCC\s*\((?P<volume>\d{1,4})\)\s*(?P<page>\d{1,9})\b")
_SCC_YEAR_PAREN_VOLUME_SCC = re.compile(r"\b(?P<year>\d{4})\s*\((?P<volume>\d{1,4})\)\s*SCC\s*(?P<page>\d{1,9})\b")

# The SCC supplementary-volume form, "1992 Supp (1) SCC 335" or "(1992) Supp (1) SCC 335": a real reporter form the index does not
# mine, so it parses and then answers NOT_IN_INDEX (a reporter the check cannot look up), never MALFORMED (the caller's error).
_SCC_SUPP = re.compile(
    r"\(?\b(?P<year>\d{4})\)?\s*Supp\.?\s*(?:\((?P<volume>\d{1,4})\)\s*)?SCC\s+(?P<page>\d{1,9})\b", re.IGNORECASE
)

_SCC_ONLINE = re.compile(
    r"\b(?P<year>\d{4})\s+SCC\s+OnLine\s+(?P<court>[A-Za-z]{2,4})\s+(?P<page>\d{1,9})\b"
)

_INSC = re.compile(r"\b(?P<year>\d{4})\s+INSC\s+(?P<page>\d{1,9})\b")

# The volume and the whitespace after it are ONE optional group: two adjacent `\s*` around an optional group backtracked
# quadratically on a long run of whitespace after the bracketed year (R2).
_SCR = re.compile(r"\[(?P<year>\d{4})\]\s*(?:(?P<volume>\d{1,4})\s*)?SCR\s*(?P<page>\d{1,9})\b")

# Published circular format (e.g. Delhi HC's 2023 neutral-citation practice direction): YYYY:CODE:NNNN. No
# real hit in either corpus available here (see module docstring on `test_hc_neutral_spec`); the strict
# no-whitespace form below is the documented shape, deliberately not loosened to also match the false
# positives found while mining ("2014:\nBETWEEN:\n1" etc. -- see the mining note in tests).
_HC_NEUTRAL = re.compile(r"\b(?P<year>\d{4}):(?P<court>[A-Z]{2,6}):(?P<page>\d{1,9})\b")


def _air(m: re.Match[str]) -> Citation:
    return Citation(
        reporter="AIR",
        year=int(m["year"]),
        volume=None,
        page=int(m["page"]),
        court=m["court"],
        span=m.span(),
    )


def _scc(m: re.Match[str]) -> Citation:
    return Citation(
        reporter="SCC",
        year=int(m["year"]),
        volume=int(m["volume"]),
        page=int(m["page"]),
        court=None,
        span=m.span(),
    )


def _scc_supp(m: re.Match[str]) -> Citation:
    vol = m["volume"]
    return Citation(
        reporter="SCC Supp",
        year=int(m["year"]),
        volume=int(vol) if vol else None,
        page=int(m["page"]),
        court=None,
        span=m.span(),
    )


def _scc_online(m: re.Match[str]) -> Citation:
    return Citation(
        reporter="SCC OnLine",
        year=int(m["year"]),
        volume=None,
        page=int(m["page"]),
        court=m["court"],
        span=m.span(),
    )


def _insc(m: re.Match[str]) -> Citation:
    return Citation(
        reporter="INSC",
        year=int(m["year"]),
        volume=None,
        page=int(m["page"]),
        court="SC",
        span=m.span(),
    )


def _scr(m: re.Match[str]) -> Citation:
    vol = m["volume"]
    return Citation(
        reporter="SCR",
        year=int(m["year"]),
        volume=int(vol) if vol else None,
        page=int(m["page"]),
        court=None,
        span=m.span(),
    )


def _hc_neutral(m: re.Match[str]) -> Citation:
    return Citation(
        reporter="HC-neutral",
        year=int(m["year"]),
        volume=None,
        page=int(m["page"]),
        court=m["court"],
        span=m.span(),
    )


# Order matters: SCR's `[YYYY] N SCR P` and HC-neutral's `YYYY:CODE:P` cannot collide (different brackets),
# but AIR is checked before HC-neutral is irrelevant here since neither can match the other's span.
_PATTERNS: tuple[tuple[re.Pattern[str], Callable[[re.Match[str]], Citation]], ...] = (
    (_AIR, _air),
    (_SCC, _scc),
    (_SCC_YEAR_SCC_PAREN_VOLUME, _scc),
    (_SCC_YEAR_PAREN_VOLUME_SCC, _scc),
    (_SCC_SUPP, _scc_supp),
    (_SCC_ONLINE, _scc_online),
    (_INSC, _insc),
    (_SCR, _scr),
    (_HC_NEUTRAL, _hc_neutral),
)


def _bracket_tag(m: re.Match[str]) -> Citation:
    # CITE captures "Act/Section N" or "Act/Section N(x)" as one group (see nyaya.py). Split it back apart:
    # the reporter is the act name, the page is the section number with any subsection stripped.
    body = m.group(1)
    act, _, rest = body.partition("/")
    rest = rest.removeprefix("Section ").removeprefix("Article ")
    section_num = re.match(r"\d+", rest)
    page = int(section_num.group()) if section_num and len(section_num.group()) <= _MAX_PAGE_DIGITS else 0
    return Citation(reporter=act, year=None, volume=None, page=page, court=None, span=m.span())


def parse_citations(text: str) -> list[Citation]:
    """Find every citation in `text`, left to right, each reporter family tried at every position.

    Overlapping matches from different families are both kept (the input is presumed not to contain
    adversarial overlaps between reporter shapes; none of the six patterns above can start at the same
    offset as another given their distinct leading tokens: `AIR`, `(`, digits+`SCC OnLine`, digits+`INSC`,
    `[`, digits+`:`).
    """
    found: list[Citation] = []
    for pattern, builder in _PATTERNS:
        for m in pattern.finditer(text):
            found.append(builder(m))
    for m in _BRACKET_CITE.finditer(text):
        found.append(_bracket_tag(m))
    found.sort(key=lambda c: c.span[0])
    return found
