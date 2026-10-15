#!/usr/bin/env python3
"""Build the slim, existence-only citation index for the hosted engine (#908) from the full dev case index.

Usage:
    python3 scripts/build_slim_citation_index.py --src /path/cases.sqlite3 --out research/nyaya/case_index/cases_slim.sqlite3 \
        --provenance docs/.../PROVENANCE.md                              # every source (the default)
    python3 scripts/build_slim_citation_index.py ... --sources injudgements   # the InJudgements-only fallback

Writes a metadata-only file (no judgment text, no source path) and prints its counts, size and sha256 as JSON; the
sha256 is the value the
image build pins. The output is a build artifact: never commit it. The source index is opened read-only and never changed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from pravrudhi.application.case_index_slim import SlimBuildError, build_slim_index  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--src", type=Path, required=True, help="the full case index (read-only)")
    ap.add_argument("--out", type=Path, required=True, help="the slim index to write (must not exist)")
    ap.add_argument(
        "--sources", default=None, help="comma-separated cases.source values to keep (default: all), e.g. injudgements"
    )
    ap.add_argument(
        "--own-supp",
        action="store_true",
        help="also write the case_citations table: the supplementary-volume citation each judgment prints in its own header",
    )
    ap.add_argument("--provenance", type=Path, default=None, help="the provenance record; its sha256 goes into the meta table")
    a = ap.parse_args(argv)
    prov = hashlib.sha256(a.provenance.read_bytes()).hexdigest() if a.provenance else ""
    try:
        res = build_slim_index(
            a.src, a.out, sources=a.sources.split(",") if a.sources else None, provenance_sha256=prov, own_supp=a.own_supp
        )
    except SlimBuildError as e:
        print(f"refused: {e}", file=sys.stderr)
        return 2
    print(json.dumps(res, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
