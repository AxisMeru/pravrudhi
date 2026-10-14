"""The SLIM, existence-only citation index for the hosted engine (#908).

The dev case index carries the full judgment text (2 GB). The hosted `POST /api/v1/verify-citations` answers EXISTENCE only (a
citation resolves to an indexed case or it does not), which needs titles, courts, years and the mined citation strings, never
the text. `build_slim_index` writes that metadata-only copy (about 15 MB for 38,657 judgments): the `cases` rows keep case id,
title, court, year and source, with `text` and `path_or_url` EMPTY (the dev index's `path_or_url` is a local filesystem path or a
source URL), and the alias table is copied whole, or filtered to one source (`sources`).

A `meta` table records what the file is: `existence_only` = '1' (the route refuses a quote against such a file: a
quote check needs the
text), the build time (the SOURCE file's mtime, so one source gives one output), the sources, the counts, and the
sha256 of the provenance
record. Source filter (the InJudgements-only fallback, a build switch): `cases` rows of the named sources only, and
only the aliases that
were MINED FROM a judgment of those sources (`citation_aliases.case_id` is the citing judgment); a citation that then
resolves to no kept case
answers not_in_index.

No judgment text is ever written: the builder asserts it before it returns.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pravrudhi.application.case_index import open_index

META_SCHEMA = "CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
SLIM_SCHEMA_ID = "slim-v1"
_CHUNK = 5000


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
) -> dict[str, Any]:
    """Write the slim index `out` from the full index `src` (opened READ-ONLY) and return the counts that went into `meta`.
    `sources` None = every source; otherwise only those `cases.source` values (see the module docstring for the alias rule).
    Refuses to overwrite `out`."""
    src, out = Path(src), Path(out)
    if not src.is_file():
        raise SlimBuildError(f"the source index {src} does not exist")
    if out.exists():
        raise SlimBuildError(f"the output {out} already exists (a build never overwrites)")
    wanted = None if sources is None else sorted({s.strip() for s in sources if s.strip()})
    if wanted is not None and not wanted:
        raise SlimBuildError("sources is empty: name at least one source or pass None for all")
    built_utc = datetime.fromtimestamp(src.stat().st_mtime, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
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
            d.execute(META_SCHEMA)
            meta = {
                "schema": SLIM_SCHEMA_ID,
                "existence_only": "1",
                "built_utc": built_utc,
                "sources": ",".join(wanted) if wanted is not None else ",".join(sorted(have)),
                "source_filter": "all" if wanted is None else "only",
                "counts": json.dumps(counts, sort_keys=True),
                "provenance_sha256": provenance_sha256,
            }
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
    return {**counts, "meta": meta, "sha256": _sha256_file(out), "bytes": out.stat().st_size}


def read_meta(conn: sqlite3.Connection) -> dict[str, str] | None:
    """The `meta` rows of an index, or None when the file has no `meta` table (a full dev index)."""
    try:
        return {k: v for k, v in conn.execute("SELECT key, value FROM meta")}
    except sqlite3.OperationalError as e:
        if "no such table" not in str(e):
            raise
        return None


def is_existence_only(conn: sqlite3.Connection) -> bool:
    """True when the index is a slim file: a `meta` table whose `existence_only` value is anything but '0' (fail
    closed: a missing key,
    an empty or unreadable value reads as existence-only). No `meta` table = a full index."""
    meta = read_meta(conn)
    if meta is None:
        return False
    return meta.get("existence_only", "1") != "0"
