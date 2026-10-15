"""The SLIM, existence-only citation index for the hosted engine (#908).

The dev case index carries the full judgment text (2 GB). Once the hosted `POST /api/v1/verify-citations` is served, it answers
EXISTENCE only (a citation resolves to an indexed case or it does not), which needs titles, courts, years and the mined citation
strings, never the text. `build_slim_index` writes that metadata-only copy (about 11 MB, 10.8 MB measured for 38,657 judgments):
the `cases` rows keep case id, title, court, year and source, with `text` and `path_or_url` EMPTY (the dev index's `path_or_url`
is a local filesystem path or a source URL), and the alias table is copied whole, or filtered to one source (`sources`).

A `meta` table records what the file is: `existence_only` = '1' (the route refuses a quote against such a file: a quote check
needs the text), `source_mtime_utc` (the modification time of the SOURCE index, not the time of the build: one source gives one
output), the sources, the counts, and the sha256 of the provenance record.

Source filter (the InJudgements-only fallback, a build switch): `cases` rows of the named sources only, and only the aliases that
were MINED FROM a judgment of those sources (`citation_aliases.case_id` is the citing judgment); a citation that then resolves
to no kept case answers not_in_index.

No judgment text is ever written: the builder asserts it before it returns.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pravrudhi.application.case_index import open_index
from pravrudhi.application.citations import _SCC_SUPP, supp_key

META_SCHEMA = "CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
SLIM_SCHEMA_ID = "slim-v1"
_CHUNK = 5000

#: Own-citation table (own-supp-v1): which SCC supplementary-volume citation a judgment PRINTS IN ITS OWN HEADER. A citation in
#: this form resolves only to the record whose header carries it, never to a same-party neighbour and without any year window
#: (the reporting year of a supplementary volume can be two years after the decision).
OWN_CITATIONS_SCHEMA = (
    "CREATE TABLE case_citations (citation TEXT NOT NULL, case_id TEXT NOT NULL, PRIMARY KEY (citation, case_id))"
)
OWN_CITATIONS_EXT = "own-supp-v1"
_HEADER_CHARS = 1200  # the header block of a record: the first characters of its text
_HEADER_LABEL = re.compile(r"equivalent\s{1,4}citations?|citation\s{0,4}:", re.IGNORECASE)
_LABEL_REACH = 700  # a citation counts only within this many characters after the "Equivalent citations" / "CITATION:" label
_MAX_YEAR_LAG = 5  # the reporting year may follow the decision year by at most this many years (a Supp volume lags 0 to 3)
_TITLE_YEAR = re.compile(r"\bon\s{1,4}\d{1,2}\s{1,4}\w{1,12},?\s{1,4}((?:19|20)\d{2})\b")


def own_supp_citations(text: str, *, title: str = "", year: int | None = None) -> list[str]:
    """The supplementary-volume keys a judgment prints in its OWN header (the "Equivalent citations" or "CITATION:" block in
    the first `_HEADER_CHARS` characters of `text`). Three guards keep a citation to ANOTHER judgment (a case cited in the first
    paragraph) out: the label must precede it and it must lie within `_LABEL_REACH` characters of the label; and the reporting
    year must not precede the decision year nor follow it by more than `_MAX_YEAR_LAG`. The decision year is the recorded year,
    else the year in an "on <day> <month>, <year>" title (every InJudgements record has no recorded year); with neither,
    nothing is returned. Keys are `citations.supp_key` strings, in order of appearance, without repeats."""
    head = text[:_HEADER_CHARS]
    label = _HEADER_LABEL.search(head)
    if label is None:
        return []
    decided = year
    if decided is None:
        m = _TITLE_YEAR.search(title)
        decided = int(m.group(1)) if m else None
    if decided is None:
        return []
    out: list[str] = []
    for m in _SCC_SUPP.finditer(head):
        if not label.start() <= m.start() <= label.start() + _LABEL_REACH:
            continue
        cited = int(m["yA"] or m["yB"])
        if not 0 <= cited - decided <= _MAX_YEAR_LAG:
            continue
        volume = m["vA"] or m["vB1"] or m["vB2"]
        key = supp_key(cited, int(volume) if volume else None, int(m["pA"] or m["pB"]))
        if key not in out:
            out.append(key)
    return out


class SlimBuildError(ValueError):
    """The build refused (a bad argument, an existing output, an empty selection, or text found in the output)."""


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def build_slim_index(
    src: Path,
    out: Path,
    *,
    sources: Iterable[str] | None = None,
    provenance_sha256: str = "",
    own_supp: bool = False,
) -> dict[str, Any]:
    """Write the slim index `out` from the full index `src` (opened READ-ONLY) and return the counts that went into `meta`.
    `sources` None = every source; otherwise only those `cases.source` values (see the module docstring for the alias rule).
    `own_supp` also writes the `case_citations` table (see `own_supp_citations`) from the text headers of `src` and records it in
    `meta` (`schema_ext` = own-supp-v1 and the counts); every other table is built exactly as without it.
    Refuses to overwrite `out`."""
    src, out = Path(src), Path(out)
    if not src.is_file():
        raise SlimBuildError(f"the source index {src} does not exist")
    if out.exists():
        raise SlimBuildError(f"the output {out} already exists (a build never overwrites)")
    wanted = None if sources is None else sorted({s.strip() for s in sources if s.strip()})
    if wanted is not None and not wanted:
        raise SlimBuildError("sources is empty: name at least one source or pass None for all")
    source_mtime_utc = datetime.fromtimestamp(src.stat().st_mtime, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    s = sqlite3.connect(f"file:{src.resolve()}?mode=ro", uri=True)
    try:
        have = {r[0] for r in s.execute("SELECT DISTINCT source FROM cases")}
        if wanted is not None and not set(wanted) <= have:
            raise SlimBuildError(f"unknown source(s) {sorted(set(wanted) - have)}; the index holds {sorted(have)}")
        keep_sql = "" if wanted is None else " WHERE source IN (" + ",".join("?" * len(wanted)) + ")"
        keep_args: tuple[str, ...] = () if wanted is None else tuple(wanted)
        d = open_index(out)
        try:
            n_cases = 0
            cur = s.execute(f"SELECT case_id, title, court, year, source FROM cases{keep_sql} ORDER BY rowid", keep_args)
            while True:
                rows = cur.fetchmany(_CHUNK)
                if not rows:
                    break
                d.executemany(
                    "INSERT INTO cases (case_id, title, court, year, source, path_or_url, text) VALUES (?, ?, ?, ?, ?, '', '')",
                    rows,
                )
                n_cases += len(rows)
            if n_cases == 0:
                raise SlimBuildError("the selection holds no case")
            alias_sql = "SELECT party_1, party_2, citation, case_id FROM citation_aliases"
            if wanted is not None:
                # the citing judgment's source decides: an alias mined from a judgment outside the selection is not carried
                alias_sql += " WHERE case_id IN (SELECT case_id FROM cases" + keep_sql + ")"
            n_alias = 0
            cur = s.execute(alias_sql, keep_args)
            while True:
                rows = cur.fetchmany(_CHUNK)
                if not rows:
                    break
                d.executemany(
                    "INSERT OR IGNORE INTO citation_aliases (party_1, party_2, citation, case_id) VALUES (?, ?, ?, ?)", rows
                )
                n_alias += len(rows)
            counts = {
                "cases": n_cases,
                "alias_rows": d.execute("SELECT COUNT(*) FROM citation_aliases").fetchone()[0],
                "citation_strings": d.execute("SELECT COUNT(DISTINCT citation) FROM citation_aliases").fetchone()[0],
                "by_source": {k: v for k, v in d.execute("SELECT source, COUNT(*) FROM cases GROUP BY source ORDER BY source")},
            }
            if d.execute("SELECT COUNT(*) FROM cases WHERE text != '' OR path_or_url != ''").fetchone()[0]:
                raise SlimBuildError("text or a path was found in the slim cases table (never written)")
            own_counts: dict[str, int] = {}
            if own_supp:
                d.execute(OWN_CITATIONS_SCHEMA)
                scanned = records = 0
                cur = s.execute(
                    f"SELECT case_id, title, year, substr(text, 1, {_HEADER_CHARS}) FROM cases{keep_sql} ORDER BY rowid",
                    keep_args,
                )
                for case_id, title, year, head in cur:
                    scanned += 1
                    try:
                        decided = int(year) if year not in (None, "") else None
                    except (TypeError, ValueError):
                        decided = None
                    for key in own_supp_citations(head or "", title=title or "", year=decided):
                        d.execute("INSERT OR IGNORE INTO case_citations (citation, case_id) VALUES (?, ?)", (key, case_id))
                        records += 1
                own_counts = {
                    "own_supp_cases_scanned": scanned,
                    "own_supp_rows": d.execute("SELECT COUNT(*) FROM case_citations").fetchone()[0],
                    "own_supp_keys": d.execute("SELECT COUNT(DISTINCT citation) FROM case_citations").fetchone()[0],
                    "own_supp_cases": d.execute("SELECT COUNT(DISTINCT case_id) FROM case_citations").fetchone()[0],
                }
            d.execute(META_SCHEMA)
            meta = {
                "schema": SLIM_SCHEMA_ID,
                "existence_only": "1",
                "source_mtime_utc": source_mtime_utc,
                "sources": ",".join(wanted) if wanted is not None else ",".join(sorted(have)),
                "source_filter": "all" if wanted is None else "only",
                "counts": json.dumps(counts, sort_keys=True),
                "provenance_sha256": provenance_sha256,
            }
            if own_supp:
                meta["schema_ext"] = OWN_CITATIONS_EXT
                meta["own_supp_counts"] = json.dumps(own_counts, sort_keys=True)
            d.executemany("INSERT INTO meta (key, value) VALUES (?, ?)", sorted(meta.items()))
            d.commit()
            d.execute("VACUUM")
        finally:
            d.close()
    except BaseException:
        out.unlink(missing_ok=True)
        raise
    finally:
        s.close()
    return {**counts, **own_counts, "meta": meta, "sha256": _sha256_file(out), "bytes": out.stat().st_size}


def read_meta(conn: sqlite3.Connection) -> dict[str, str] | None:
    """The `meta` rows of an index, or None when the file has no `meta` table (a full dev index)."""
    try:
        return {k: v for k, v in conn.execute("SELECT key, value FROM meta")}
    except sqlite3.OperationalError as e:
        if "no such table" not in str(e):
            raise
        return None
