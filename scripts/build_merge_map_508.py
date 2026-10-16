#!/usr/bin/env python3
"""Write the #508 merge map from Track A's handoff files (no index is read or written).

Usage:
    python3 scripts/build_merge_map_508.py --handoff DIR --out-dir DIR

DIR holds hash_equal_groups.jsonl and ratio_only_pairs_233.jsonl. Writes merge_map.json (the input of
`build_slim_citation_index.py --merge-map`), refused_pairs.json (EVERY ratio-only pair the party-name test refused) and prints
the counts and the sha256 of each input and output as JSON. A build never overwrites an existing file.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from pravrudhi.application.case_merge import KEPT_SOURCE, NOISE_WORDS, Row, plan_merges  # noqa: E402

RULE = (
    "hash-equal text merges; same-date >=0.98 text pairs merge unless party names differ after the closed normalisation; "
    "nothing else merges (Lead-2, 16 Oct 2026, option 2)"
)


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _lines(p: Path) -> list[dict]:  # type: ignore[type-arg]
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--handoff", type=Path, required=True)
    ap.add_argument("--out-dir", type=Path, required=True)
    a = ap.parse_args(argv)
    hg, rp = a.handoff / "hash_equal_groups.jsonl", a.handoff / "ratio_only_pairs_233.jsonl"
    out_map, out_ref = a.out_dir / "merge_map.json", a.out_dir / "refused_pairs.json"
    for o in (out_map, out_ref):
        if o.exists():
            print(f"refused: {o} already exists (a build never overwrites)", file=sys.stderr)
            return 2
    groups = [[Row.from_handoff(r) for r in g["rows"]] for g in _lines(hg)]
    pairs = [(Row.from_handoff(p["a"]), Row.from_handoff(p["b"]), float(p["ratio"])) for p in _lines(rp)]
    plan = plan_merges(groups, pairs)
    counts = {
        "hash_groups": plan.n_hash_groups,
        "ratio_pairs": plan.n_ratio_pairs,
        "ratio_pairs_kept": plan.n_ratio_kept,
        "ratio_pairs_refused": len(plan.refused_pairs),
        "refused_but_joined_by_other_links": len(plan.refused_but_joined),
        "merge_groups": plan.n_groups,
        "rows_dropped": len(plan.dropped_to_kept),
    }
    involved = sorted(set(plan.dropped_to_kept) | set(plan.dropped_to_kept.values()))
    doc = {
        "schema": "merge-map-508-v1",
        "rule": RULE,
        "kept_row": f"recorded year first, then source {KEPT_SOURCE}, then lowest rowid",
        "noise_words": sorted(NOISE_WORDS),
        "inputs": {"hash_equal_groups.jsonl": _sha(hg), "ratio_only_pairs_233.jsonl": _sha(rp)},
        "counts": counts,
        "dropped_to_kept": {str(k): v for k, v in sorted(plan.dropped_to_kept.items())},
        "case_ids": {str(r): plan.rows[r].case_id for r in involved},
        "titles": {str(r): plan.rows[r].title for r in involved},
    }
    a.out_dir.mkdir(parents=True, exist_ok=True)
    out_map.write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    out_ref.write_text(
        json.dumps({"counts": counts, "refused_pairs": plan.refused_pairs, "refused_but_joined": plan.refused_but_joined},
                   indent=1, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({**counts, "merge_map_sha256": _sha(out_map), "refused_pairs_sha256": _sha(out_ref)}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
