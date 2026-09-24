"""T1 pass bar, part (c') (Lead-2, 2026-09-24): does (b)'s 0.06 max|delta p| come from the backend, or from
something the typed path actually does differently? Two SEPARATE `HouseJudge` instances (separate ChatClient
objects, separate connections), called in EXACTLY (b)'s pattern and order -- judge_1 then judge_2, per prompt,
sequential, concurrency 1 -- over all 279 live prompts.

If this reproduces (b)'s ~0.06 max|delta p| with 0 flips, the gap is backend-side (two independent
connections asking the identical question don't get bit-identical logits from vLLM) and typed_layer_parity_e2e
part (b)'s result already sits at that floor. If this comes out near 0, something about the typed path itself
differs and needs a second look before T1 passes.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from pravrudhi.application.nyaya_judges import HouseJudge, p_established_from_top_logprobs, parse_house_fact_id  # noqa: E402

# No host path is committed here. Set PRAVRUDHI_T1_PARITY_PROMPTS to the 279-prompt file; the script refuses
# with a clear message when unset, and hash-checks whatever it points to before using it either way.
EXPECTED_SHA = "d56c449f9332f22a85176b1008974c1f147a7b8d45e4f5cea0e5ed08a01935cf"
BASE_URL = "http://127.0.0.1:8110/v1"
TAU = 0.74
MAX_TOKENS = 30
TOP_LOGPROBS = 20


def main() -> int:
    env_path = os.environ.get("PRAVRUDHI_T1_PARITY_PROMPTS")
    if not env_path:
        print("REFUSING: PRAVRUDHI_T1_PARITY_PROMPTS is not set (no host-path default)", file=sys.stderr)
        return 2
    prompts_path = Path(env_path)
    digest = hashlib.sha256(prompts_path.read_bytes()).hexdigest()
    if digest != EXPECTED_SHA:
        print(f"REFUSING: {prompts_path} sha256 {digest} != expected {EXPECTED_SHA}", file=sys.stderr)
        return 2
    rows = [json.loads(line) for line in prompts_path.read_text().splitlines() if line.strip()]
    print(f"{len(rows)} prompts, sha256 confirmed. Backend: {BASE_URL}, concurrency 1.")

    # Two independent instances -- separate ChatClient, separate connection -- same construction as (b)'s
    # `house` object, built twice.
    judge_1 = HouseJudge(
        tau=TAU, statute_chars=600, base_url=BASE_URL, max_tokens=MAX_TOKENS, top_logprobs=TOP_LOGPROBS, timeout_s=60
    )
    judge_2 = HouseJudge(
        tau=TAU, statute_chars=600, base_url=BASE_URL, max_tokens=MAX_TOKENS, top_logprobs=TOP_LOGPROBS, timeout_s=60
    )
    print(f"judge_1 model: {judge_1.model}  judge_2 model: {judge_2.model}")

    flips: list[dict[str, object]] = []
    max_abs_delta = 0.0
    large_deltas: list[dict[str, object]] = []
    for i, row in enumerate(rows):
        prompt = row["prompt"]
        res_1 = judge_1._complete(prompt)  # noqa: SLF001 -- exactly (b)'s call shape, on two separate instances
        res_2 = judge_2._complete(prompt)  # noqa: SLF001

        p_1 = p_established_from_top_logprobs(res_1.top_logprobs[0]) if res_1.top_logprobs else None
        p_2 = p_established_from_top_logprobs(res_2.top_logprobs[0]) if res_2.top_logprobs else None
        if p_1 is None or p_2 is None:
            continue
        delta = abs(p_1 - p_2)
        if delta > 1e-6:
            large_deltas.append({"row": i, "id": row.get("id"), "delta": delta, "p_1": p_1, "p_2": p_2})
        max_abs_delta = max(max_abs_delta, delta)
        status_1 = "established" if p_1 >= TAU else "not_established"
        status_2 = "established" if p_2 >= TAU else "not_established"
        fid_1 = parse_house_fact_id(res_1.text) if status_1 == "established" else None
        fid_2 = parse_house_fact_id(res_2.text) if status_2 == "established" else None
        if status_1 != status_2 or fid_1 != fid_2:
            flips.append({"row": i, "id": row.get("id"), "p_1": p_1, "p_2": p_2})
        if (i + 1) % 50 == 0:
            print(f"  {i + 1}/{len(rows)}  max|dp| so far: {max_abs_delta:.3e}  flips: {len(flips)}")

    print()
    print(f"(c') HouseJudge vs HouseJudge, 2 separate instances, (b)'s call pattern: {len(rows)} prompts")
    print(f"     flips: {len(flips)}")
    print(f"     max|dp|: {max_abs_delta:.3e}")
    result = {
        "n_prompts": len(rows),
        "flips": flips,
        "max_abs_delta_p": max_abs_delta,
        "large_deltas": sorted(large_deltas, key=lambda d: -d["delta"])[:10],  # type: ignore[arg-type,return-value]
        "model": judge_1.model,
        "prompts_sha256": digest,
    }
    out_path = ROOT / "scripts" / "typed_layer_parity_c_prime_result.json"
    out_path.write_text(json.dumps(result, indent=2))
    print(f"result: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
