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

import re
import sqlite3
from dataclasses import dataclass
from difflib import SequenceMatcher
from enum import StrEnum

from pravrudhi.application.citations import Citation, parse_citations

# Leading words that can precede a real party name in running prose ("This Court in X v. Y...", "In X v.
# Y..."), stripped repeatedly from the front before comparing two mentions of "the same" party. Found by
# hand measuring resolution precision on 100 real citations (LEG-PLAN P3): mine_aliases's own regex (a run
# of Title-Case tokens) cannot tell "In" the filler word from "In" a genuine name, since both are
# Title-Case; this list is the disambiguation `citations.py`'s stricter regex didn't fully cover either.
_LEADING_FILLER = re.compile(r"^(?:this|that|in|the|court|held|observed|noted|see)\s+", re.IGNORECASE)
# Trailing honorific/plural variants that name the same party ("Anr." vs "Ors." vs the bare name) --
# stripped for comparison only, never for the stored/displayed party name.
_TRAILING_HONORIFIC = re.compile(r"\s+(?:and|&)?\s*(?:anr\.?|ors\.?|others?|etc\.?)\s*$", re.IGNORECASE)

# A word broken across a PDF line wrap with a hyphen at the break point ("specific" -> "specific-\nmance"
# for "performance") -- real `pypdf`/`pdftotext` output, seen building this corpus's own index. Only a
# hyphen immediately followed by a newline is treated as a wrap artefact; a real hyphenated word ("time-
# barred") keeps its hyphen because the char right after it is not a newline.
_LINE_WRAP_HYPHEN = re.compile(r"-\n")
_LIGATURES = {"ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl", "ﬃ": "ffi", "ﬄ": "ffl"}


def normalize_text_for_match(text: str) -> str:
    """The comparison-only normalization for the quote/proposition substring check: undo a PDF line-wrap
    hyphen, expand ligature glyphs to their plain letters, collapse whitespace. Applied identically to both
    the proposition and the indexed document text, so a real quote copied verbatim from the ORIGINAL source
    (not from this index's own extracted copy) still matches; never applied to what gets stored or shown."""
    s = _LINE_WRAP_HYPHEN.sub("", text)
    for lig, plain in _LIGATURES.items():
        s = s.replace(lig, plain)
    return " ".join(s.split())


def _strip_filler(name: str) -> str:
    """Whitespace-collapse and drop a leading filler phrase (repeatedly -- "This Court in In X" happens
    when two prose patterns overlap). Light-touch: keeps periods and internal spacing, so the result is
    still a fair token to search the FTS5 `cases.title` index with."""
    s = " ".join(name.split())
    while True:
        stripped = _LEADING_FILLER.sub("", s)
        if stripped == s:
            return s
        s = stripped


def normalize_party_name(name: str) -> str:
    """Fold two mentions of the same real party into the same CONFLICT-grouping key: `_strip_filler` plus
    drop a trailing honorific, drop every period (initials vary in exactly how much whitespace surrounds
    each one -- "S.A. Kamtam" vs "S. A.  Kamtam" -- real OCR/PDF-extraction noise, not a different person),
    lowercase. Deliberately more aggressive than `_strip_filler` alone, and NOT used for the FTS5 lookup
    below: collapsing "S.A." to "sa" would stop matching a corpus title that keeps initials space-separated
    ("S A Kamtam", from the SC PDF filename convention) -- a real bug this function's own tests caught."""
    s = _strip_filler(name)
    s = _TRAILING_HONORIFIC.sub("", s)
    s = s.replace(".", "")
    s = " ".join(s.split()).lower()
    # A run of single-letter initials also varies in whether OCR/PDF extraction left a space between them
    # ("s a kamtam" vs "sa kamtam" -- both from "S.A. Kamtam", just with the source PDF's own internal
    # spacing around the period differing). Merge adjacent single-letter tokens into one, repeatedly (three
    # or more initials in a row need more than one pass).
    while True:
        merged = re.sub(r"\b([a-z])\s+(?=[a-z]\b)", r"\1", s)
        if merged == s:
            break
        s = merged
    return s.strip(" ,")


#: Conservative on purpose (reviewer 2, P3 audit 2026-09-24): used ONLY to confirm a candidate the exact
#: citation-key lookup already narrowed to (via alias party tokens), never to search the whole corpus for
#: a plausible-looking title on its own.
_FUZZY_THRESHOLD = 0.9


def _fuzzy_confirm(conn: sqlite3.Connection, party_1: str, party_2: str) -> list[sqlite3.Row]:
    """The exact `"t1" AND "t2"` FTS5 match found nothing -- fall back to a real spelling variant, e.g.
    "Ramchandran" (as mined from a citing document) vs "Ramachandran" (the actual corpus title).

    Scans every case's title directly rather than pre-filtering through an FTS `"t1" OR "t2"` query: a real
    corpus miss (P3 re-measurement, 2026-09-24, idx 69 against the full 38,657-title corpus) showed that
    pre-filter actively hides the correct match whenever one of the two tokens is generic ("State" alone
    matched 14,221 titles) -- the real title shares zero tokens with the misspelled name and is just one of
    thousands of equally-ranked "State" hits, so any LIMIT on the OR query can cut it off before the ratio
    check ever sees it. A full scan is the conservative-but-correct fix: title-only comparison keeps it to
    ~1s even at full-corpus scale (this function is a fallback on the already-rare "exact match failed"
    path, never the hot path), and the `_FUZZY_THRESHOLD` ratio floor still does all the actual filtering --
    this function widens what gets INSPECTED, not what gets ACCEPTED."""
    target = normalize_party_name(f"{party_1} {party_2}")
    confirmed = []
    for row in conn.execute("SELECT case_id, title, text FROM cases"):
        ratio = SequenceMatcher(None, target, normalize_party_name(row["title"])).ratio()
        if ratio >= _FUZZY_THRESHOLD:
            confirmed.append(row)
    return confirmed


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


@dataclass(frozen=True)
class ResolvedAlias:
    """The outcome of resolving a citation KEY to case row(s) in the index, stopping short of any quote
    check. `status` is `NOT_IN_INDEX` exactly when `case_rows` is empty. `CONFLICT` carries one candidate
    row PER genuinely distinct party-pair group (Lead-2, prereg v3 R1 rejection of 90c101b: the signed
    protocol's §4 CONFLICT bucket needs a labeler to actually see the candidates and judge "real ambiguity"
    vs "normalization bug" -- a bare CONFLICT with the candidates discarded cannot produce that judgment).
    Any other status is a successful resolution (`status` is `None`) to one or more rows (rows agree after
    party-name normalization, so more than one row here means the SAME real case indexed more than once,
    not an ambiguity -- a real CONFLICT already returned above it)."""

    status: VerifyResult | None
    case_rows: list[sqlite3.Row]


def _resolve_party_pair(conn: sqlite3.Connection, party_1: str, party_2: str) -> list[sqlite3.Row]:
    """The exact-title FTS match -> `_fuzzy_confirm` fallback for one already-normalized party pair,
    extracted so `resolve_citation_key` can run it once per candidate group on a CONFLICT (previously run
    only for the single-group case) without duplicating the lookup logic.

    Corpus documents are titled from party names (SC PDF filenames, InJudgements `Titles`) -- an FTS5 match
    on both party names' first significant token finds the resolved case's own document, not the citing
    one. `_strip_filler`'s output is used (not the raw alias text, and not `normalize_party_name`'s more
    aggressive period-stripping -- that would collapse "S.A." to "sa" and stop matching a title that keeps
    initials space-separated, e.g. "S A Kamtam"), so a leading filler word that survived mining ("In
    Narandas Karsondas") doesn't send the lookup hunting for a document titled "In ...". A short/common-word
    first token (e.g. "the") is not filtered here; a real corpus mostly avoids that shape ("The State v. X"
    is common in the OTHER direction, party_1 first) -- not proven bulletproof at full-corpus scale, flagged
    as a known simplification rather than silently assumed safe."""
    t1 = _strip_filler(party_1).split()[0]
    t2 = _strip_filler(party_2).split()[0]
    case_rows = conn.execute(
        "SELECT case_id, title, text FROM cases WHERE title MATCH ?", (f'"{t1}" AND "{t2}"',)
    ).fetchall()
    if not case_rows:
        case_rows = _fuzzy_confirm(conn, party_1, party_2)
    return list(case_rows)


def resolve_citation_key(conn: sqlite3.Connection, key: str) -> ResolvedAlias:
    """`verify()`'s resolution step alone (citation-key lookup -> party-group -> exact-title FTS match ->
    `_fuzzy_confirm` fallback), extracted so the P3 prereg's resolution-precision measurement
    (`application.p3_prereg`) can label exactly what the system resolved a sampled alias to, without also
    running (or needing) a quote check -- resolution-precision asks "is the resolved case the right case",
    a question `verify()` alone has no way to answer since it only ever returns the end-to-end enum."""
    conn.row_factory = sqlite3.Row
    alias_rows = conn.execute(
        "SELECT DISTINCT party_1, party_2 FROM citation_aliases WHERE citation = ?", (key,)
    ).fetchall()
    if not alias_rows:
        return ResolvedAlias(VerifyResult.NOT_IN_INDEX, [])

    # Group by NORMALIZED party pair, not raw string equality -- two mentions of the same real case
    # (OCR line-break noise, a residual leading-filler word) must not count as a real conflict. Only a
    # citation string genuinely attributed to two DIFFERENT parties after normalization is a real CONFLICT.
    groups: dict[tuple[str, str], sqlite3.Row] = {}
    for row in alias_rows:
        gkey = (normalize_party_name(row["party_1"]), normalize_party_name(row["party_2"]))
        groups.setdefault(gkey, row)

    if len(groups) > 1:
        # A real conflict: resolve EACH distinct party-pair to its own candidate row(s), rather than
        # discarding them, so a labeler can actually see what the citation might point to and judge which
        # of the prereg's two categories this is -- a genuine ambiguity in the source text, or a
        # normalization bug in this module that should have folded these into one group.
        candidates: list[sqlite3.Row] = []
        for group_row in groups.values():
            candidates.extend(_resolve_party_pair(conn, group_row["party_1"], group_row["party_2"]))
        return ResolvedAlias(VerifyResult.CONFLICT, candidates)

    row = next(iter(groups.values()))
    case_rows = _resolve_party_pair(conn, row["party_1"], row["party_2"])
    if not case_rows:
        return ResolvedAlias(VerifyResult.NOT_IN_INDEX, [])
    return ResolvedAlias(None, case_rows)


def verify(conn: sqlite3.Connection, citation_text: str, quote_or_proposition: str) -> VerifyResult:
    citations = parse_citations(citation_text)
    if len(citations) != 1:
        return VerifyResult.MALFORMED

    key = _citation_key(citations[0])
    if key is None:
        return VerifyResult.NOT_IN_INDEX

    resolved = resolve_citation_key(conn, key)
    if resolved.status is not None:
        return resolved.status

    normalized_quote = normalize_text_for_match(quote_or_proposition)
    for row in resolved.case_rows:
        normalized_text = normalize_text_for_match(row["text"])
        if normalized_quote in normalized_text:
            return VerifyResult.VERIFIED
    return VerifyResult.EXISTS_QUOTE_NOT_FOUND
