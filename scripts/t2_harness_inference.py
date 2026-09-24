"""T2 harness-level false-prove check, phase 1: element-level inference (Lead-2, 2026-09-24).

CONSTRUCTED (per the plan doc's own convention -- the frozen eval set is regression-only, never proof) and
this is an ADDITIONAL read of that already-frozen set, alongside the existing config A/B/C re-measure.

1519 elements from `v1val/eval_items.jsonl` (sha256 600f6dc2..., already verified for T2 item 6), BOTH arms
(`free_text` = today's HouseJudge, `typed` = TypedHouseJudge) via pravrudhi's own build_house_prompt/scoring
code -- no cross-repo dependency at THIS phase (phase 2, assembly, needs prabhasa-nyaya's WS-B code; this
phase does not). 3038 live calls total against the production judge (127.0.0.1:8110): concurrency 1, a 50ms
inter-call delay (Lead-2's rate decision, ~6 minutes total), and the same 4x-rolling-median latency circuit
breaker C3 used -- abort and report if it trips, never push through.

Also computes, per row, agreement against `eval_logit_scores.jsonl` (prabhasa-nyaya's own frozen 4B scores,
the ones behind config A's 8/225): this is the first live measurement of the PRODUCT prompt path
(pravrudhi's build_house_prompt via vLLM) on this frozen set, so this agreement number says whether config
A's 8/225 actually describes what the product does, or whether the frozen scores were produced by a
different code path.
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
sys.path.insert(0, str(ROOT / "scripts"))

from _t2_run_metadata import RunMetadata  # noqa: E402

from pravrudhi.application.nyaya_judges import (  # noqa: E402
    HouseJudge,
    JudgeRequest,
    build_house_prompt,
)
from pravrudhi.application.typed.decoder import VLLMDecoder  # noqa: E402

EVAL_ITEMS_SHA = "600f6dc28222b97eb5dd16e8b2ac4d159dd1aea5f1bb05c91eb6ef8a6991fa19"
BASE_URL = "http://127.0.0.1:8110/v1"
JUDGE_CONTAINER_NAME = "vllm-judge"  # local docker container name, for RunMetadata's StartedAt/RestartCount
TAU = 0.74
MAX_TOKENS = 30
TOP_LOGPROBS = 20
STATUTE_CHARS = 600
INTER_CALL_DELAY_S = 0.05  # Lead-2's rate decision: concurrency 1 + 50ms delay, ~6 min for 3038 calls

_LATENCY_WARMUP = 20
_LATENCY_MULTIPLE = 4.0


class LatencyDegraded(RuntimeError):
    pass


def main() -> int:
    eval_items_env = os.environ.get("PRAVRUDHI_T2_EVAL_ITEMS")
    if not eval_items_env:
        print("REFUSING: PRAVRUDHI_T2_EVAL_ITEMS is not set (no host-path default)", file=sys.stderr)
        return 2
    eval_scores_env = os.environ.get("PRAVRUDHI_T2_EVAL_LOGIT_SCORES")
    if not eval_scores_env:
        print("REFUSING: PRAVRUDHI_T2_EVAL_LOGIT_SCORES is not set (no host-path default)", file=sys.stderr)
        return 2
    results_dir_env = os.environ.get("PRAVRUDHI_T2_RESULTS_DIR")
    if not results_dir_env:
        print("REFUSING: PRAVRUDHI_T2_RESULTS_DIR is not set (results never write inside the repo)", file=sys.stderr)
        return 2
    results_dir = Path(results_dir_env)
    results_dir.mkdir(parents=True, exist_ok=True)

    items_path = Path(eval_items_env)
    digest = hashlib.sha256(items_path.read_bytes()).hexdigest()
    if digest != EVAL_ITEMS_SHA:
        print(f"REFUSING: {items_path} sha256 {digest} != expected {EVAL_ITEMS_SHA}", file=sys.stderr)
        return 2
    items = [json.loads(line) for line in items_path.read_text().splitlines() if line.strip()]

    scores_path = Path(eval_scores_env)
    scores_sha = hashlib.sha256(scores_path.read_bytes()).hexdigest()
    frozen_scores = [json.loads(line) for line in scores_path.read_text().splitlines() if line.strip()]
    if len(frozen_scores) != len(items):
        print(f"REFUSING: {len(frozen_scores)} frozen scores != {len(items)} eval items", file=sys.stderr)
        return 2
    for it, fs in zip(items, frozen_scores, strict=True):
        if it["item_id"] != fs["source_row_id"]:
            print(f"REFUSING: order mismatch {it['item_id']} != {fs['source_row_id']}", file=sys.stderr)
            return 2

    print(f"{len(items)} CONSTRUCTED eval items (sha256 confirmed), frozen scores sha256 {scores_sha}")
    print(f"Backend: {BASE_URL}, concurrency 1, {INTER_CALL_DELAY_S * 1000:.0f}ms inter-call delay")

    meta = RunMetadata(
        script_path=Path(__file__), base_url=BASE_URL, container_name=JUDGE_CONTAINER_NAME,
        concurrency_description="concurrency 1 (sequential synchronous calls, no threading/asyncio import)",
        delay_s=INTER_CALL_DELAY_S,
    )
    meta.start()

    house = HouseJudge(
        tau=TAU, statute_chars=STATUTE_CHARS, base_url=BASE_URL, max_tokens=MAX_TOKENS,
        top_logprobs=TOP_LOGPROBS, timeout_s=60,
    )
    decoder = VLLMDecoder(base_url=BASE_URL, timeout_s=60)
    print(f"house model: {house.model}  typed model: {decoder.model}")

    wall_times: list[float] = []

    def _timed(fn: Any, *a: Any, **kw: Any) -> Any:
        t0 = time.monotonic()
        res = fn(*a, **kw)
        dt = time.monotonic() - t0
        if len(wall_times) >= _LATENCY_WARMUP:
            median = statistics.median(wall_times[-20:])
            if dt > median * _LATENCY_MULTIPLE:
                raise LatencyDegraded(f"call took {dt:.2f}s, > {_LATENCY_MULTIPLE}x rolling median {median:.2f}s")
        wall_times.append(dt)
        time.sleep(INTER_CALL_DELAY_S)
        return res

    raw_rows: list[dict[str, Any]] = []
    try:
        for i, item in enumerate(items):
            req = JudgeRequest(
                contract_id=item["contract_id"], element=item["element_desc"], is_denial=False,
                statute=item["statute"], narrative=item["narrative"],
                facts=tuple((f["id"], f["text"]) for f in item["facts"]),
            )
            prompt = build_house_prompt(req, statute_chars=STATUTE_CHARS)
            res_free = _timed(house._complete, prompt)  # noqa: SLF001
            res_typed = _timed(decoder.complete, prompt, max_tokens=MAX_TOKENS, temperature=0.0, logprobs=TOP_LOGPROBS)
            raw_rows.append(
                {
                    "item_id": item["item_id"], "element_id": item["element_id"], "contract_id": item["contract_id"],
                    "partition": item["partition"], "gold_status": item["gold_status"],
                    "frozen_p_established": frozen_scores[i]["p_established"],
                    "free_text": {"text": res_free.text, "top_logprobs": res_free.top_logprobs},
                    "typed": {"text": res_typed.text, "top_logprobs": res_typed.top_logprobs},
                }
            )
            if (i + 1) % 100 == 0:
                print(f"  {i + 1}/{len(items)}")
    except LatencyDegraded as e:
        print(f"ABORTING: {e}", file=sys.stderr)
        print(f"completed {len(raw_rows)}/{len(items)} rows before stopping", file=sys.stderr)

    raw_path = results_dir / "t2_harness_raw_outputs.jsonl"
    with raw_path.open("w") as f:
        for r in raw_rows:
            f.write(json.dumps(r) + "\n")
    raw_sha = hashlib.sha256(raw_path.read_bytes()).hexdigest()
    print()
    print(f"RAW OUTPUTS SEALED: {raw_path}, sha256 {raw_sha}, {len(raw_rows)} rows")
    print("This sha must reach R2 before any scoring pass, per the sealed prereg.")

    meta.finish(extra={"raw_output_sha256": raw_sha, "n_rows": len(raw_rows)})
    meta_path = results_dir / "t2_harness_inference_RUN-METADATA.json"
    meta.write(meta_path)
    print(f"RUN-METADATA written: {meta_path}")

    # Lead-2's protocol note (2026-09-24): no aggregate/headline numbers computed here, even as a "quick
    # sanity check" -- everything downstream of the raw seal, including the free-arm vs frozen-score
    # agreement byproduct, waits for R2's sign on this sha and runs as its own separate scoring pass.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
