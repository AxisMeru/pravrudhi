"""T2 C3 baseline (Lead-2, 2026-09-24): the current HouseJudge's false-establish rate at tau=0.74 on
calib_v1 and heldout_v1 separately, for the T2 prereg's baseline-to-beat. One live pass over the same
279-prompt set T1 used (`prompts.jsonl`, sha256 d56c449f...) -- that file IS calib_v1 (n=195) plus
heldout_v1 (n=84) as fed to the judge, each row carrying its own `split` and `gold` label.

False-establish rate = P(model says established at p>=tau | gold is not_established), i.e. FP / N_neg --
exactly `analyse.py`'s own `stats()` function in tmp_scratch/lead2/eval14b/. The bound reported is the
one-sided 95% Clopper-Pearson UPPER bound on that rate, same convention as `analyse.py`'s `cp_upper`.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from math import comb
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from pravrudhi.application.nyaya_judges import HouseJudge, p_established_from_top_logprobs  # noqa: E402

# No host path is committed here. Set PRAVRUDHI_T1_PARITY_PROMPTS to the 279-prompt file; the script refuses
# with a clear message when unset, and hash-checks whatever it points to before using it either way.
EXPECTED_SHA = "d56c449f9332f22a85176b1008974c1f147a7b8d45e4f5cea0e5ed08a01935cf"
BASE_URL = "http://127.0.0.1:8110/v1"
TAU = 0.74
MAX_TOKENS = 30
TOP_LOGPROBS = 20


def cp_upper(x: int, n: int, alpha: float = 0.05) -> float:
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


def main() -> int:
    env_path = os.environ.get("PRAVRUDHI_T1_PARITY_PROMPTS")
    if not env_path:
        print("REFUSING: PRAVRUDHI_T1_PARITY_PROMPTS is not set (no host-path default)", file=sys.stderr)
        return 2
    results_dir_env = os.environ.get("PRAVRUDHI_T2_RESULTS_DIR")
    if not results_dir_env:
        print("REFUSING: PRAVRUDHI_T2_RESULTS_DIR is not set (results never write inside the repo)", file=sys.stderr)
        return 2
    results_dir = Path(results_dir_env)
    results_dir.mkdir(parents=True, exist_ok=True)
    prompts_path = Path(env_path)
    digest = hashlib.sha256(prompts_path.read_bytes()).hexdigest()
    if digest != EXPECTED_SHA:
        print(f"REFUSING: {prompts_path} sha256 {digest} != expected {EXPECTED_SHA}", file=sys.stderr)
        return 2
    rows = [json.loads(line) for line in prompts_path.read_text().splitlines() if line.strip()]
    print(f"{len(rows)} prompts, sha256 confirmed. Backend: {BASE_URL}, concurrency 1.")

    house = HouseJudge(
        tau=TAU, statute_chars=600, base_url=BASE_URL, max_tokens=MAX_TOKENS, top_logprobs=TOP_LOGPROBS, timeout_s=60
    )
    print(f"model: {house.model}")

    by_split: dict[str, list[dict[str, object]]] = {}
    for i, row in enumerate(rows):
        res = house._complete(row["prompt"])  # noqa: SLF001 -- current production HouseJudge's own transport
        p = p_established_from_top_logprobs(res.top_logprobs[0]) if res.top_logprobs else None
        by_split.setdefault(str(row["split"]), []).append({"id": row.get("id"), "gold": row["gold"], "p": p})
        if (i + 1) % 50 == 0:
            print(f"  {i + 1}/{len(rows)}")

    print()
    result: dict[str, object] = {"prompts_sha256": digest, "model": house.model, "tau": TAU, "splits": {}}
    for split, split_rows in by_split.items():
        neg = [r for r in split_rows if r["gold"] != "established" and r["p"] is not None]
        fp = sum(1 for r in neg if r["p"] >= TAU)  # type: ignore[operator]
        n_neg = len(neg)
        rate = fp / n_neg if n_neg else float("nan")
        upper = cp_upper(fp, n_neg)
        print(f"{split}: n={len(split_rows)}, n_neg={n_neg}, fp={fp}, false_establish_rate={rate:.4f}, cp_upper_95={upper:.4f}")
        result["splits"][split] = {  # type: ignore[index]
            "n": len(split_rows), "n_neg": n_neg, "fp": fp, "false_establish_rate": rate, "cp_upper_95": upper,
        }

    out_path = results_dir / "typed_layer_c3_baseline_result.json"
    out_path.write_text(json.dumps(result, indent=2))
    print(f"result: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
