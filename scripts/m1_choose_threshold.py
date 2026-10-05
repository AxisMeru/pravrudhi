#!/usr/bin/env python3
"""Choose and FREEZE the M1 selector's probability threshold and mass floor from a DEV run made with permissive settings (0.0,
0.0).

Rule, written before the dev run was read: over the grid theta in {0.5, 0.6, 0.7, 0.8, 0.9} x mass_floor in {0.3, 0.5, 0.7},
keep the pairs with ZERO negative false passes on the dev set (kind negative, got pass); among them take the lowest refusal
rate on the positives that should pass (expected pass); ties go to the higher theta, then the higher floor. The sweep is
exact, because both settings only turn a pass into a refusal. If no pair has zero misses, it refuses to choose (exit 1) and
says so. Writes the frozen values with the shas of the dev set, the dev results and the code, to be committed BEFORE the
held-out run.

    python scripts/m1_choose_threshold.py --dev-result DEV.json --out m1_frozen.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

THETAS = (0.5, 0.6, 0.7, 0.8, 0.9)
FLOORS = (0.3, 0.5, 0.7)


def sweep(rows: list[dict]) -> list[dict]:
    out = []
    pos = [r for r in rows if r["expected"] == "pass"]
    neg = [r for r in rows if r["expected"] == "refuse" and r["cell"].startswith("N")]
    for th in THETAS:
        for fl in FLOORS:

            def passes(r: dict, th: float = th, fl: float = fl) -> bool:
                return r["got"] == "pass" and (r["actor_p"] or 0) >= th and (r["mass_ratio"] or 0) >= fl

            refused = sum(1 for r in pos if not passes(r))
            misses = sum(1 for r in neg if passes(r))
            out.append(
                {
                    "theta": th,
                    "mass_floor": fl,
                    "pos_refused": refused,
                    "n_pos": len(pos),
                    "pos_refusal": round(refused / len(pos), 4) if pos else None,
                    "neg_misses": misses,
                    "n_neg": len(neg),
                }
            )
    return out


def choose(table: list[dict]) -> dict | None:
    ok = [t for t in table if t["neg_misses"] == 0]
    if not ok:
        return None
    return sorted(ok, key=lambda t: (t["pos_refusal"], -t["theta"], -t["mass_floor"]))[0]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dev-result", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args(argv)
    raw = a.dev_result.read_bytes()
    doc = json.loads(raw)
    table = sweep(doc["rows"])
    pick = choose(table)
    code = (
        subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=Path(__file__).parent).stdout.strip()
        or None
    )
    frozen = {
        "label": "constructed, dev stack (local); thresholds chosen on the DEV run only",
        "chosen": pick,
        "table": table,
        "dev_set_sha256": doc["summary"].get("set_sha256"),
        "dev_result_sha256": hashlib.sha256(raw).hexdigest(),
        "code_sha": code,
    }
    a.out.write_text(json.dumps(frozen, indent=1))
    print(json.dumps({"chosen": pick, "dev_set_sha256": frozen["dev_set_sha256"]}, indent=1))
    if pick is None:
        print("REFUSING to choose: no (theta, floor) gives zero negative false passes on the dev set")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
