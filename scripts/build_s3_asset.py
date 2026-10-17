# ruff: noqa: E501
#!/usr/bin/env python3
"""Build the S3 product asset and its attestation from the PRIVATE archive of the full-text index (build time, offline).

Usage: build_s3_asset.py --archive PATH --archive-sha256 HEX --dev-index-sha256 HEX [--out-dir DIR] [--check]

The archive (see docs/decisions in the private record) is NOT in any repository. This script reads the pinned judgment's RAW text
from it, cuts the stem, items (1)-(7) and the caution as exact raw substrings, writes the asset (the Court's words only) and an
attestation (shas, counts, hashes; no judgment text), and fails on any mismatch. `--check` rebuilds in memory and compares with the
committed files byte for byte (R2's reproduction step).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from pravrudhi.application import quashing_gate as Q

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "src" / "pravrudhi" / "assets" / "quashing"
PIN_CASE_ID = "5ecd439db292f46d0bbad761"
SECOND_CASE_ID = "c688a5684f046cc585159999"  # P.M. Lokanath: the second exact copy of the caution sentence (gate only)
SOURCE_NOTE = (
    "The Court's words in paras 102 and 103 of State of Haryana v. Bhajan Lal, as quoted in the judgment of the Supreme Court in "
    "Amanullah v. State of Bihar (2016); copied in at build time from our full-text index of that judgment; no reporter or "
    "publisher text."
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build(archive: Path, archive_sha: str, dev_sha: str) -> tuple[dict[str, Any], dict[str, Any]]:
    if _sha(archive) != archive_sha:
        raise SystemExit(f"FAIL: archive sha256 {_sha(archive)} differs from the pinned {archive_sha}")
    rows = [json.loads(line) for line in archive.read_text(encoding="utf-8").splitlines() if line.strip()]
    pins = [r for r in rows if r["case_id"] == PIN_CASE_ID]
    if len(pins) != 1 or not pins[0]["full_text"]:
        raise SystemExit("FAIL: the pinned case is not present exactly once with its full text")
    pin = pins[0]
    raw = pin["window"]
    if hashlib.sha256(raw.encode("utf-8")).hexdigest() != pin["text_sha256"]:
        raise SystemExit("FAIL: the pinned full text does not hash to its recorded text sha256")
    second = next((r for r in rows if r["case_id"] == SECOND_CASE_ID and r["full_text"]), None)
    units = Q.extract_units(raw)
    fails = Q.exact_failures(units, raw, second["window"] if second else None)
    if second is None:
        fails.append("the second exact copy is missing from the archive")
    if Q.block_between_has_artefact(raw, units):
        fails.append("a page header or note marker sits inside the stem-to-item-(7) block")
    for text in (units.stem, *units.items, units.caution):
        if Q.reporter_markers(text):
            fails.append("a reporter or publisher marker is inside a quote")
    if fails:
        raise SystemExit("FAIL:\n  " + "\n  ".join(fails))
    others = [r for r in rows if r["case_id"] != PIN_CASE_ID]
    table = Q.table(Q.corroborate(units, others))
    asset = {
        "schema": 1,
        "attribution": {
            "court": "Supreme Court of India",
            "case_title": pin["title"],
            "year": pin["year"],
            "para": "102 and 103 of State of Haryana v. Bhajan Lal, as quoted",
            "source_note": SOURCE_NOTE,
        },
        "pin": {"case_id": PIN_CASE_ID, "title": pin["title"], "year": pin["year"], "court": "Supreme Court"},
        "stem": {"quote_raw": units.stem},
        "items": [{"n": k, "quote_raw": t} for k, t in enumerate(units.items, 1)],
        "caution": {"quote_raw": units.caution},
        "repairs": [],
    }
    attestation = {
        "schema": 1,
        "dev_index_file_sha256": dev_sha,
        "archive_sha256": archive_sha,
        "archive_rows": len(rows),
        "case_id": PIN_CASE_ID,
        "text_sha256": pin["text_sha256"],
        "row_title": pin["title"],
        "row_year": pin["year"],
        "quote_block_sha256": Q.quote_block_sha256(units),
        "normaliser_sha256": Q.normaliser_hash(),
        "fold": {"name": "s3_fold_v1", "sha256": Q.fold_hash()},
        "cleaner_a_sha256": Q.cleaner_hash(),
        "builder_script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "gate_module_sha256": hashlib.sha256(Path(Q.__file__).read_bytes()).hexdigest(),
        "case_citations_for_pin": [],
        "corroboration": table,
        "second_copy_case_id": SECOND_CASE_ID,
    }
    return asset, attestation


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--archive", required=True, type=Path)
    ap.add_argument("--archive-sha256", required=True)
    ap.add_argument("--dev-index-sha256", required=True)
    ap.add_argument("--out-dir", type=Path, default=OUT_DIR)
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    asset, att = build(a.archive, a.archive_sha256, a.dev_index_sha256)
    files = {"bhajan_lal_para102.json": asset, "attestation.json": att}
    bad = False
    for name, obj in files.items():
        body = json.dumps(obj, indent=1, ensure_ascii=False, sort_keys=True) + "\n"
        dest = a.out_dir / name
        if a.check:
            same = dest.exists() and dest.read_text(encoding="utf-8") == body
            print(("same: " if same else "DIFFERS: ") + name)
            bad = bad or not same
        else:
            a.out_dir.mkdir(parents=True, exist_ok=True)
            dest.write_text(body, encoding="utf-8")
            print("wrote", dest)
    for t in att["corroboration"]:
        print(f"{t['unit']:8} computable={t['n_computable']:3} equal={t['n_equal']:3} status={t['status']}")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
