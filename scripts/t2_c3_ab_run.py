"""T2 C3 (sealed: tmp_scratch/trackc/sealed/T2-AB-PREREG-v1.0-SEALED-2026-09-24.md, sha256 c6e7f601...):
the judge on calib_v1 (primary, n=195) and heldout_v1 (secondary, n=84) from `prompts.jsonl`
(sha256 d56c449f...) -- free_text (today's HouseJudge) vs typed (TypedHouseJudge), same frozen inputs,
same 5090 vLLM judge, same downstream parsing (parse_house_fact_id, shared).

Order-swap control (sealed §2, C3 only): calib_v1's rows are split into two halves by row parity
(even/odd index WITHIN calib_v1's own filtered order); free_text runs FIRST on the even half, typed runs
FIRST on the odd half. heldout_v1 has no such control in the sealed design -- free_text runs first for
every heldout_v1 row (a simple, stated default, not itself pre-registered).

Concurrency 1 (well under the sealed "concurrency <=2" ceiling), against the LIVE production judge backend
(127.0.0.1:8110) -- gentle load, latency-monitored, and this script never restarts or stops that server.
Raw outputs are hash-sealed to a JSONL file BEFORE any scoring/metric pass, per sealed §6.
"""

from __future__ import annotations

import hashlib
import json
import os
import statistics
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from pravrudhi.application.nyaya_judges import HouseJudge, p_established_from_top_logprobs, parse_house_fact_id  # noqa: E402
from pravrudhi.application.typed.decoder import VLLMDecoder, score_decision  # noqa: E402
from pravrudhi.application.typed.house_judge import _STATUS_FIELD  # noqa: E402

EXPECTED_SHA = "d56c449f9332f22a85176b1008974c1f147a7b8d45e4f5cea0e5ed08a01935cf"
BASE_URL = "http://127.0.0.1:8110/v1"
TAU = 0.74
MAX_TOKENS = 30
TOP_LOGPROBS = 20

#: Latency circuit breaker: after this many calls, abort if the current call is this many times the
#: rolling median of the last 20 -- "stop if its latency degrades" (Lead-2), never silently push through.
_LATENCY_WARMUP = 20
_LATENCY_MULTIPLE = 4.0


class LatencyDegraded(RuntimeError):
    pass


def _score_house(res: Any) -> tuple[float | None, str | None]:
    if not res.top_logprobs:
        return None, None
    p = p_established_from_top_logprobs(res.top_logprobs[0])
    fact_id = parse_house_fact_id(res.text) if p >= TAU else None
    return p, fact_id


def _score_typed(res: Any) -> tuple[float | None, str | None]:
    if not res.top_logprobs:
        return None, None
    p = score_decision(res, _STATUS_FIELD)["true"]
    fact_id = parse_house_fact_id(res.text) if p >= TAU else None
    return p, fact_id


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
    calib_v1 = [r for r in rows if r["split"] == "calib_v1"]
    heldout_v1 = [r for r in rows if r["split"] == "heldout_v1"]
    print(f"sha256 confirmed. calib_v1 n={len(calib_v1)}, heldout_v1 n={len(heldout_v1)}. Backend: {BASE_URL}")

    house = HouseJudge(
        tau=TAU, statute_chars=600, base_url=BASE_URL, max_tokens=MAX_TOKENS, top_logprobs=TOP_LOGPROBS, timeout_s=60
    )
    decoder = VLLMDecoder(base_url=BASE_URL, timeout_s=60)
    print(f"house model: {house.model}  typed model: {decoder.model}")

    wall_times: list[float] = []

    def _timed_complete(fn: Any, *a: Any, **kw: Any) -> Any:
        t0 = time.monotonic()
        res = fn(*a, **kw)
        dt = time.monotonic() - t0
        if len(wall_times) >= _LATENCY_WARMUP:
            median = statistics.median(wall_times[-20:])
            if dt > median * _LATENCY_MULTIPLE:
                raise LatencyDegraded(f"call took {dt:.2f}s, > {_LATENCY_MULTIPLE}x rolling median {median:.2f}s -- stopping")
        wall_times.append(dt)
        return res

    def _call_free(prompt: str) -> Any:
        return _timed_complete(house._complete, prompt)  # noqa: SLF001

    def _call_typed(prompt: str) -> Any:
        return _timed_complete(decoder.complete, prompt, max_tokens=MAX_TOKENS, temperature=0.0, logprobs=TOP_LOGPROBS)

    raw_rows: list[dict[str, Any]] = []
    try:
        for i, row in enumerate(calib_v1):
            half = "even" if i % 2 == 0 else "odd"
            first = "free_text" if half == "even" else "typed"
            if first == "free_text":
                res_free = _call_free(row["prompt"])
                res_typed = _call_typed(row["prompt"])
            else:
                res_typed = _call_typed(row["prompt"])
                res_free = _call_free(row["prompt"])
            raw_rows.append(
                {
                    "row_source": "calib_v1", "index_in_split": i, "id": row.get("id"), "gold": row["gold"],
                    "half": half, "arm_run_first": first,
                    "free_text": {"text": res_free.text, "top_logprobs": res_free.top_logprobs},
                    "typed": {"text": res_typed.text, "top_logprobs": res_typed.top_logprobs},
                }
            )
            if (i + 1) % 50 == 0:
                print(f"  calib_v1 {i + 1}/{len(calib_v1)}")

        for i, row in enumerate(heldout_v1):
            res_free = _call_free(row["prompt"])
            res_typed = _call_typed(row["prompt"])
            raw_rows.append(
                {
                    "row_source": "heldout_v1", "index_in_split": i, "id": row.get("id"), "gold": row["gold"],
                    "half": None, "arm_run_first": "free_text",
                    "free_text": {"text": res_free.text, "top_logprobs": res_free.top_logprobs},
                    "typed": {"text": res_typed.text, "top_logprobs": res_typed.top_logprobs},
                }
            )
            if (i + 1) % 50 == 0:
                print(f"  heldout_v1 {i + 1}/{len(heldout_v1)}")
    except LatencyDegraded as e:
        print(f"ABORTING: {e}", file=sys.stderr)
        print(f"completed {len(raw_rows)}/{len(calib_v1) + len(heldout_v1)} rows before stopping", file=sys.stderr)

    # -- hash-seal raw outputs BEFORE any scoring pass (sealed §6) -----------------------------------------
    raw_path = results_dir / "t2_c3_raw_outputs.jsonl"
    with raw_path.open("w") as f:
        for r in raw_rows:
            f.write(json.dumps(r) + "\n")
    raw_sha = hashlib.sha256(raw_path.read_bytes()).hexdigest()
    print()
    print(f"RAW OUTPUTS SEALED: {raw_path}, sha256 {raw_sha}, {len(raw_rows)} rows")
    print("This sha must reach R2 before any scoring pass, per the sealed prereg.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
