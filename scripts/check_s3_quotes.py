# ruff: noqa: E501
#!/usr/bin/env python3
"""The S3 BOX gate: the shipped quotes against the sha-pinned PRIVATE archive of the full-text index (real text; never run in public CI).

Usage: check_s3_quotes.py --archive PATH [--dev-index PATH] [--mutations] [--out FILE]

Checks (every one FAILS, never skips): archive sha256 equals the attestation's; the pinned case is in the archive with its full text
and the text hashes to the attested sha256; every `quote_raw` of the asset is a BYTE-EXACT substring of the RAW pinned text (not
after any fold), once, in order; the asset's units equal the units re-extracted from the raw text; no page header or note marker is
inside a quote or inside the stem-to-item-(7) block; no reporter/publisher marker; the caution is an exact raw prefix of the second
copy's sentence; quote_block_sha256, normaliser, fold and cleaner-A hashes equal the attestation's; the corroboration table
recomputed from the archive equals the attested table. With `--dev-index` (read-only) the file's sha256 and the pinned row's
title/year are compared too. With `--mutations` each real-text mutation (m1, m2, m3, m7 and the header-at-window-edge case) must be
caught. The output file's sha256 goes in the PR record.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

from pravrudhi.application import quashing as G
from pravrudhi.application import quashing_gate as Q

PIN = "5ecd439db292f46d0bbad761"
SECOND = "c688a5684f046cc585159999"


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_archive(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def units_of(asset: dict[str, Any]) -> Q.Units:
    return Q.Units(asset["stem"]["quote_raw"], tuple(i["quote_raw"] for i in asset["items"]), asset["caution"]["quote_raw"])


def check(asset: dict[str, Any], att: dict[str, Any], rows: list[dict[str, Any]], archive_sha: str) -> list[str]:
    """All failures for one (asset, attestation, archive) triple; an empty list is a pass."""
    fails: list[str] = []
    if archive_sha != att["archive_sha256"]:
        fails.append("archive sha256 differs from the attestation's")
    pins = [r for r in rows if r["case_id"] == att["case_id"]]
    if len(pins) != 1 or not pins[0]["full_text"]:
        return [*fails, "the pinned case is not in the archive exactly once with its full text"]
    pin = pins[0]
    raw = pin["window"]
    if hashlib.sha256(raw.encode("utf-8")).hexdigest() != att["text_sha256"] or pin["text_sha256"] != att["text_sha256"]:
        fails.append("the pinned full text does not hash to the attested text sha256")
    if pin["title"] != att["row_title"] or pin["year"] != att["row_year"]:
        fails.append("the archive row's title/year differ from the attestation")
    try:
        units = units_of(asset)
        reextracted = Q.extract_units(raw)
    except (Q.ExtractError, KeyError) as e:
        return [*fails, f"extraction failed: {e}"]
    if units != reextracted:
        fails.append("the asset's units differ from the units re-extracted from the raw pinned text")
    second = next((r for r in rows if r["case_id"] == SECOND and r["full_text"]), None)
    fails += Q.exact_failures(units, raw, second["window"] if second else None)
    if second is None:
        fails.append("the second exact copy is missing from the archive")
    if Q.block_between_has_artefact(raw, reextracted):
        fails.append("a page header or note marker is inside the stem-to-item-(7) block")
    for name, text in (
        ("stem", units.stem),
        *[(f"item{k}", t) for k, t in enumerate(units.items, 1)],
        ("caution", units.caution),
    ):
        if Q.clean_a(text) != text:
            fails.append(f"{name}: cleaner A would remove text from the quote (a header or note marker is inside it)")
        if Q.reporter_markers(text):
            fails.append(f"{name}: a reporter or publisher marker is inside the quote")
    if Q.quote_block_sha256(units) != att["quote_block_sha256"]:
        fails.append("quote_block_sha256 differs from the attestation's")
    for key, now in (
        ("normaliser_sha256", Q.normaliser_hash()),
        ("cleaner_a_sha256", Q.cleaner_hash()),
    ):
        if att[key] != now:
            fails.append(f"{key} differs from the attestation's")
    if att["fold"]["sha256"] != Q.fold_hash():
        fails.append("the s3_fold_v1 hash differs from the attestation's")
    others = [r for r in rows if r["case_id"] != att["case_id"]]
    table = Q.table(Q.corroborate(units, others))
    if table != att["corroboration"]:
        fails.append("the corroboration table recomputed from the archive differs from the attested table")
    return fails


def mutation_proofs(asset: dict[str, Any], att: dict[str, Any], rows: list[dict[str, Any]], archive_sha: str) -> list[str]:
    """Each real-text mutation must make `check` fail (or, for m3, the consensus rule leave CORROBORATED). Returns the failures of THIS
    proof (a mutation that was NOT caught)."""
    uncaught: list[str] = []

    def mutate(fn: Any) -> dict[str, Any]:
        m = copy.deepcopy(asset)
        fn(m)
        return m

    def rep(path: tuple[str, ...], a: str, b: str) -> Any:
        def fn(m: dict[str, Any]) -> None:
            node: Any = m
            for p in path[:-1]:
                node = node[int(p)] if isinstance(node, list) else node[p]
            key = path[-1]
            node[key] = node[key].replace(a, b, 1)

        return fn

    cases = {
        "m1_punctuation_only": mutate(rep(("items", "3", "quote_raw"), "(4) Where, the", "(4) Where the")),
        "m1_case_only": mutate(rep(("items", "1", "quote_raw"), "Where", "where")),
        "m1_one_letter": mutate(rep(("items", "0", "quote_raw"), "allegations", "allegatiols")),
        "m2_stem_char": mutate(rep(("stem", "quote_raw"), "extraordinary", "extraordinarx")),
        "m7_caution_through_header": mutate(
            lambda m: m["caution"].__setitem__(
                "quote_raw",
                m["caution"]["quote_raw"]
                + " and that the extraordinary or\nAmanullah & Anr vs State Of Bihar & Ors on 12 April, 2016\nIndian Kanoon - http://indiankanoon.org/doc/98540909/ 11",
            )
        ),
    }
    for name, m in cases.items():
        if not check(m, att, rows, archive_sha):
            uncaught.append(name)
    # m3: a quote_raw taken from a typo'd region of another copy: the pinned text no longer equals the consensus
    typo = mutate(rep(("stem", "quote_raw"), "rigid formulae", "rigid formulate"))
    if typo["stem"]["quote_raw"] == asset["stem"]["quote_raw"]:
        uncaught.append("m3_setup: the stem no longer holds 'rigid formulae'")
    else:
        status = {
            t["unit"]: t["status"]
            for t in Q.table(Q.corroborate(units_of(typo), [r for r in rows if r["case_id"] != att["case_id"]]))
        }
        if status["stem"] == "CORROBORATED":
            uncaught.append("m3_typo_region")
        if not check(typo, att, rows, archive_sha):
            uncaught.append("m3_typo_region_exact")
    # header at the window edge: cleaner A must still remove a header whose title was cut by the window
    edge = "words Indian Kanoon - http://indiankanoon.org/doc/98540909/ 11 words"
    if "doc/98540909" in Q.clean_a(edge):
        uncaught.append("header_at_window_edge")
    return uncaught


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--archive", required=True, type=Path)
    ap.add_argument("--dev-index", type=Path)
    ap.add_argument("--mutations", action="store_true")
    ap.add_argument("--out", type=Path)
    a = ap.parse_args()
    asset = json.loads(G.ASSET.read_text(encoding="utf-8"))
    att = json.loads(G.ATTESTATION.read_text(encoding="utf-8"))
    archive_sha = _sha(a.archive)
    rows = load_archive(a.archive)
    lines = [
        f"archive {a.archive} sha256 {archive_sha} rows {len(rows)}",
        f"attestation dev_index_file_sha256 {att['dev_index_file_sha256']}",
    ]
    fails = check(asset, att, rows, archive_sha)
    if a.dev_index is not None:
        dev_sha = _sha(a.dev_index)
        lines.append(f"dev index {a.dev_index} sha256 {dev_sha}")
        if dev_sha != att["dev_index_file_sha256"]:
            lines.append(
                "NOTE: the dev index file changed since the attestation (the archive is used; re-attest to move the pin)"
            )
        else:
            conn = sqlite3.connect(f"file:{a.dev_index}?mode=ro", uri=True)
            try:
                row = conn.execute("SELECT title, year, length(text) FROM cases WHERE case_id = ?", (att["case_id"],)).fetchone()
            finally:
                conn.close()
            if row is None or (row[0], row[1]) != (att["row_title"], att["row_year"]):
                fails.append("the dev index row differs from the attestation")
    for t in Q.table(Q.corroborate(units_of(asset), [r for r in rows if r["case_id"] != att["case_id"]])):
        lines.append(
            f"{t['unit']:8} computable={t['n_computable']:3} equal={t['n_equal']:3} variants={[(v['class'], v['n']) for v in t['variants'][:4]]} {t['status']}"
        )
    if a.mutations:
        uncaught = mutation_proofs(asset, att, rows, archive_sha)
        lines.append("mutations: all caught" if not uncaught else f"mutations NOT caught: {uncaught}")
        fails += [f"mutation not caught: {u}" for u in uncaught]
    lines.append("RESULT: " + ("PASS" if not fails else "FAIL"))
    lines += [f"  FAIL: {f}" for f in fails]
    text = "\n".join(lines) + "\n"
    if a.out:
        a.out.write_text(text, encoding="utf-8")
        lines.append(f"output sha256 {hashlib.sha256(text.encode()).hexdigest()}")
    print("\n".join(lines))
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
