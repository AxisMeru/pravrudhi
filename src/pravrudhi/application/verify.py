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
import unicodedata
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
# Typographic variants of the same punctuation, folded for the comparison only (#330, found by the #529 probe: a
# quote copied with curly quotes or an en/em dash was reported not found against text that carries the straight
# form, or the reverse). One character to one character, EXCEPT the soft hyphen (U+00AD), which is deleted (it is
# invisible, and a PDF line break can leave one inside a word); no other character is dropped or added. There is NO
# case folding, no partial match and no fuzzy match: the quote must still occur verbatim after the fold.
_PUNCTUATION_FOLD = str.maketrans(
    {
        "‘": "'",
        "’": "'",
        "‚": "'",
        "‛": "'",
        "“": '"',
        "”": '"',
        "„": '"',
        "‟": '"',
        "‐": "-",
        "‑": "-",
        "‒": "-",
        "–": "-",
        "—": "-",
        "―": "-",
        "−": "-",
        "­": "",
    }
)


def normalize_text_for_match(text: str) -> str:
    """The comparison-only normalization for the quote/proposition substring check: undo a PDF line-wrap
    hyphen, expand ligature glyphs to their plain letters, collapse whitespace. Applied identically to both
    the proposition and the indexed document text, so a real quote copied verbatim from the ORIGINAL source
    (not from this index's own extracted copy) still matches; never applied to what gets stored or shown."""
    s = _LINE_WRAP_HYPHEN.sub("", text)
    for lig, plain in _LIGATURES.items():
        s = s.replace(lig, plain)
    s = s.translate(_PUNCTUATION_FOLD)
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
    this function widens what gets INSPECTED, not what gets ACCEPTED. Only the title column is read during the scan."""
    target = normalize_party_name(f"{party_1} {party_2}")
    # Titles only: the `text` column is the whole judgment, and a scan that dragged it through memory for every
    # case would be an attacker-triggerable unbounded read (R1, #181). The full row is fetched for the few that
    # clear the ratio floor, after the scan.
    confirmed_ids = [
        row["case_id"]
        for row in conn.execute("SELECT case_id, title FROM cases")
        if SequenceMatcher(None, target, normalize_party_name(row["title"])).ratio() >= _FUZZY_THRESHOLD
    ]
    confirmed: list[sqlite3.Row] = []
    for i in range(0, len(confirmed_ids), 500):
        chunk = confirmed_ids[i : i + 500]
        marks = ",".join("?" * len(chunk))
        confirmed.extend(conn.execute(f"SELECT case_id, title, text FROM cases WHERE case_id IN ({marks})", chunk))
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
    #: CONFLICT only: one (alias row, its resolved case rows) per distinct party-pair group, so the caller can apply the
    #: name and year tests per candidate. Empty for every other outcome.
    group_candidates: tuple[tuple[sqlite3.Row, tuple[sqlite3.Row, ...]], ...] = ()


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
    case_rows = conn.execute("SELECT case_id, title, text FROM cases WHERE title MATCH ?", (f'"{t1}" AND "{t2}"',)).fetchall()
    if not case_rows:
        case_rows = _fuzzy_confirm(conn, party_1, party_2)
    return list(case_rows)


# Tokens too common to tell two parties apart (a party made only of these cannot be used to disambiguate).
_COMMON_TOKENS = frozenset(
    {
        "the",
        "of",
        "and",
        "ors",
        "anr",
        "others",
        "another",
        "v",
        "vs",
        "versus",
        "state",
        "union",
        "india",
        "m",
        "s",
        "shri",
        "smt",
        "mr",
        "ms",
        "dr",
        "co",
        "ltd",
        "pvt",
        "limited",
        "company",
        "commissioner",
        "govt",
        "government",
    }
)


def _distinctive_tokens(name: str) -> frozenset[str]:
    s = unicodedata.normalize("NFKD", normalize_party_name(name)).encode("ascii", "ignore").decode()
    # tokens of one or two letters are initials (`S.A.` folds to `sa`): noise for telling parties apart
    return frozenset(t for t in re.sub(r"[^a-z0-9 ]+", " ", s.lower()).split() if len(t) > 2 and t not in _COMMON_TOKENS)


# Share of an alias party's distinctive tokens the claimed name must contain. A shared surname alone (Ram Singh vs
# Mohan Singh) is 0.5 and does not agree (R2 on #331).
_PARTY_CONTAINMENT_MIN = 0.7


def _group_agrees_with_claim(claimed: frozenset[str], row: sqlite3.Row) -> bool:
    """Two-sided: for EACH party of the alias group that has distinctive tokens, at least `_PARTY_CONTAINMENT_MIN`
    of its distinctive tokens are in the claimed name. A party with no distinctive token is skipped; a group with
    no distinctive token at all cannot be told apart and does not agree."""
    sides = [t for t in (_distinctive_tokens(row["party_1"]), _distinctive_tokens(row["party_2"])) if t]
    return bool(sides) and all(len(side & claimed) / len(side) >= _PARTY_CONTAINMENT_MIN for side in sides)


def _disambiguate(
    conn: sqlite3.Connection, groups: dict[tuple[str, str], sqlite3.Row], claimed_name: str, claimed_year: int | None
) -> list[sqlite3.Row] | None:
    """Pick the ONE alias group the claim points to, or None (the caller then returns CONFLICT as before). By
    claimed party names first; if more than one group still agrees, by year (the resolved case's decision year
    within one of the citation's year). Never guesses: zero or several survivors mean None. Returns the resolved
    case rows of the surviving group."""
    claimed = _distinctive_tokens(claimed_name)
    if not claimed:
        return None
    named = [g for g in groups.values() if _group_agrees_with_claim(claimed, g)]
    if not named:
        return None
    resolved = [_resolve_party_pair(conn, g["party_1"], g["party_2"]) for g in named]
    if any(not rows for rows in resolved):
        # a name-agreeing group that resolves to no case is still a candidate the claim could mean:
        # do not drop it and pick its sibling
        return None
    if len(resolved) == 1:
        return resolved[0]
    # Several name-agreeing groups: they may all resolve to the SAME case (alias spellings of one party pair),
    # so compare CASES, not groups.
    cases = {r["case_id"]: r for rows in resolved for r in rows}
    if len(cases) > 1 and claimed_year is not None:
        near = {}
        for cid, r in cases.items():
            y = conn.execute("SELECT year FROM cases WHERE case_id = ?", (cid,)).fetchone()
            try:
                if y is not None and y[0] not in (None, "") and abs(int(y[0]) - claimed_year) <= 1:
                    near[cid] = r
            except (TypeError, ValueError):
                continue
        cases = near
    return list(cases.values()) if len(cases) == 1 else None


def resolve_citation_key(
    conn: sqlite3.Connection, key: str, claimed_name: str | None = None, claimed_year: int | None = None
) -> ResolvedAlias:
    """`verify()`'s resolution step alone (citation-key lookup -> party-group -> exact-title FTS match ->
    `_fuzzy_confirm` fallback), extracted so the P3 prereg's resolution-precision measurement
    (`application.p3_prereg`) can label exactly what the system resolved a sampled alias to, without also
    running (or needing) a quote check -- resolution-precision asks "is the resolved case the right case",
    a question `verify()` alone has no way to answer since it only ever returns the end-to-end enum."""
    conn.row_factory = sqlite3.Row
    alias_rows = conn.execute("SELECT DISTINCT party_1, party_2 FROM citation_aliases WHERE citation = ?", (key,)).fetchall()
    if not alias_rows:
        return ResolvedAlias(VerifyResult.NOT_IN_INDEX, [])

    # Group by NORMALIZED party pair, not raw string equality -- two mentions of the same real case
    # (OCR line-break noise, a residual leading-filler word) must not count as a real conflict. Only a
    # citation string genuinely attributed to two DIFFERENT parties after normalization is a real CONFLICT.
    groups: dict[tuple[str, str], sqlite3.Row] = {}
    for row in alias_rows:
        gkey = (normalize_party_name(row["party_1"]), normalize_party_name(row["party_2"]))
        groups.setdefault(gkey, row)

    if len(groups) > 1 and claimed_name:
        picked = _disambiguate(conn, groups, claimed_name, claimed_year)
        if picked is not None:
            return ResolvedAlias(None, picked)

    if len(groups) > 1:
        # A real conflict: resolve EACH distinct party-pair to its own candidate row(s), rather than
        # discarding them, so a labeler can actually see what the citation might point to and judge which
        # of the prereg's two categories this is -- a genuine ambiguity in the source text, or a
        # normalization bug in this module that should have folded these into one group.
        candidates: list[sqlite3.Row] = []
        per_group: list[tuple[sqlite3.Row, tuple[sqlite3.Row, ...]]] = []
        for group_row in groups.values():
            rows = _resolve_party_pair(conn, group_row["party_1"], group_row["party_2"])
            candidates.extend(rows)
            per_group.append((group_row, tuple(rows)))
        return ResolvedAlias(VerifyResult.CONFLICT, candidates, tuple(per_group))

    row = next(iter(groups.values()))
    case_rows = _resolve_party_pair(conn, row["party_1"], row["party_2"])
    if not case_rows:
        return ResolvedAlias(VerifyResult.NOT_IN_INDEX, [])
    return ResolvedAlias(None, case_rows)


def _title_carries_the_cited_parties(conn: sqlite3.Connection, key: str, title: str) -> bool:
    """Whether a candidate's title carries EVERY distinctive word of both parties of some alias group of the citation key
    (containment of the alias in the title, not equality: a real title often adds words, "State of Rajasthan Jaipur vs
    Balchand Baliay" for the alias "State of Rajasthan v. Balchand"), or is a spelling variant at the verifier's own 0.9
    ratio. A candidate that shares only SOME party words (a "Balchand Jain vs State of Madhya Pradesh" for that alias) is a
    different case and may not verify a quote. A party with no distinctive word is skipped, as in `_group_agrees_with_claim`;
    a group with no distinctive word at all cannot be told apart and does not exclude the candidate.
    Limit, stated: a different case whose title contains ALL the alias words plus others still passes; only the verbatim
    quote check stands between such a case and VERIFIED."""
    tokens = _distinctive_tokens(title)
    groups = list(conn.execute("SELECT DISTINCT party_1, party_2 FROM citation_aliases WHERE citation = ?", (key,)))
    if not groups:
        return True
    for party_1, party_2 in groups:
        sides = [t for t in (_distinctive_tokens(party_1), _distinctive_tokens(party_2)) if t]
        if not sides or all(side <= tokens for side in sides):
            return True
        names = normalize_party_name(f"{party_1} {party_2}")
        if SequenceMatcher(None, names, normalize_party_name(title)).ratio() >= _FUZZY_THRESHOLD:
            return True
    return False


def _rows_in_cited_year(conn: sqlite3.Connection, rows: list[sqlite3.Row], cited_year: int | None) -> list[sqlite3.Row]:
    """The candidate rows whose recorded decision year is within one year of the cited (reporting) year (the window the
    alias disambiguation uses); an unreadable year
    is not a match: a record with NO recorded year (213 of the 26,687 SC PDFs and every InJudgements row in the 7 Oct 2026
    index) can never qualify, so a case whose only record has no year answers NOT_IN_INDEX even though it is indexed (pinned
    by tests as intended; a distinct status for an ungateable year is post-MVP). With no cited year there is nothing to check
    against, so no row qualifies."""
    if cited_year is None:
        return []
    ok: list[sqlite3.Row] = []
    for row in rows:
        found = conn.execute("SELECT year FROM cases WHERE case_id = ?", (row["case_id"],)).fetchone()
        try:
            year = int(found[0]) if found is not None else None
        except (TypeError, ValueError):
            year = None
        if year is not None and abs(year - cited_year) <= 1:
            ok.append(row)
    return ok


_PARTY_SEPARATOR = re.compile(r"\s+(?:v\.?|vs\.?|versus)\s+", re.IGNORECASE)


def _name_before_citation(citation_text: str, citation: Citation) -> str | None:
    """The "A v. B" typed in front of the citation, or None when the text carries no party pair."""
    prefix = citation_text[: citation.span[0]].strip(" \t\r\n,;:-")
    return prefix if prefix and _PARTY_SEPARATOR.search(prefix) else None


def _narrow_conflict(
    conn: sqlite3.Connection, key: str, resolved: ResolvedAlias, claimed_name: str | None, year: int | None, normalized_quote: str
) -> ResolvedAlias | VerifyResult:
    """A CONFLICT is kept only while at least two candidates survive BOTH the name and the year test and the exact quote
    is not found in exactly one of them (Lead-2, 8 Oct: an exact-text citation that resolves to one case must not be a
    CONFLICT). Never guesses: a surviving alias group that resolves to no case is an uncheckable candidate and keeps the
    CONFLICT (#331); nothing surviving, or an empty quote, keeps it too. Returns the resolution to continue with
    (a single surviving case) or a final VerifyResult."""
    if not normalized_quote or not resolved.group_candidates:
        return resolved
    claimed = _distinctive_tokens(claimed_name) if claimed_name else frozenset()
    survivors: list[tuple[sqlite3.Row, tuple[sqlite3.Row, ...]]] = []
    for group_row, rows in resolved.group_candidates:
        if claimed and not _group_agrees_with_claim(claimed, group_row):
            continue  # name test
        if rows:
            # the SAME year window and title-carries-the-parties test the quote check itself uses (#723), so a narrowed
            # CONFLICT can never verify against a case that `verify()` would not have checked
            in_year = _rows_in_cited_year(conn, list(rows), year)
            rows = tuple(r for r in in_year if _title_carries_the_cited_parties(conn, key, r["title"]))
            if not rows:
                continue  # year or title test: no case of this group is checkable
        survivors.append((group_row, rows))
    if not survivors or any(not rows for _, rows in survivors):
        return resolved
    cases = {r["case_id"]: r for _, rows in survivors for r in rows}
    if len(cases) == 1:
        return ResolvedAlias(None, list(cases.values()))
    holding = [c for c in cases.values() if normalized_quote in normalize_text_for_match(c["text"])]
    return VerifyResult.VERIFIED if len(holding) == 1 else resolved


def verify(
    conn: sqlite3.Connection, citation_text: str, quote_or_proposition: str, claimed_name: str | None = None
) -> VerifyResult:
    citations = parse_citations(citation_text)
    if len(citations) != 1:
        return VerifyResult.MALFORMED

    key = _citation_key(citations[0])
    if key is None:
        return VerifyResult.NOT_IN_INDEX

    # The party names the caller typed in front of the citation ("A v. B, (1977) 3 SCC 247") are a claimed name too.
    claimed_name = claimed_name or _name_before_citation(citation_text, citations[0])
    resolved = resolve_citation_key(conn, key, claimed_name, citations[0].year)
    if resolved.status is VerifyResult.CONFLICT:
        narrowed = _narrow_conflict(
            conn, key, resolved, claimed_name, citations[0].year, normalize_text_for_match(quote_or_proposition)
        )
        if isinstance(narrowed, VerifyResult):
            return narrowed
        resolved = narrowed
    if resolved.status is not None:
        return resolved.status

    normalized_quote = normalize_text_for_match(quote_or_proposition)
    if not normalized_quote:
        # the empty string is "in" every text: an empty or whitespace-only quote verifies nothing
        return VerifyResult.EXISTS_QUOTE_NOT_FOUND
    # The party lookup matches titles by each party's first token, so it can return a DIFFERENT case that shares those
    # tokens (a later case between similar parties). The quote must be checked only against a case the citation's own
    # year allows: the year in "(1977) 3 SCC 247" is the reporting year, so a decision within a year of it.
    # A candidate set with no such case is "no evidence either way", never a verification against some other case.
    case_rows = _rows_in_cited_year(conn, resolved.case_rows, citations[0].year)
    # #723 (#717): and only against a candidate whose title carries every distinctive word of the citation's parties.
    case_rows = [r for r in case_rows if _title_carries_the_cited_parties(conn, key, r["title"])]
    if not case_rows:
        return VerifyResult.NOT_IN_INDEX
    for row in case_rows:
        normalized_text = normalize_text_for_match(row["text"])
        if normalized_quote in normalized_text:
            return VerifyResult.VERIFIED
    return VerifyResult.EXISTS_QUOTE_NOT_FOUND
