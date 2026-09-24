"""T1 pass bar (docs/decisions/TYPED-LAYER-PLAN-2026-09-24.md): on the 279-prompt judge parity set
(tmp_scratch/lead2/eval14b/prompts.jsonl, sha256 d56c449f...), the typed path vs current HouseJudge on the
SAME 5090 vLLM backend gives 0 decision flips and max|delta p| <= 1e-6.

One completion call per prompt (temperature 0, the model's trained shape, max_tokens/top_logprobs/tau exactly
`configs/nyaya_agent.yaml`'s production values), then BOTH scoring paths read the SAME `CompletionResult`:
`nyaya_judges.p_established_from_top_logprobs` (today's production math) and
`pravrudhi.application.typed.decoder.score_decision` (T1's re-expression) over the identical bool field
`TypedHouseJudge` uses. `parse_house_fact_id` is literally the same imported function on both sides, so a
fact-id disagreement is not possible by construction; this script's real question is whether the two
probability computations agree on live model logprobs, not just on the unit-test fixture range.

Usage: PYTHONPATH=src:pravrudhi_kernel/src .venv/bin/python scripts/typed_layer_parity.py
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from pravrudhi.application.nyaya_judges import p_established_from_top_logprobs, parse_house_fact_id  # noqa: E402
from pravrudhi.application.typed.decoder import score_decision  # noqa: E402
from pravrudhi.application.typed.house_judge import _STATUS_FIELD  # noqa: E402
from pravrudhi.models.openai_compat import ChatClient  # noqa: E402

PROMPTS = Path("/home/ss/fusion-project/tmp_scratch/lead2/eval14b/prompts.jsonl")
EXPECTED_SHA = "d56c449f9332f22a85176b1008974c1f147a7b8d45e4f5cea0e5ed08a01935cf"

BASE_URL = "http://127.0.0.1:8110/v1"
TAU = 0.74
MAX_TOKENS = 30
TOP_LOGPROBS = 20


def main() -> int:
    digest = hashlib.sha256(PROMPTS.read_bytes()).hexdigest()
    if digest != EXPECTED_SHA:
        print(f"REFUSING: {PROMPTS} sha256 {digest} != expected {EXPECTED_SHA}", file=sys.stderr)
        return 2
    rows = [json.loads(line) for line in PROMPTS.read_text().splitlines() if line.strip()]
    print(f"{len(rows)} prompts, sha256 confirmed. Backend: {BASE_URL}")

    client = ChatClient(base_url=BASE_URL, model="", timeout_s=60)
    listed = client.list_models()
    if not listed:
        print(f"REFUSING: {BASE_URL}/models lists no model", file=sys.stderr)
        return 2
    client.model = listed[0]
    print(f"model: {client.model}")

    flips: list[dict[str, object]] = []
    max_abs_delta = 0.0
    n_no_evidence = 0
    for i, row in enumerate(rows):
        res = client.complete(row["prompt"], max_tokens=MAX_TOKENS, temperature=0.0, logprobs=TOP_LOGPROBS)
        if not res.top_logprobs:
            n_no_evidence += 1
            continue
        p_old = p_established_from_top_logprobs(res.top_logprobs[0])
        p_new = score_decision(res, _STATUS_FIELD)["true"]
        delta = abs(p_old - p_new)
        max_abs_delta = max(max_abs_delta, delta)

        status_old = "established" if p_old >= TAU else "not_established"
        status_new = "established" if p_new >= TAU else "not_established"
        fact_id_old = parse_house_fact_id(res.text) if status_old == "established" else None
        fact_id_new = parse_house_fact_id(res.text) if status_new == "established" else None
        if status_old != status_new or fact_id_old != fact_id_new:
            flips.append(
                {"row": i, "id": row.get("id"), "p_old": p_old, "p_new": p_new,
                 "status_old": status_old, "status_new": status_new}
            )
        if (i + 1) % 50 == 0:
            print(f"  {i + 1}/{len(rows)} (max|delta p| so far: {max_abs_delta:.3e}, flips so far: {len(flips)})")

    print()
    print(f"rows scored: {len(rows) - n_no_evidence}/{len(rows)} (no-evidence rows skipped: {n_no_evidence})")
    print(f"decision flips: {len(flips)}")
    print(f"max|delta p|: {max_abs_delta:.3e}")
    out = {
        "n_prompts": len(rows),
        "n_no_evidence": n_no_evidence,
        "n_flips": len(flips),
        "max_abs_delta_p": max_abs_delta,
        "flips": flips,
        "prompts_sha256": digest,
        "backend": BASE_URL,
        "model": client.model,
        "tau": TAU,
    }
    out_path = ROOT / "scripts" / "typed_layer_parity_result.json"
    out_path.write_text(json.dumps(out, indent=2))
    print(f"result: {out_path}")

    passed = len(flips) == 0 and max_abs_delta <= 1e-6
    print("PASS" if passed else "FAIL")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
