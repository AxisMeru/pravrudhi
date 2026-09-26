#!/usr/bin/env python3
"""CLI wrapper over `pravrudhi.application.nyaya_pin_regression.compare_pins`. See that module for what
gets compared and why.

Usage:
  python scripts/nyaya_pin_regression.py --old /path/to/score-old --new /path/to/score-new
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from pravrudhi.application.nyaya_pin_regression import compare_pins, list_contract_ids


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--old", type=Path, required=True, help="path to the currently-pinned score binary")
    ap.add_argument("--new", type=Path, required=True, help="path to the pin-candidate score binary")
    args = ap.parse_args()

    for label, path in (("--old", args.old), ("--new", args.new)):
        if not path.exists():
            print(f"{label} binary not found: {path}", file=sys.stderr)
            return 2

    old_ids = list_contract_ids(args.old)
    new_ids = list_contract_ids(args.new)
    print(f"OLD binary contracts: {len(old_ids)}")
    print(f"NEW binary contracts: {len(new_ids)}")
    print(f"New in NEW binary (expected for a pin bump that adds contracts): {sorted(new_ids - old_ids)}")
    print()

    diffs = compare_pins(args.old, args.new)
    print(f"Diffs found: {len(diffs)}")
    for d in diffs:
        print(d)

    return 1 if diffs else 0


if __name__ == "__main__":
    sys.exit(main())
