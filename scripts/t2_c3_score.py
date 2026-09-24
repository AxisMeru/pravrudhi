"""T2 C3 scoring (sealed prereg sha256 c6e7f601..., raw outputs sha256 d6118974..., R2-signed 2026-09-24):
compute parse rate, decision flips, ECE, element-level non-inferiority, and the order-swap analysis from
`t2_c3_raw_outputs.jsonl`. No live model calls -- pure scoring over the already-sealed raw completions.

R2's two conditions on scoring (2026-09-24):
(1) heldout_v1 has 14 duplicated ids (84 rows, 70 unique, `dup_in_calib` mirrors the source's own dedup
    flag). calib_v1 has no duplicates (195/195 unique). PRIMARY heldout_v1 figures use UNIQUE ids only
    (first occurrence in file order); WITH-dups (all 84) is reported as a sensitivity line. ECE bins and
    Clopper-Pearson bounds are computed on the unique counts, not reused from any prior 84-row measurement.
(2) Concurrency verification: see RUN-METADATA.md beside the sealed raw-output copy.
"""

from __future__ import annotations

import json
import os
import sys
from math import comb
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from pravrudhi.application.nyaya_judges import p_established_from_top_logprobs, parse_house_fact_id  # noqa: E402
from pravrudhi.application.typed.decoder import score_decision  # noqa: E402
from pravrudhi.application.typed.house_judge import _STATUS_FIELD  # noqa: E402
from pravrudhi.models.openai_compat import CompletionResult  # noqa: E402

TAU = 0.74
RAW_SHA256 = "d6118974e194737ff03480941089780d5a187ebf8e580d91e1dcc5a03899d43c"


def cp_upper(x: int, n: int, alpha: float = 0.05) -> float:
    """One-sided 95% Clopper-Pearson upper bound, same convention as `analyse.py`'s `cp_upper`."""
    if n == 0:
        return float("nan")
    if x >= n:
        return 1.0

    def cdf(k: int, n: int, p: float) -> float:
        return sum(comb(n, i) * p**i * (1 - p) ** (n - i) for i in range(k + 1))

    lo, hi = 0.0, 1.0
    for _ in range(80):
        m = (lo + hi) / 2
        lo, hi = (m, hi) if cdf(x, n, m) > alpha else (lo, m)
    return hi


def score_row(raw: dict[str, Any]) -> dict[str, Any]:
    """p and status/fact_id for both arms on one raw row. Mirrors `t2_c3_ab_run.py`'s own scoring exactly
    (same functions), just applied after the fact to the sealed raw completions rather than live."""
    free_top = raw["free_text"]["top_logprobs"]
    p_free = p_established_from_top_logprobs(free_top[0]) if free_top else None
    fid_free = parse_house_fact_id(raw["free_text"]["text"]) if p_free is not None and p_free >= TAU else None

    typed_top = raw["typed"]["top_logprobs"]
    p_typed = None
    fid_typed = None
    if typed_top:
        res = CompletionResult(text=raw["typed"]["text"], model="x", top_logprobs=typed_top, wall_s=0.0)
        p_typed = score_decision(res, _STATUS_FIELD)["true"]
        fid_typed = parse_house_fact_id(raw["typed"]["text"]) if p_typed >= TAU else None

    return {
        "id": raw["id"], "gold": raw["gold"], "half": raw["half"], "arm_run_first": raw["arm_run_first"],
        "p_free": p_free, "fid_free": fid_free, "status_free": _status(p_free),
        "p_typed": p_typed, "fid_typed": fid_typed, "status_typed": _status(p_typed),
    }


def _status(p: float | None) -> str | None:
    if p is None:
        return None
    return "established" if p >= TAU else "not_established"


def is_flip(s: dict[str, Any]) -> bool:
    if s["p_free"] is None or s["p_typed"] is None:
        return False  # a parse failure is counted separately, not as a flip
    if s["status_free"] != s["status_typed"]:
        return True
    return bool(s["status_free"] == "established" and s["fid_free"] != s["fid_typed"])


def false_establish(scored: list[dict[str, Any]], p_key: str, status_key: str) -> dict[str, Any]:
    neg = [s for s in scored if s["gold"] != "established" and s[p_key] is not None]
    fp = sum(1 for s in neg if s[status_key] == "established")
    n_neg = len(neg)
    rate = fp / n_neg if n_neg else float("nan")
    return {"n_neg": n_neg, "fp": fp, "rate": rate, "cp_upper_95": cp_upper(fp, n_neg)}


def ece(scored: list[dict[str, Any]], p_key: str, n_bins: int) -> dict[str, Any]:
    """Equal-mass (quantile) bins. `p_key`'s probability vs. gold=established as the correctness target."""
    have_p = [s for s in scored if s[p_key] is not None]
    ordered = sorted(have_p, key=lambda s: s[p_key])
    n = len(ordered)
    bin_size = n // n_bins
    remainder = n % n_bins
    bins: list[list[dict[str, Any]]] = []
    idx = 0
    for bin_index in range(n_bins):
        size = bin_size + (1 if bin_index < remainder else 0)
        bins.append(ordered[idx : idx + size])
        idx += size
    total_weighted_gap = 0.0
    bin_reports = []
    for one_bin in bins:
        if not one_bin:
            continue
        mean_p = sum(s[p_key] for s in one_bin) / len(one_bin)
        acc = sum(1 for s in one_bin if s["gold"] == "established") / len(one_bin)
        gap = abs(mean_p - acc)
        total_weighted_gap += gap * len(one_bin) / n
        bin_reports.append({"n": len(one_bin), "mean_p": mean_p, "accuracy": acc, "gap": gap})
    return {"ece": total_weighted_gap, "n_bins": len(bin_reports), "bins": bin_reports}


def parse_rate(scored: list[dict[str, Any]], p_key: str) -> float:
    return sum(1 for s in scored if s[p_key] is not None) / len(scored) if scored else float("nan")


def order_swap_analysis(calib_scored: list[dict[str, Any]]) -> dict[str, Any]:
    """Pooled by CALL POSITION (first vs second), not by arm identity -- the sealed control's actual
    question: does the gap track position (backend artifact) or arm (a real typed-vs-free difference)?"""
    first_ps: list[float] = []
    second_ps: list[float] = []
    pos_flips = 0
    pos_deltas: list[float] = []
    id_deltas: list[float] = []  # free - typed, by ARM identity, for comparison
    for s in calib_scored:
        if s["p_free"] is None or s["p_typed"] is None:
            continue
        p_first, p_second = (s["p_free"], s["p_typed"]) if s["arm_run_first"] == "free_text" else (s["p_typed"], s["p_free"])
        first_ps.append(p_first)
        second_ps.append(p_second)
        pos_deltas.append(p_first - p_second)
        id_deltas.append(s["p_free"] - s["p_typed"])
        if is_flip(s):
            pos_flips += 1
    return {
        "n": len(pos_deltas),
        "by_position": {
            "mean_first_minus_second": sum(pos_deltas) / len(pos_deltas) if pos_deltas else float("nan"),
            "max_abs_first_minus_second": max((abs(d) for d in pos_deltas), default=float("nan")),
        },
        "by_arm_identity": {
            "mean_free_minus_typed": sum(id_deltas) / len(id_deltas) if id_deltas else float("nan"),
            "max_abs_free_minus_typed": max((abs(d) for d in id_deltas), default=float("nan")),
        },
        "flips_in_calib_v1": pos_flips,
        "note": (
            "flip count is identical under either framing (position vs identity) -- {free_text, typed} are "
            "the same two values relabelled. The framings differ in whether the SIGNED delta's pattern "
            "tracks which arm went first (backend artifact, T1's finding) or which arm it structurally is "
            "(a real typed-vs-free effect)."
        ),
    }


def main() -> int:
    results_dir_env = os.environ.get("PRAVRUDHI_T2_RESULTS_DIR")
    if not results_dir_env:
        print("REFUSING: PRAVRUDHI_T2_RESULTS_DIR is not set (results never write inside the repo)", file=sys.stderr)
        return 2
    results_dir = Path(results_dir_env)
    raw_path = results_dir / "t2_c3_raw_outputs.jsonl"
    if not raw_path.exists():
        print(f"REFUSING: {raw_path} does not exist", file=sys.stderr)
        return 2
    raw_bytes = raw_path.read_bytes()
    import hashlib

    digest = hashlib.sha256(raw_bytes).hexdigest()
    if digest != RAW_SHA256:
        print(f"REFUSING: {raw_path} sha256 {digest} != R2-signed {RAW_SHA256}", file=sys.stderr)
        return 2

    raw_rows = [json.loads(line) for line in raw_bytes.decode().splitlines() if line.strip()]
    calib = [r for r in raw_rows if r["row_source"] == "calib_v1"]
    heldout_all = [r for r in raw_rows if r["row_source"] == "heldout_v1"]
    seen: set[str] = set()
    heldout_unique = []
    for r in heldout_all:
        if r["id"] not in seen:
            seen.add(r["id"])
            heldout_unique.append(r)
    print(f"raw sha256 confirmed. calib_v1={len(calib)}, heldout_v1 all={len(heldout_all)} unique={len(heldout_unique)}")

    calib_scored = [score_row(r) for r in calib]
    heldout_all_scored = [score_row(r) for r in heldout_all]
    heldout_unique_scored = [score_row(r) for r in heldout_unique]

    result: dict[str, Any] = {"raw_sha256": digest, "tau": TAU}

    for name, scored, n_bins in [
        ("calib_v1", calib_scored, 4),
        ("heldout_v1_unique_PRIMARY", heldout_unique_scored, 2),
        ("heldout_v1_with_dups_SENSITIVITY", heldout_all_scored, 2),
    ]:
        result[name] = {
            "n": len(scored),
            "parse_rate_free": parse_rate(scored, "p_free"),
            "parse_rate_typed": parse_rate(scored, "p_typed"),
            "flips": sum(1 for s in scored if is_flip(s)),
            "false_establish_free": false_establish(scored, "p_free", "status_free"),
            "false_establish_typed": false_establish(scored, "p_typed", "status_typed"),
            "ece_free": ece(scored, "p_free", n_bins),
            "ece_typed": ece(scored, "p_typed", n_bins),
        }

    result["order_swap_control_calib_v1"] = order_swap_analysis(calib_scored)

    out_path = results_dir / "t2_c3_scored_result.json"
    out_path.write_text(json.dumps(result, indent=2, default=str))
    print(f"result: {out_path}")

    for name in ["calib_v1", "heldout_v1_unique_PRIMARY", "heldout_v1_with_dups_SENSITIVITY"]:
        r = result[name]
        print(
            f"{name}: n={r['n']} flips={r['flips']} "
            f"parse(free/typed)={r['parse_rate_free']:.3f}/{r['parse_rate_typed']:.3f} "
            f"false_est_free={r['false_establish_free']['rate']:.4f} (CP {r['false_establish_free']['cp_upper_95']:.4f}) "
            f"false_est_typed={r['false_establish_typed']['rate']:.4f} (CP {r['false_establish_typed']['cp_upper_95']:.4f}) "
            f"ece_free={r['ece_free']['ece']:.4f} ece_typed={r['ece_typed']['ece']:.4f}"
        )
    os_result = result["order_swap_control_calib_v1"]
    print(f"order-swap (calib_v1, n={os_result['n']}): {json.dumps(os_result, default=str)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
