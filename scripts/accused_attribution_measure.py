#!/usr/bin/env python3
"""Score an accused-attribution variant on a held-out constructed set (JSONL), ONCE.

Variants: `d0` (deterministic, lexicon verbs), `d0b` (D0 with an open predicate: the first non-bridge word after the leading
actor group,
a cheap baseline), `m1` (a base language model picks the actor among deterministic candidates; needs --base-url and --model).
The 20% gate (Lead-2 decision 4) is the refusal rate on the positive cells P1-P6 INCLUDING the pronoun cell P4, with the exact
one-sided
Clopper-Pearson upper bound beside it; also reported without P4. Any pass on a negative cell (N*) is a miss; N4b and N5b are
reported on
their own. Row fields: id, cell, sentence, accused_ref {id, aliases, other_parties}, expected (pass|refuse), kind.

Measure-once discipline: a ledger records (set sha256, variant, code sha); scoring the same (set, variant) again is refused unless
`--allow-repeat` is given, and a repeat is recorded as one. The set file is never modified; nothing is tuned here.

    python scripts/accused_attribution_measure.py --set heldout_v2.jsonl --variant d0 --ledger LEDGER --out OUT.json
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import subprocess
import sys
from math import comb
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "pravrudhi_kernel" / "src"))

from pravrudhi.application.nyaya_attribution import AccusedRef, check_attribution  # noqa: E402


def cp_upper(k: int, n: int, a: float = 0.05) -> float:
    if n <= 0:
        return float("nan")
    if k >= n:
        return 1.0
    lo, hi = 0.0, 1.0
    for _ in range(100):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if sum(comb(n, i) * mid**i * (1 - mid) ** (n - i) for i in range(k + 1)) > a else (lo, mid)
    return hi


def code_sha() -> str | None:
    p = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True)
    return p.stdout.strip() or None


def make_checker(variant: str, base_url: str | None, model: str | None, threshold: float, mass_floor: float = 0.5):
    if variant == "d0":
        return lambda q, ref: check_attribution(q, ref)
    if variant == "d0b":
        return lambda q, ref: check_attribution(q, ref, open_verbs=True)
    if variant == "fallback":
        from pravrudhi.application.nyaya_attribution_fallback import check_attribution_fallback

        return lambda q, ref: check_attribution_fallback(q, ref)
    if variant == "m1":
        if not (base_url and model):
            raise SystemExit("refusing: --variant m1 needs --base-url and --model (the BASE model id, not the judge adapter)")
        from pravrudhi.application.nyaya_attribution_m1 import LlmActorSelector, check_attribution_m1
        from pravrudhi.application.typed.decoder import VLLMDecoder

        sel = LlmActorSelector(
            VLLMDecoder(base_url=base_url, model=model, timeout_s=120), threshold=threshold, mass_floor=mass_floor
        )
        return lambda q, ref: check_attribution_m1(q, ref, sel)
    raise SystemExit(f"unknown variant {variant!r}")


def score(rows: list[dict], check) -> dict:
    cells: dict[str, dict] = collections.OrderedDict()
    detail = []
    for r in rows:
        a = r["accused_ref"]
        ref = AccusedRef(a["id"], tuple(a["aliases"]), tuple(tuple(g) for g in a["other_parties"]))
        res = check(r["sentence"], ref)
        c = cells.setdefault(
            r["cell"],
            {
                "kind": r["kind"],
                "n": 0,
                "passed": 0,
                "refused": 0,
                "reasons": collections.Counter(),
                "rules": collections.Counter(),
            },
        )
        c["n"] += 1
        if res.passed:
            c["passed"] += 1
        else:
            c["refused"] += 1
            c["reasons"][res.reason] += 1
        c["rules"][res.rule or "pass"] += 1
        detail.append(
            {
                "id": r["id"],
                "cell": r["cell"],
                "expected": r["expected"],
                "got": "pass" if res.passed else "refuse",
                "reason": res.reason,
                "rule": res.rule,
                "actor_p": getattr(res, "actor_p", None),
                "mass_ratio": getattr(res, "mass_ratio", None),
                "actor_span": res.actor_span,
            }
        )
    out: dict = {"cells": {n: {**c, "reasons": dict(c["reasons"]), "rules": dict(c["rules"])} for n, c in cells.items()}}
    pos = [n for n, c in cells.items() if c["kind"] == "positive"]

    def agg(names: list[str]) -> dict:
        k, n = sum(cells[x]["refused"] for x in names), sum(cells[x]["n"] for x in names)
        return {
            "refused": k,
            "n": n,
            "point": round(k / n, 4) if n else None,
            "cp95_upper": round(cp_upper(k, n), 4) if n else None,
        }

    out["gate_P1_P6_including_P4"] = agg(pos)
    out["P1_P6_excluding_P4"] = agg([n for n in pos if not n.startswith("P4")])
    out["gate_20pct_met_point_estimate"] = bool(pos) and out["gate_P1_P6_including_P4"]["point"] <= 0.20
    neg = [n for n, c in cells.items() if c["kind"] == "negative"]
    out["negatives"] = {
        "passed_miss": sum(cells[n]["passed"] for n in neg),
        "n": sum(cells[n]["n"] for n in neg),
        "by_cell": {n: cells[n]["passed"] for n in neg},
    }
    return {"summary": out, "rows": detail}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--set", type=Path, required=True)
    ap.add_argument("--variant", choices=("d0", "d0b", "m1", "fallback"), required=True)
    ap.add_argument("--base-url")
    ap.add_argument("--model")
    ap.add_argument("--threshold", type=float, default=0.6)
    ap.add_argument("--mass-floor", type=float, default=0.5)
    ap.add_argument("--ledger", type=Path, required=True)
    ap.add_argument("--allow-repeat", action="store_true")
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args(argv)
    raw = a.set.read_bytes()
    set_sha = hashlib.sha256(raw).hexdigest()
    seen = []
    if a.ledger.exists():
        seen = [json.loads(line) for line in a.ledger.read_text().splitlines() if line.strip()]
    if any(e["set_sha256"] == set_sha and e["variant"] == a.variant for e in seen) and not a.allow_repeat:
        raise SystemExit(
            f"refusing: {a.variant} was already scored on set {set_sha[:12]} (measure once; --allow-repeat records a repeat)"
        )
    rows = [json.loads(line) for line in raw.decode().splitlines() if line.strip()]
    res = score(rows, make_checker(a.variant, a.base_url, a.model, a.threshold, a.mass_floor))
    res["summary"].update(
        set_sha256=set_sha,
        n_rows=len(rows),
        variant=a.variant,
        code_sha=code_sha(),
        m1_backend={"base_url": a.base_url, "model": a.model, "threshold": a.threshold, "mass_floor": a.mass_floor}
        if a.variant == "m1"
        else None,
        repeat=any(e["set_sha256"] == set_sha and e["variant"] == a.variant for e in seen),
    )
    a.out.write_text(json.dumps(res, indent=1))
    with a.ledger.open("a") as f:
        f.write(
            json.dumps(
                {
                    "set_sha256": set_sha,
                    "variant": a.variant,
                    "code_sha": res["summary"]["code_sha"],
                    "out": str(a.out),
                    "repeat": res["summary"]["repeat"],
                }
            )
            + "\n"
        )
    s = res["summary"]
    print(
        json.dumps(
            {
                k: s[k]
                for k in (
                    "variant",
                    "set_sha256",
                    "gate_P1_P6_including_P4",
                    "P1_P6_excluding_P4",
                    "gate_20pct_met_point_estimate",
                    "negatives",
                )
            },
            indent=1,
        )
    )
    for name, c in s["cells"].items():
        print(f"  [{c['kind'][:3]}] {name:36s} refused {c['refused']:3d}/{c['n']}  passed {c['passed']:3d}  {c['reasons']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
