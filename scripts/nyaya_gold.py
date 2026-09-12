#!/usr/bin/env python3
"""CLI wrapper over `pravrudhi.application.nyaya_gold.build_gold_set`. See that module for the taxonomy, the
generator, and why a constructed set is admissible as evidence here.

Usage:
  python scripts/nyaya_gold.py --per-class 120 --seed 0 --out research/ext/nyaya-gold/gold.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from pravrudhi.application.nyaya_gold import CLASSES, build_gold_set


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--per-class", type=int, default=120)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    gold = build_gold_set(args.per_class, args.seed)
    counts = {c: sum(1 for it in gold if it["expected"] == c) for c in CLASSES}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(
        {"seed": args.seed, "per_class_target": args.per_class, "counts": counts, "items": gold},
        indent=1, sort_keys=True,
    ) + "\n")
    print(f"wrote {args.out} -- {len(gold)} items")
    for cls, n in counts.items():
        short = "" if n >= args.per_class else f"  (SHORT of {args.per_class})"
        print(f"  {cls:14} {n}{short}")


if __name__ == "__main__":
    main()
