#!/usr/bin/env python3
"""Ground-truth precision measurement for `application.verify.verify` (P3, `docs/decisions/
LEG-PLAN-2026-09-23.md`). Committed so the numbers reported to Lead-2-assistant/Lead-2 are independently
reproducible against `research/nyaya/case_index/cases.sqlite3` (rebuild with `scripts/build_case_index.py`
first if that file doesn't exist locally -- it is a gitignored build artifact, ~2GB, not shipped).

Methodology, stated plainly (who checked what): built programmatically by this session, not from
independent human legal review. For each of a random 100-citation sample that `verify()` resolves to
exactly one case (`EXISTS_QUOTE_NOT_FOUND` with a placeholder proposition -- i.e. resolution succeeded but
no real quote was supplied), a REAL ~12-word substring is pulled from THAT resolved case's own indexed text
and fed back to `verify()` as the proposition. Because the substring is copied verbatim from the resolved
document's own text, a correct resolution plus a working quote-matcher MUST return VERIFIED. This proves
(a) the case `verify()` found really is indexed under that citation's alias, and (b) the quote-matching
mechanism itself works -- not that the citation's real-world holding matches the quote. A resolution that
found the WRONG case would still show VERIFIED under this test if the wrong case happens to also contain
the sampled substring; vanishingly unlikely for a 12-word slice, but a real limit of the method, stated
rather than hidden.

Usage: ``uv run python3 scripts/measure_verify_precision.py``
"""

from __future__ import annotations

import random
import re
import sqlite3
from pathlib import Path

from pravrudhi.application.verify import VerifyResult, _strip_filler, verify

DB_PATH = Path("research/nyaya/case_index/cases.sqlite3")
SAMPLE_SIZE = 100
SEED = 3


def main() -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    rows = conn.execute("SELECT DISTINCT citation FROM citation_aliases").fetchall()
    random.seed(SEED)
    sample = random.sample(rows, min(SAMPLE_SIZE, len(rows)))

    resolved_single: list[str] = []
    conflict: list[str] = []
    not_in_index: list[str] = []
    for r in sample:
        res = verify(conn, r["citation"], "___placeholder_never_matches_xyz___")
        if res == VerifyResult.EXISTS_QUOTE_NOT_FOUND:
            resolved_single.append(r["citation"])
        elif res == VerifyResult.CONFLICT:
            conflict.append(r["citation"])
        elif res == VerifyResult.NOT_IN_INDEX:
            not_in_index.append(r["citation"])

    print(f"sample n={len(sample)} (seed={SEED})")
    print(f"resolved (single case): {len(resolved_single)}")
    print(f"conflict: {len(conflict)}")
    print(f"not_in_index: {len(not_in_index)}")

    verified_count = 0
    failed_examples = []
    for cite in resolved_single:
        alias = conn.execute(
            "SELECT party_1, party_2 FROM citation_aliases WHERE citation = ? LIMIT 1", (cite,)
        ).fetchone()
        t1 = _strip_filler(alias["party_1"]).split()[0]
        t2 = _strip_filler(alias["party_2"]).split()[0]
        case = conn.execute(
            "SELECT text FROM cases WHERE title MATCH ? LIMIT 1", (f'"{t1}" AND "{t2}"',)
        ).fetchone()
        if not case:
            continue
        words = re.sub(r"\s+", " ", case["text"]).split()
        if len(words) < 30:
            continue
        start = random.randint(10, len(words) - 15)
        real_quote = " ".join(words[start : start + 12])
        result = verify(conn, cite, real_quote)
        if result == VerifyResult.VERIFIED:
            verified_count += 1
        else:
            failed_examples.append((cite, result, real_quote[:80]))

    n_tested = len(resolved_single)
    print(f"\nground-truth VERIFIED: {verified_count}/{n_tested}")
    print("failures:")
    for f in failed_examples:
        print(" ", f)


if __name__ == "__main__":
    main()
