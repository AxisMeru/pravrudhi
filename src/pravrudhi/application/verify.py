"""`verify(citation, quote_or_proposition)` (P3, `docs/decisions/LEG-PLAN-2026-09-23.md`).

Resolves a citation to a real case in the index via `case_index.citation_aliases` (populated by
`mine_aliases` from real judgment text elsewhere in the corpus), then checks whether the claimed quote
actually appears in that case's own text.

Scope: only SCC-family citations resolve today, because `mine_aliases` only mines "party v. party, (year) N
SCC page" strings. Every other reporter `citations.py` can parse (AIR, SCC OnLine, INSC, SCR, HC-neutral)
correctly comes back NOT_IN_INDEX -- an honest "no alias evidence", not a false claim either way. See
`tests/test_verify.py`'s module docstring.
"""

from __future__ import annotations

import sqlite3
from enum import StrEnum

from pravrudhi.application.citations import Citation, parse_citations


class VerifyResult(StrEnum):
    VERIFIED = "VERIFIED"
    EXISTS_QUOTE_NOT_FOUND = "EXISTS_QUOTE_NOT_FOUND"
    NOT_IN_INDEX = "NOT_IN_INDEX"
    MALFORMED = "MALFORMED"
    CONFLICT = "CONFLICT"


def _citation_key(c: Citation) -> str | None:
    """The lookup key `mine_aliases`/`citation_aliases` use -- only defined for the SCC family it mines."""
    if c.reporter == "SCC" and c.volume is not None:
        return f"({c.year}) {c.volume} SCC {c.page}"
    return None


def verify(conn: sqlite3.Connection, citation_text: str, quote_or_proposition: str) -> VerifyResult:
    citations = parse_citations(citation_text)
    if len(citations) != 1:
        return VerifyResult.MALFORMED

    key = _citation_key(citations[0])
    if key is None:
        return VerifyResult.NOT_IN_INDEX

    conn.row_factory = sqlite3.Row
    alias_rows = conn.execute(
        "SELECT DISTINCT party_1, party_2 FROM citation_aliases WHERE citation = ?", (key,)
    ).fetchall()
    if not alias_rows:
        return VerifyResult.NOT_IN_INDEX
    if len(alias_rows) > 1:
        return VerifyResult.CONFLICT

    party_1, party_2 = alias_rows[0]["party_1"], alias_rows[0]["party_2"]
    # Corpus documents are titled from party names (SC PDF filenames, InJudgements `Titles`) -- an FTS5
    # match on both party names' first significant token finds the resolved case's own document, not the
    # citing one. A short/common-word first token (e.g. "The") is not filtered here; a real corpus mostly
    # avoids that shape ("The State v. X" is common in the OTHER direction, party_1 first) -- not proven
    # bulletproof at full-corpus scale, flagged as a known simplification rather than silently assumed safe.
    t1 = party_1.split()[0]
    t2 = party_2.split()[0]
    case_rows = conn.execute(
        "SELECT case_id, text FROM cases WHERE title MATCH ?", (f'"{t1}" AND "{t2}"',)
    ).fetchall()
    if not case_rows:
        return VerifyResult.NOT_IN_INDEX

    normalized_quote = " ".join(quote_or_proposition.split())
    for row in case_rows:
        normalized_text = " ".join(row["text"].split())
        if normalized_quote in normalized_text:
            return VerifyResult.VERIFIED
    return VerifyResult.EXISTS_QUOTE_NOT_FOUND
