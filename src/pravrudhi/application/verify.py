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

import json
import os
import re
import sqlite3
import threading
import unicodedata
from collections import Counter
from dataclasses import dataclass
from difflib import SequenceMatcher
from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Any, NamedTuple

from pravrudhi.application.citations import Citation, parse_citations, supp_key

# Leading words that can precede a real party name in running prose ("This Court in X v. Y...", "In X v.
# Y..."), stripped repeatedly from the front before comparing two mentions of "the same" party. Found by
# hand measuring resolution precision on 100 real citations (LEG-PLAN P3): mine_aliases's own regex (a run
# of Title-Case tokens) cannot tell "In" the filler word from "In" a genuine name, since both are
# Title-Case; this list is the disambiguation `citations.py`'s stricter regex didn't fully cover either.
_LEADING_FILLER = re.compile(r"^(?:this|that|in|the|court|held|observed|noted|see)\s+", re.IGNORECASE)
# Trailing honorific/plural variants that name the same party ("Anr." vs "Ors." vs the bare name) --
# stripped for comparison only, never for the stored/displayed party name.
# `(?<!\s)` starts the match only at the beginning of a whitespace run (the leftmost match always did), and the optional
# "and" owns its own trailing gap, so no two `\s` quantifiers are adjacent: a long run of spaces no longer backtracks (#476).
_TRAILING_HONORIFIC = re.compile(r"(?<!\s)\s+(?:(?:and|&)\s*)?(?:anr\.?|ors\.?|others?|etc\.?)\s*$", re.IGNORECASE)

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


def _fuzzy_match(target: str, title: str) -> bool:
    """`SequenceMatcher(target, normalised title).ratio() >= _FUZZY_THRESHOLD`, with difflib's two cheap upper bounds
    (`real_quick_ratio`, `quick_ratio`) tried first: ratio() can never exceed either, so a title that fails a bound
    cannot reach the floor and the answer is exactly the plain ratio test, found without the expensive match search for
    nearly every title of a 38,000-title scan."""
    matcher = SequenceMatcher(None, target, normalize_party_name(title))
    return (
        matcher.real_quick_ratio() >= _FUZZY_THRESHOLD
        and matcher.quick_ratio() >= _FUZZY_THRESHOLD
        and matcher.ratio() >= _FUZZY_THRESHOLD
    )


#: One index file's titles, normalised once: (case_id, normalised title, its length, its character counts). The fuzzy scan
#: compares
#: a target with EVERY title, once per alias group of a citation, so a citation with many groups normalised the same 38,657
#: titles (and re-read them from the file) a dozen times per lookup; this reads and normalises them once per process.
_TITLE_ROWS: dict[tuple[Any, ...], tuple[tuple[str, str, int, Counter[str]], ...]] = {}
#: Held while the cache is built, so concurrent first lookups (and the start-up warm-up) build it ONCE; none sees a partial copy.
_TITLE_LOCK = threading.Lock()
#: How long a lookup waits for another thread's cache build before it scans the table itself (the plain per-title scan, which
#: obeys the lookup's own deadline): the build takes well under a second, so a longer wait means a starved host, and no lookup
#: may sit on the lock past its 5 s budget.
_TITLE_LOCK_WAIT_S = 1.0


def _title_rows(conn: sqlite3.Connection) -> tuple[tuple[str, str, int, Counter[str]], ...] | None:
    """The cached normalised titles of the index file behind `conn`, or None where the scan reads the table as before: a database
    with no file (in memory), or another thread's build that outlasts `_TITLE_LOCK_WAIT_S`. The key names the file's identity, its
    header change counter and the stat of a `-wal` file, so a rewritten file is never served from an older copy. Index files are
    REPLACED, never updated in place (the shipped index is read-only in the image); a swap by rename, a rewrite and a WAL commit
    all move the key. One entry only: two index files alternating in one process would rebuild the cache each time."""
    try:
        row = conn.execute("PRAGMA database_list").fetchone()
        file = row[2] if row else ""
        if not file:
            return None
        st = os.stat(file)
        with open(file, "rb") as f:
            header = f.read(100)
    except (OSError, sqlite3.Error):
        return None
    try:
        wal = os.stat(file + "-wal")
        wal_id = (wal.st_mtime_ns, wal.st_size)
    except OSError:
        wal_id = None
    key = (file, st.st_ino, st.st_mtime_ns, st.st_size, header[24:28], wal_id)
    rows = _TITLE_ROWS.get(key)
    if rows is None:
        if not _TITLE_LOCK.acquire(timeout=_TITLE_LOCK_WAIT_S):
            return None  # another thread's build is taking too long: scan the table directly, inside this lookup's own deadline
        try:
            rows = _TITLE_ROWS.get(key)  # another thread may have built it while this one waited
            if rows is None:
                titles = [(r[0], normalize_party_name(r[1] or "")) for r in conn.execute("SELECT case_id, title FROM cases")]
                rows = tuple((case_id, t, len(t), Counter(t)) for case_id, t in titles)
                _TITLE_ROWS.clear()  # one index file per process; a replaced file drops the old copy
                _TITLE_ROWS[key] = rows
        finally:
            _TITLE_LOCK.release()
    return rows


def warm_title_cache(index_path: str | Path) -> int:
    """Load the title cache of the index at `index_path` (engine start): returns the number of titles, 0 when unavailable."""
    try:
        conn = sqlite3.connect(f"file:{Path(index_path).resolve()}?mode=ro", uri=True)
    except sqlite3.Error:
        return 0
    try:
        with open(Path(index_path), "rb") as f:  # the whole file into the OS page cache (11 MB for the shipped slim index)
            while f.read(1 << 20):
                pass
        rows = _title_rows(conn)
        # One full lookup of the citation with the most alias groups: the first real call on this worker then finds the
        # code paths, regex caches and the index's pages already used (#478).
        try:
            heavy = conn.execute(
                "SELECT citation FROM citation_aliases GROUP BY citation "
                "ORDER BY COUNT(DISTINCT party_1 || '|' || party_2) DESC LIMIT 1"
            ).fetchone()
            if heavy is not None:
                exists_in_index(conn, heavy[0])
        except sqlite3.Error:
            pass  # an index without alias rows has no heavy lookup to rehearse; the title cache is loaded either way
        return len(rows) if rows else 0
    except (OSError, sqlite3.Error):
        return 0
    finally:
        conn.close()


def _fuzzy_scan(target: str, rows: tuple[tuple[str, str, int, Counter[str]], ...]) -> list[str]:
    """The case ids whose normalised title matches `target` at `_FUZZY_THRESHOLD`: `_fuzzy_match` over the cached titles, with
    difflib's two upper bounds computed from the cached length and character counts (the same formulas as
    `real_quick_ratio` and `quick_ratio`), so a SequenceMatcher is built only for a title that passed both."""
    la = len(target)
    ca = Counter(target)
    out: list[str] = []
    for case_id, title, lb, cb in rows:
        total = la + lb
        if total and 2.0 * min(la, lb) / total < _FUZZY_THRESHOLD:
            continue
        if total and 2.0 * sum((ca & cb).values()) / total < _FUZZY_THRESHOLD:
            continue
        if SequenceMatcher(None, target, title).ratio() >= _FUZZY_THRESHOLD:
            out.append(case_id)
    return out


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
    rows = _title_rows(conn)
    if rows is not None:
        confirmed_ids = _fuzzy_scan(target, rows)
    else:
        confirmed_ids = [
            row["case_id"] for row in conn.execute("SELECT case_id, title FROM cases") if _fuzzy_match(target, row["title"])
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
    #: Existence only (#832 E1): the citation resolves to an indexed case and NO quote was checked.
    #: Returned only by `exists_in_index`, never by `verify`.
    IN_INDEX = "IN_INDEX"
    #: #509 (existence only): the citation resolves to ONE indexed case, the caller typed a case name in front of it, and that name does
    #: not match the case. Returned only by `exists_in_index`, never by `verify`.
    NAME_MISMATCH = "NAME_MISMATCH"


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
        # ONE batched read of the years (an id with no row is absent: no year), not one full-table select per candidate (#472)
        years = _recorded_years(conn, list(cases))
        near = {}
        for cid, r in cases.items():
            y = years.get(cid)
            try:
                if y not in (None, "") and abs(int(y) - claimed_year) <= 1:
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


def _fold_abbreviations(tokens: frozenset[str]) -> frozenset[str]:
    """The words of a party name with a printed abbreviation folded to its long form ("Corp" and "Corpn" to "Corporation"), for the
    containment test only: a title that prints "Delhi Transport Corp" carries the alias word "Corporation"."""
    return frozenset(_ABBREVIATIONS.get(t, t) for t in tokens)


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
    years = _recorded_years(conn, [row["case_id"] for row in rows])
    ok: list[sqlite3.Row] = []
    for row in rows:
        try:
            year = int(years[row["case_id"]]) if row["case_id"] in years else None
        except (TypeError, ValueError):
            year = None
        if year is not None and abs(year - cited_year) <= 1:
            ok.append(row)
    return ok


#: Case ids per IN list: below SQLITE_MAX_VARIABLE_NUMBER (999 on older builds); a huge alias group has over 1,500 candidates.
_YEAR_LOOKUP_CHUNK = 500


def _recorded_years(conn: sqlite3.Connection, case_ids: list[str]) -> dict[str, Any]:
    """The recorded `year` of each case id, EXACTLY what the old per-case `SELECT year ... WHERE case_id = ?` + `fetchone()`
    returned: the year of the FIRST row in scan order (lowest rowid) when a case id has two rows (1,307 do in the 7 Oct 2026
    index, all with a NULL year); an id with no row is absent from the result (the caller reads that as "no year"). `cases` is
    an FTS5 table whose `case_id` is not indexed, so each per-case lookup scanned the whole table (about 0.07 s; 1,517
    candidates took 110 s, #380): one query per chunk of ids replaces one scan per case. A derived `case_year(case_id PRIMARY
    KEY, year)` table, when the index carries a CURRENT one (built by speed380/build_case_year.py from the lowest-rowid rows),
    is read by key instead, and an id it does not hold is still read from `cases`."""
    wanted = list(dict.fromkeys(case_ids))
    out: dict[str, Any] = {}
    if _derived_year_table_is_current(conn):
        _fill_years(conn, "case_year", "", wanted, out)
        wanted = [i for i in wanted if i not in out]  # an id the table lacks is read from `cases`, never assumed absent
    _fill_years(conn, "cases", " ORDER BY rowid", wanted, out)
    return out


def _derived_year_table_is_current(conn: sqlite3.Connection) -> bool:
    """Whether the index carries a `case_year` table built from THIS `cases` content: `case_year_meta.cases_max_rowid` (written
    when the table is built) must equal the current `max(rowid)` of `cases`. A copy that went stale (rows added after the
    build) is ignored, so the old `cases` read answers instead; a table can never silently change an answer by being out of
    date (#380)."""
    try:
        built = conn.execute("SELECT cases_max_rowid FROM case_year_meta").fetchone()
    except sqlite3.OperationalError as e:
        if "no such table" not in str(e):
            raise
        return False
    if built is None:
        return False
    now = conn.execute("SELECT max(rowid) FROM cases").fetchone()
    return now is not None and now[0] == built[0]


def _fill_years(conn: sqlite3.Connection, table: str, order: str, wanted: list[str], out: dict[str, Any]) -> None:
    for i in range(0, len(wanted), _YEAR_LOOKUP_CHUNK):
        chunk = wanted[i : i + _YEAR_LOOKUP_CHUNK]
        marks = ",".join("?" * len(chunk))
        for case_id, year in conn.execute(f"SELECT case_id, year FROM {table} WHERE case_id IN ({marks}){order}", chunk):
            out.setdefault(case_id, year)  # first row in rowid order wins, as fetchone() did


# `(?<!\s)`: tried from the start of a whitespace run only (the leftmost match always started there): a long run costs one pass.
_PARTY_SEPARATOR = re.compile(r"(?<!\s)\s+(?:v\.?|vs\.?|versus)\s+", re.IGNORECASE)


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
    if claimed_name and not claimed:
        # A name WAS typed but has no distinctive token (only initials, one- or two-letter pieces, common words), so it
        # cannot tell the groups apart. It must not fall through as "no name given" (that verifies on a quote found in one
        # candidate): the CONFLICT stays (#379). A bare citation (no claimed name) keeps the #373 behaviour.
        return resolved
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


#: #717: the most distinctive title words, beyond the cited parties' own words, that an existence-only IN_INDEX tolerates.
#: 0 keeps only titles that carry no extra word; the value was set from the measured distribution on the dev index
#: (see CHANGELOG).
EXISTENCE_MAX_EXTRA_TITLE_TOKENS = 0


_NONDISTINCTIVE_FILE = Path(__file__).resolve().parents[1] / "assets" / "citation" / "nondistinctive_tokens.json"


@lru_cache(maxsize=1)
def _nondistinctive_tokens() -> frozenset[str]:
    """The words an existence-only answer does not count as extra title words (config:
    assets/citation/nondistinctive_tokens.json). A missing or
    unreadable file, or one that is not a list of strings, is the EMPTY set: the strict rule, never a looser one."""
    try:
        raw = json.loads(_NONDISTINCTIVE_FILE.read_text())["tokens"]
        if not isinstance(raw, list) or not all(isinstance(t, str) for t in raw):
            return frozenset()
    except (OSError, ValueError, KeyError, TypeError):
        return frozenset()
    return frozenset(t for w in raw for t in _distinctive_tokens(w))


def _extra_title_tokens(conn: sqlite3.Connection, key: str, title: str) -> int:
    """How many distinctive words the title carries beyond the words of the citation key's alias group that fits it best
    (the group with the fewest extra words). No alias group = 0 (nothing to compare)."""
    tokens = _distinctive_tokens(title)
    groups = list(conn.execute("SELECT DISTINCT party_1, party_2 FROM citation_aliases WHERE citation = ?", (key,)))
    if not groups:
        return 0
    free = _nondistinctive_tokens()
    return min(len(tokens - (_distinctive_tokens(p1) | _distinctive_tokens(p2)) - free) for p1, p2 in groups)


#: A word is a SPELLING of another only for an OCR- or transliteration-style slip: the words are long enough to be a rare name (a
#: short common surname such as Kumar/Kumari, Sharma/Sharmila or Lal/Lall is a different person's name, never a spelling) and they
#: differ by exactly ONE inserted, dropped or changed letter, or by two neighbouring letters swapped. The first letter must agree.
_SPELLING_MIN_LETTERS = 7
#: Long words that are titles or kinds of body, not a person's or firm's own name: a shortened form that keeps only one of them is
#: not a rare name, whatever its length.
_GENERIC_LONG_WORDS = frozenset(
    {
        "governor",
        "corporation",
        "municipal",
        "authority",
        "director",
        "secretary",
        "chairman",
        "tribunal",
        "manager",
        "officer",
        "society",
        "committee",
        "industries",
        "university",
        "registrar",
        "magistrate",
        "collector",
        "superintendent",
        "district",
        "department",
        "ministry",
        "chief",
        "minister",
        "inspector",
        "regional",
        "national",
        "central",
        "railway",
        "railways",
        "corporate",
        "electricity",
        "board",
        "council",
        "association",
        "institute",
        "college",
        "hospital",
        "bank",
        "insurance",
    }
)


def _one_slip(a: str, b: str) -> bool:
    """Exactly one inserted, dropped or changed letter, or one pair of neighbouring letters swapped, and the same first letter."""
    if a == b or abs(len(a) - len(b)) > 1 or a[0] != b[0]:
        return False
    p = 0
    while p < min(len(a), len(b)) and a[p] == b[p]:
        p += 1
    if len(a) == len(b):
        if a[p + 1 :] == b[p + 1 :]:
            return True  # one letter changed
        return a[p] == b[p + 1] and a[p + 1] == b[p] and a[p + 2 :] == b[p + 2 :]  # two neighbours swapped
    longer, shorter = (a, b) if len(a) > len(b) else (b, a)
    return longer[p + 1 :] == shorter[p:]  # one letter dropped or inserted


#: Abbreviations a party name is printed with, folded to the long form for the spelling comparison only.
_ABBREVIATIONS = {"corpn": "corporation", "corp": "corporation"}


def _same_word(a: str, b: str) -> bool:
    a, b = _ABBREVIATIONS.get(a, a), _ABBREVIATIONS.get(b, b)
    if a == b:
        return True
    return min(len(a), len(b)) >= _SPELLING_MIN_LETTERS - 1 and max(len(a), len(b)) >= _SPELLING_MIN_LETTERS and _one_slip(a, b)


def _tokens_correspond(x: frozenset[str], y: frozenset[str], *, allow_shortened: bool = True) -> bool:
    """Whether two sets of distinctive words name the same party: every word of each has an equal or OCR-slip word in the
    other; or the smaller set is inside the larger one and carries a rare name (a word of at least `_SPELLING_MIN_LETTERS`
    letters), so a shortened form of a rare name ("Marlowenton Thackeray" for "Marlowenton Thackeray Sripadagalvaru")
    corresponds, but a short form that only drops words from common names ("Kumar" for "Rajesh Kumar Gupta") never does.
    `allow_shortened=False` asks for the first reading only. Empty sets correspond to nothing."""
    if not x or not y:
        return False
    x_in_y = all(any(_same_word(a, b) for b in y) for a in x)
    y_in_x = all(any(_same_word(b, a) for a in x) for b in y)
    if x_in_y and y_in_x:
        return True
    if not allow_shortened:
        return False
    smaller, covered = (x, x_in_y) if len(x) <= len(y) else (y, y_in_x)
    return covered and any(len(w) >= _SPELLING_MIN_LETTERS and w not in _GENERIC_LONG_WORDS for w in smaller)


#: Words a printed party name may carry in front of the name itself ("His Holiness ...", "Shri ...", "Vide ..."): ignored when two
#: spellings of a party are compared.
_NAME_PREFIX_WORDS = frozenset({"his", "holiness", "shri", "sri", "smt", "vide", "justice", "hon", "ble"})


def _party_is_spelling_of(unresolved: str, resolved: str) -> bool:
    """Whether the distinctive words of `unresolved` are a spelling of those of `resolved` (see `_tokens_correspond`)."""
    return _tokens_correspond(
        _distinctive_tokens(unresolved) - _NAME_PREFIX_WORDS, _distinctive_tokens(resolved) - _NAME_PREFIX_WORDS
    )


def _title_is_the_group(title: str, group_row: sqlite3.Row) -> bool:
    """Whether the case title names THIS alias group's parties (and no others): the group's and the title's words correspond.
    A case a group only reaches through the fuzzy or first-word lookup does not count as the group's case."""
    group = _distinctive_tokens(group_row["party_1"]) | _distinctive_tokens(group_row["party_2"])
    return _tokens_correspond(group, _distinctive_tokens(title), allow_shortened=False)


def _collapse_alias_conflict(
    conn: sqlite3.Connection, key: str, resolved: ResolvedAlias, year: int | None
) -> ResolvedAlias | None:
    """Existence only: a key whose alias groups are SPELLINGS of one case ("Aldric Vennoway", "In Aldric Vennoway" /
    "State of Zorbland", "State of Zorbland and Another") is not a conflict. Each group is resolved and held to the
    year window and the title-carries-the-parties test exactly as `_narrow_conflict` does. A group that has a
    checkable case titled with its OWN parties survives. Any other group (it resolves to no case, only to cases that
    fail the tests, or only to a case that is not titled with its own parties because the fuzzy lookup reached it) is
    an uncheckable candidate (#331) that folds in ONLY when both its parties are spellings of a surviving group's.
    When the surviving groups' cases are exactly ONE case, that case is the resolution; anything else returns None
    (CONFLICT stays). Never guesses between two different cases."""
    survivors: list[tuple[sqlite3.Row, tuple[sqlite3.Row, ...]]] = []
    unresolved: list[sqlite3.Row] = []
    for group_row, rows in resolved.group_candidates:
        if not rows:
            unresolved.append(group_row)
            continue
        checkable = tuple(
            r for r in _rows_in_cited_year(conn, list(rows), year) if _title_carries_the_cited_parties(conn, key, r["title"])
        )
        if not checkable:
            unresolved.append(group_row)  # reached other cases only: as uncheckable as a group with none
            continue
        own = tuple(r for r in checkable if _title_is_the_group(r["title"], group_row))
        if own:
            survivors.append((group_row, own))
        else:
            unresolved.append(group_row)
    if not survivors:
        return None
    for u in unresolved:
        if not any(
            _party_is_spelling_of(u["party_1"], g["party_1"]) and _party_is_spelling_of(u["party_2"], g["party_2"])
            for g, _ in survivors
        ):
            return None
    cases = {r["case_id"]: r for _, rows in survivors for r in rows}
    return ResolvedAlias(None, list(cases.values())) if len(cases) == 1 else None


def _supp_answer(conn: sqlite3.Connection, key: str) -> tuple[VerifyResult, list[str]]:
    """An SCC supplementary-volume citation resolves ONLY to the record(s) whose own header prints it (the
    `case_citations` table of an own-supp index): no party matching, no year window, never a same-party neighbour. No
    such table, or no row for the key, is NOT_IN_INDEX (the form is read but not held). One indexed case, or several
    records of one judgment (variant titles that share a distinctive word), is IN_INDEX; records whose titles share
    none are CONFLICT (nothing here guesses between them)."""
    try:
        ids = [r[0] for r in conn.execute("SELECT case_id FROM case_citations WHERE citation = ?", (key,))]
    except sqlite3.OperationalError as e:
        if "no such table" not in str(e).lower():
            raise
        return VerifyResult.NOT_IN_INDEX, []
    if not ids:
        return VerifyResult.NOT_IN_INDEX, []
    marks = ",".join("?" * len(ids))
    titles = {r[0]: r[1] for r in conn.execute(f"SELECT case_id, title FROM cases WHERE case_id IN ({marks})", ids)}
    if not titles:
        return VerifyResult.NOT_IN_INDEX, []  # a citation row whose case is not in this index
    # One judgment indexed under several records prints variant titles ("&" and "And", "Admn" for "Administration", a
    # truncated title, a respelt party): the records are ONE judgment when their titles are connected by a shared
    # distinctive word. Titles that share no word are different judgments printing one key (a volume-less supplementary
    # form can): nothing guesses between them.
    groups: list[set[str]] = []
    for words in (set(_distinctive_tokens(t)) for t in titles.values()):
        linked = [g for g in groups if g & words]
        merged = set(words).union(*linked) if linked else set(words)
        groups = [g for g in groups if g not in linked] + [merged]
    return (VerifyResult.IN_INDEX if len(groups) == 1 else VerifyResult.CONFLICT), list(titles.values())


class ExistenceAnswer(NamedTuple):
    """What `exists_in_index_detail` answers: the result, and whether a typed case name was COMPARED with the index to reach it (true only for
    an IN_INDEX that matched the name and for NAME_MISMATCH; false for every other answer, including a bare citation)."""

    result: VerifyResult
    name_compared: bool = False


#: #509: words in front of a typed case name that are not part of it ("Per Maneka Gandhi v. ...", "As held in ...", "Vide ...").
_TYPED_SIGNAL_WORDS = re.compile(
    r"^(?:(?:as\s+)?(?:per|vide|cf\.?|see|also|held\s+in|observed\s+in|noted\s+in|decided\s+in|in|the\s+case\s+of|case\s+of|following|"
    r"relying\s+on|reported\s+in|ref\.?)\b[\s,:;-]*)+",
    re.IGNORECASE,
)
#: Words that are a title or an honorific, not the name ("His Holiness ...", "Late ...", "Hon'ble Justice ..."), and the initialisms of a
#: party that is itself only common words (UOI is Union of India): ignored when a typed name is compared.
_TYPED_NOISE_WORDS = frozenset({"his", "her", "holiness", "late", "hon", "ble", "honble", "justice", "prof", "professor", "mrs", "miss", "uoi"})
#: Spellings of one given name that print differently on a title and in a citation.
_TYPED_NAME_FOLDS = {m: "mohammad" for m in ("mohd", "mohammed", "mohammad", "muhammad", "mohamed", "mohamad", "muhamed")}


#: Surnames and given names so common that sharing one tells nothing ("Singh" is in thousands of titles): a typed name never agrees with a
#: title on these alone. A side made only of such words must match all of them.
_COMMON_NAME_WORDS = frozenset(
    {
        "singh", "kumar", "kumari", "devi", "sharma", "verma", "lal", "ram", "das", "prasad", "khan", "begum", "bai", "rao",
        "reddy", "gupta", "jain", "shah", "patel", "ali", "mohammad", "chand", "nath", "raj", "lall",
    }
)


def _slip(a: str, b: str) -> bool:
    """A spelling variant that may avoid a MISMATCH but is never a match: a one-letter slip in a word of six or more letters (the same
    first letter), a doubled-vowel variant (Kumaar/Kumar, Laalit/Lalit), or the shared rules of `_same_word`."""
    return _same_word(a, b) or (min(len(a), len(b)) >= 6 and _one_slip(a, b)) or (a != b and _collapse_vowels(a) == _collapse_vowels(b))


def _side_agrees(side: frozenset[str], title_words: frozenset[str], initials: frozenset[str] = frozenset()) -> bool:
    """One typed party against a title: at least half of its words are in the title, and (when it has a word that is not a very common
    name) at least one such word; a side made only of very common name words needs all of them."""
    matched = {w for w in side if any(_slip(w, other) for other in title_words)}
    if len(matched) * 2 < len(side):
        return False
    evidence = side - initials  # a run of initials (L H V) matching is not evidence by itself
    if not evidence:
        return False  # a party made only of initials is never agreement
    rare = evidence - _COMMON_NAME_WORDS
    return bool(matched & rare) if rare else matched >= evidence


def _split_parties(name: str) -> list[str]:
    """The two parties of a typed case name. An initial V ("G V Rao v. L H V Prasad", "G. V. Rao v. L. H. V. Prasad") looks like the
    separator, so the candidates are tried in tiers: a lower-case "v", "v.", "vs", "vs." or "versus"; then any candidate that ends with
    a full stop; then any. Within a tier the candidate nearest the middle of the name wins. One piece when there is no separator."""
    found = list(_PARTY_SEPARATOR.finditer(name))
    if not found:
        return [name]
    token = lambda m: m.group(0).strip()  # noqa: E731
    tiers = (
        [m for m in found if token(m) in ("v", "v.", "vs", "vs.", "versus")],
        [m for m in found if token(m).endswith(".")],
        found,
    )
    pool = next(t for t in tiers if t)
    mid = len(name) / 2
    m = min(pool, key=lambda x: abs((x.start() + x.end()) / 2 - mid))
    return [name[: m.start()], name[m.end() :]]


def _unglue_dots(side: str) -> str:
    """"G.V.Rao" is G V Rao: a full stop followed by a letter starts a new word (so the initials do not fuse with the name)."""
    return re.sub(r"\.(?=[A-Za-z])", ". ", side)


def _initialisms(side: str) -> frozenset[str]:
    """The merged runs of three or more single-letter initials in one typed party ("L H V" and "L.H.V." both give "lhv"): they are
    kept as words, but a run of initials alone is never evidence that two names are the same."""
    return frozenset(
        re.sub(r"[^a-z]", "", run.lower()) for run in re.findall(r"(?<![A-Za-z])(?:[A-Za-z](?![A-Za-z])\.?\s*){3,}", _unglue_dots(side)) if run.strip()
    )


def _fold_word(w: str) -> str:
    """One word folded for the typed-name comparison: Mohammad spellings and printed abbreviations (the approved folds: a word equal after
    them is the same word). Doubled-vowel spellings (Kumaar, Laalit) are NOT folded here: they only avoid a mismatch (see `_slip`)."""
    return _TYPED_NAME_FOLDS.get(w, _ABBREVIATIONS.get(w, w))


def _collapse_vowels(w: str) -> str:
    return re.sub(r"([aeiou])\1+", r"\1", w)


def _typed_words(side: str) -> frozenset[str]:
    """The distinctive words of ONE side of a typed case name, for the comparison only: leading signal words and honorifics dropped,
    the initialism UOI dropped, Mohd/Mohammed/Muhammad folded to one word, printed abbreviations folded ("Corp" to "Corporation")."""
    side = _TYPED_SIGNAL_WORDS.sub("", " ".join(_unglue_dots(side).split()))
    words = _distinctive_tokens(side) - _TYPED_NOISE_WORDS
    return frozenset(_fold_word(w) for w in words)


def _title_words(title: str) -> frozenset[str]:
    return frozenset(_fold_word(w) for w in _distinctive_tokens(title))


def _word_in(word: str, words: frozenset[str]) -> bool:
    return any(_slip(word, other) for other in words)


def _typed_letters(text: str) -> list[str]:
    """The single-letter initials of a typed name or a title, lower-case ("G. V. Rao" gives g, v)."""
    return [t.lower() for t in re.findall(r"(?<![A-Za-z])[A-Za-z](?![A-Za-z])", _unglue_dots(text))]


#: #509 name states. STRONG: every typed party's own words are all in the case title (equal after the folds below, no spelling slip),
#: so the engine really compared the name. MISMATCH: a typed party clearly disagrees with the title. MIDDLE: neither (half the words
#: match, only a spelling slip matches, a party made only of initials, extra rare words typed beside the case's own words): the answer is
#: IN_INDEX WITHOUT `name_compared`, so no caller is told a comparison was made that was not.
NAME_STRONG, NAME_MIDDLE, NAME_MISMATCH_STATE = "strong", "middle", "mismatch"


_ACRONYM_SKIP = frozenset({"of", "and", "the", "&"})


def _typed_acronym(piece: str, words: frozenset[str]) -> str | None:
    """The letters of a typed party written as an ACRONYM: a single distinctive word that is printed in capitals, 2 to 6 letters, with no
    full stops ("ONGC", "LIC of India", "DTC"). None otherwise."""
    if len(words) != 1:
        return None
    (word,) = words
    raw = [t for t in re.findall(r"(?<![A-Za-z.])[A-Z]{2,6}(?![A-Za-z.])", _TYPED_SIGNAL_WORDS.sub("", " ".join(piece.split())))]
    return word if any(t.lower() == word for t in raw) else None


_ACRONYM_TRAILING = frozenset({"ors", "anr", "etc", "others", "another", "co", "company", "ltd", "limited", "pvt", "private", "india"})


def _initials_full(part: str) -> frozenset[str]:
    """The FULL initials of a title party, skipping of/and/the: with every word ("Bharat Sanchar Nigam Ltd" gives bsnl) and without the
    trailing generic words (Ors, Anr, Etc, Others, Co, Ltd, Limited, Pvt, Private, India: "Oil And Natural Gas Corporation Ltd" also gives
    ongc, "Life Insurance Corporation Of India" also gives lic). A PREFIX of the initials is not among them: BSE is not "Bihar School
    Examination Board" (bseb)."""
    words = [w for w in re.sub(r"[^a-z0-9& ]+", " ", part.lower()).split() if w not in _ACRONYM_SKIP]
    out = {"".join(w[0] for w in words)} if words else set()
    while words and words[-1] in _ACRONYM_TRAILING:
        words = words[:-1]
        if words:
            out.add("".join(w[0] for w in words))
    return frozenset(out)


def _generic_text(text: str) -> str:
    """A party made only of common words ("Union of India", "The State"), as one comparable string: UOI is Union of India."""
    t = normalize_party_name(_TYPED_SIGNAL_WORDS.sub("", " ".join(_unglue_dots(text).split())))
    return " ".join(("union of india" if w == "uoi" else w) for w in t.split() if w != "the")


def _typed_sig(text: str) -> tuple[str, frozenset[str] | str]:
    """What identifies one typed party for a STRONG comparison: its distinctive words, or (a party with none) its whole text."""
    words = _typed_words(text)
    return ("w", words) if words else ("g", _generic_text(text))


def _sig_key(sig: tuple[str, frozenset[str] | str]) -> tuple[str, str]:
    return (sig[0], " ".join(sorted(sig[1])) if isinstance(sig[1], frozenset) else sig[1])


def _title_sig(text: str) -> tuple[str, frozenset[str] | str]:
    words = _title_words(text)
    return ("w", words) if words else ("g", _generic_text(text))


def _name_state_for_title(
    sides: list[frozenset[str]],
    initials: list[frozenset[str]],
    typed_letters: list[str],
    pieces: list[str],
    title: str,
    acronyms: list[str | None],
) -> str:
    tw = _title_words(title)
    evidence = [side - ini for side, ini in zip(sides, initials, strict=True)]
    clear = False
    for side, ev, acro in zip(sides, evidence, acronyms, strict=True):
        if acro is not None:
            continue  # an acronym ("ONGC") is never a disagreement by itself: it is read against the title's initials below
        if ev and not _side_agrees(side, tw, frozenset(side - ev)):
            clear = True  # a typed party with evidence words clearly disagrees with the title
    typed_all = frozenset().union(*sides)
    covers_title = len(tw) >= 2 and all(_word_in(w, typed_all) for w in tw)
    if clear:
        return NAME_MIDDLE if covers_title else NAME_MISMATCH_STATE  # the case's own words are all there, with more typed beside them
    # STRONG only when EVERY typed party (a party made only of common words too: "Union of India" must equal the title's own) equals one
    # party of the title, word for word after the folds (Corp, Mohd, doubled vowels, UOI, honorifics, signal words), and the typed initials
    # are initials the title has. A half match, a spelling slip, a very common name alone, initials alone or leftover words stay MIDDLE.
    title_parts = [part for part in _split_parties(title) if part.strip()]
    pool = [_title_sig(part) for part in title_parts]
    equal = len(pool) >= 2 and len(pool) == len([p for p in pieces if p.strip()])
    acro_by_piece = {piece: acro for piece, acro in zip((p for p in pieces if p.strip() and _typed_words(p)), acronyms, strict=False)}
    if equal:
        remaining = list(title_parts)
        for piece in (p for p in pieces if p.strip()):
            acro = acro_by_piece.get(piece)
            sig = _typed_sig(piece)
            hit = next((i for i, part in enumerate(remaining) if (acro is not None and acro in _initials_full(part)) or _title_sig(part) == sig), None)
            if hit is None:
                equal = False
                break
            remaining.pop(hit)
    title_letters = [c for part in title_parts for c in _typed_letters(part)]
    letters_ok = not title_letters or not typed_letters or all(typed_letters.count(c) <= title_letters.count(c) for c in set(typed_letters))
    return NAME_STRONG if equal and letters_ok else NAME_MIDDLE


def _name_state(conn: sqlite3.Connection, key: str, titles: list[str], claimed_name: str) -> str | None:
    """How a typed case name relates to the resolved case (any of its record titles): STRONG, MIDDLE or MISMATCH (see above), the best
    over the record titles; or an alias group of the key that agrees on both parties (each with a distinctive word) is STRONG. The two
    typed parties are read apart. None when the typed name has no distinctive word (nothing to compare)."""
    pieces = _split_parties(claimed_name)
    sides = [_typed_words(side) for side in pieces]
    initials = [frozenset(_fold_word(i) for i in _initialisms(side)) for side in pieces]
    keep = [(w, i) for w, i in zip(sides, initials, strict=True) if w]
    if not keep:
        return None
    side_words = [w for w, _ in keep]
    side_inits = [i for _, i in keep]
    typed_letters = [c for piece in pieces for c in _typed_letters(piece)]
    acronyms = [_typed_acronym(piece, w) for piece, (w, _) in zip((p for p in pieces if _typed_words(p)), keep, strict=True)]
    states = [_name_state_for_title(side_words, side_inits, typed_letters, pieces, title, acronyms) for title in titles]
    for want in (NAME_STRONG, NAME_MIDDLE):
        if want in states:
            return want
    typed_sigs = [_typed_sig(piece) for piece in pieces if piece.strip()]
    groups = conn.execute("SELECT DISTINCT party_1, party_2 FROM citation_aliases WHERE citation = ?", (key,)).fetchall()
    for g in groups:  # the typed name equals an alias group of the key (the index's own spelling of the parties)
        group_sigs = [_title_sig(g["party_1"]), _title_sig(g["party_2"])]
        if len(typed_sigs) == 2 and sorted(map(_sig_key, typed_sigs)) == sorted(map(_sig_key, group_sigs)):
            return NAME_STRONG
    return NAME_MISMATCH_STATE


def exists_in_index(conn: sqlite3.Connection, citation_text: str, claimed_name: str | None = None) -> VerifyResult:
    """#832 E1: does the citation resolve to an indexed case? Existence only: no quote is checked, so never VERIFIED. See
    `exists_in_index_detail` for the answer with the comparison flag."""
    return exists_in_index_detail(conn, citation_text, claimed_name).result


def exists_in_index_detail(conn: sqlite3.Connection, citation_text: str, claimed_name: str | None = None) -> ExistenceAnswer:
    """Same resolution, year window and party-title test as `verify` (a case `verify` would not check never counts as found); a key
    shared by two alias groups that are NOT spellings of one case stays CONFLICT (spellings of one case collapse first).
    #509: when the answer is IN_INDEX and a case name was typed in front of the citation, the name is compared with the ONE case the
    citation resolved to (after any collapse); a name that does not match answers NAME_MISMATCH. No name, or a name with no distinctive
    word, answers exactly as before (name_compared false)."""
    citations = parse_citations(citation_text)
    if len(citations) != 1:
        return ExistenceAnswer(VerifyResult.MALFORMED)
    if citations[0].reporter == "SCC Supp":
        supp = supp_key(citations[0].year or 0, citations[0].volume, citations[0].page)
        result, supp_titles = _supp_answer(conn, supp)
        typed = claimed_name or _name_before_citation(citation_text, citations[0])
        if result is VerifyResult.IN_INDEX and typed:  # #509: a supplementary-volume answer compares a typed name too
            state = _name_state(conn, supp, supp_titles, typed)
            if state == NAME_MISMATCH_STATE:
                return ExistenceAnswer(VerifyResult.NAME_MISMATCH, True)
            if state == NAME_STRONG:
                return ExistenceAnswer(VerifyResult.IN_INDEX, True)
        return ExistenceAnswer(result)
    key = _citation_key(citations[0])
    if key is None:
        return ExistenceAnswer(VerifyResult.NOT_IN_INDEX)
    claimed_name = claimed_name or _name_before_citation(citation_text, citations[0])
    resolved = resolve_citation_key(conn, key, claimed_name, citations[0].year)
    if resolved.status is VerifyResult.CONFLICT:
        resolved = _collapse_alias_conflict(conn, key, resolved, citations[0].year) or resolved
    if resolved.status is not None:
        return ExistenceAnswer(resolved.status)
    in_year = _rows_in_cited_year(conn, resolved.case_rows, citations[0].year)
    rows = [r for r in in_year if _title_carries_the_cited_parties(conn, key, r["title"])]
    # #717: WITHOUT a quote nothing else stands between a different case and IN_INDEX, so the answer is stricter than `verify`'s
    # candidate test: exactly ONE distinct case must survive (several cannot be told apart: no evidence either way), and its
    # title may carry at most EXISTENCE_MAX_EXTRA_TITLE_TOKENS distinctive words the citation's parties do not (a title that adds
    # many words is probably a different case sharing the party words). Anything else answers NOT_IN_INDEX, never IN_INDEX.
    cases = {r["case_id"]: r for r in rows}
    if len(cases) != 1:
        return ExistenceAnswer(VerifyResult.NOT_IN_INDEX)
    only = next(iter(cases.values()))
    if _extra_title_tokens(conn, key, only["title"]) > EXISTENCE_MAX_EXTRA_TITLE_TOKENS:
        return ExistenceAnswer(VerifyResult.NOT_IN_INDEX)
    if claimed_name:
        state = _name_state(conn, key, [only["title"]], claimed_name)
        if state == NAME_MISMATCH_STATE:
            return ExistenceAnswer(VerifyResult.NAME_MISMATCH, True)
        if state == NAME_STRONG:
            return ExistenceAnswer(VerifyResult.IN_INDEX, True)
    return ExistenceAnswer(VerifyResult.IN_INDEX)


_COVERAGE_CACHE: dict[tuple[str, int, int], dict[str, Any]] = {}


def index_coverage(conn: sqlite3.Connection, path: str, mtime_ns: int) -> dict[str, Any]:
    """#832 E2: what the index holds and what the citation check can resolve, read from the index (cached per file and mtime).
    `courts` lists the courts a citation can resolve to, with `resolvable_cases` and `year_min`/`year_max` over THOSE cases
    only; `courts_held` counts every case row by court (years `held_year_min`/`held_year_max`); `courts_resolvable` counts only
    cases a citation can resolve to (those reached through `citation_aliases`, which mines SCC-family citations): a court
    held but not resolvable is answered NOT_IN_INDEX."""
    k = (path, mtime_ns, 0)
    if k in _COVERAGE_CACHE:
        return _COVERAGE_CACHE[k]
    conn.row_factory = sqlite3.Row
    held_sql = "SELECT court, COUNT(*) AS n FROM cases GROUP BY court ORDER BY n DESC"
    held = {r["court"]: r["n"] for r in conn.execute(held_sql)}
    # IN over a subquery, not a JOIN: `cases` is an FTS5 table and SQLite 3.46 (the container image) plans the JOIN as a full
    # scan of `cases` per alias row (~30 s on the slim index). Same counts. Do not make it NOT IN (a NULL alias case_id matters).
    res_sql = (
        "SELECT c.court AS court, COUNT(DISTINCT c.case_id) AS n FROM cases c "
        "WHERE c.case_id IN (SELECT case_id FROM citation_aliases) GROUP BY c.court ORDER BY n DESC"
    )
    res = {r["court"]: r["n"] for r in conn.execute(res_sql)}
    held_yr = conn.execute("SELECT MIN(year) AS lo, MAX(year) AS hi FROM cases").fetchone()
    # years over the cases a citation can RESOLVE to (the count the reader sees beside them), not over everything held
    yr = conn.execute(
        "SELECT MIN(c.year) AS lo, MAX(c.year) AS hi FROM cases c WHERE c.case_id IN (SELECT case_id FROM citation_aliases)"
    ).fetchone()
    out = {
        "judgments_held": sum(held.values()),
        "courts_held": held,
        "courts": sorted(res),
        "courts_resolvable": res,
        "resolvable_cases": sum(res.values()),
        "resolvable_citation_strings": conn.execute("SELECT COUNT(DISTINCT citation) FROM citation_aliases").fetchone()[0],
        "year_min": yr["lo"],
        "year_max": yr["hi"],
        "held_year_min": held_yr["lo"],
        "held_year_max": held_yr["hi"],
        "note": (
            "Only SCC-family citations resolve today; a citation to any court or reporter not listed under courts_resolvable "
            "is answered not_in_index, which is no evidence either way."
        ),
    }
    _COVERAGE_CACHE[k] = out
    return out
