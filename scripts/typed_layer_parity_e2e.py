"""T1 pass bar, parts (b) and (c) (Lead-2, 2026-09-24): typed_layer_parity.py's same-CompletionResult check
(part a) only proves the two probability formulas are algebraically equivalent -- it cannot catch the typed
path building a DIFFERENT request. This script runs each judge through its own real network path (its own
ChatClient, its own model-id resolution, its own fallback wiring) and:

(b) end-to-end: HouseJudge._complete(prompt) and TypedHouseJudge.decoder.complete(prompt, ...) each make
    their OWN call, for all 279 prompts. `ChatClient.complete` is monkeypatched (call-through, behaviour
    unchanged) to record every (base_url, model, prompt, max_tokens, temperature, logprobs, stop) it is
    invoked with, so the two paths' request bodies are diffed byte-for-byte on a sample.
(c) noise floor: HouseJudge._complete(prompt) called TWICE (its own two independent calls) for a sample of
    prompts, so a nonzero delta-p in (b) can be attributed to vLLM's own batch/kernel nondeterminism at
    temperature 0 rather than to a difference between the two code paths.

Pass: (b) 0 decision flips, max|delta p| no larger than (c)'s max|delta p|, and every diffed request body
identical except for the prompt reuse count (both paths pass the same args into the same complete()).
Concurrency 1 throughout, per Lead-2's instruction.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from pravrudhi.application.nyaya_judges import HouseJudge, p_established_from_top_logprobs, parse_house_fact_id  # noqa: E402
from pravrudhi.application.typed.decoder import VLLMDecoder, score_decision  # noqa: E402
from pravrudhi.application.typed.house_judge import _STATUS_FIELD, TypedHouseJudge  # noqa: E402
from pravrudhi.models.openai_compat import ChatClient  # noqa: E402

# No host path is committed here. Set PRAVRUDHI_T1_PARITY_PROMPTS to the 279-prompt file; the script refuses
# with a clear message when unset, and hash-checks whatever it points to before using it either way.
EXPECTED_SHA = "d56c449f9332f22a85176b1008974c1f147a7b8d45e4f5cea0e5ed08a01935cf"
BASE_URL = "http://127.0.0.1:8110/v1"
TAU = 0.74
MAX_TOKENS = 30
TOP_LOGPROBS = 20
SAMPLE_EVERY = 20  # request-body diff sample: every 20th row (~14 of 279) -- enough to catch a shape mismatch


_calls: list[dict[str, Any]] = []
_orig_complete = ChatClient.complete


def _recording_complete(self: ChatClient, prompt: str, **kw: Any) -> Any:
    _calls.append({"base_url": self.base_url, "model": self.model, "prompt_len": len(prompt), **kw})
    return _orig_complete(self, prompt, **kw)


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

    ChatClient.complete = _recording_complete  # type: ignore[method-assign]
    try:
        house = HouseJudge(
            tau=TAU, statute_chars=600, base_url=BASE_URL, max_tokens=MAX_TOKENS, top_logprobs=TOP_LOGPROBS, timeout_s=60
        )
        decoder = VLLMDecoder(base_url=BASE_URL, timeout_s=60)
        typed_house = TypedHouseJudge(
            tau=TAU, statute_chars=600, decoder=decoder, max_tokens=MAX_TOKENS, top_logprobs=TOP_LOGPROBS
        )
        print(f"house model: {house.model}  typed model: {typed_house.decoder.model}")

        # -- (b) end-to-end: each judge's own call, per prompt -------------------------------------------
        flips_b: list[dict[str, object]] = []
        max_abs_delta_b = 0.0
        body_mismatches: list[dict[str, object]] = []
        large_deltas: list[dict[str, object]] = []
        for i, row in enumerate(rows):
            prompt = row["prompt"]
            n_before = len(_calls)
            res_house = house._complete(prompt)  # noqa: SLF001 -- intentional: HouseJudge's own real transport
            res_typed = typed_house.decoder.complete(prompt, max_tokens=MAX_TOKENS, temperature=0.0, logprobs=TOP_LOGPROBS)

            if i % SAMPLE_EVERY == 0:
                a, b = _calls[n_before], _calls[n_before + 1]
                diff = {k: (a[k], b[k]) for k in a if a.get(k) != b.get(k) and k != "prompt_len"}
                if diff:
                    body_mismatches.append({"row": i, "diff": diff})

            p_house = p_established_from_top_logprobs(res_house.top_logprobs[0]) if res_house.top_logprobs else None
            p_typed = score_decision(res_typed, _STATUS_FIELD)["true"] if res_typed.top_logprobs else None
            if p_house is None or p_typed is None:
                continue
            delta = abs(p_house - p_typed)
            if delta > 1e-6:
                large_deltas.append(
                    {
                        "row": i, "id": row.get("id"), "delta": delta, "p_house": p_house, "p_typed": p_typed,
                        "text_house": res_house.text, "text_typed": res_typed.text,
                        "top_house": res_house.top_logprobs[0] if res_house.top_logprobs else None,
                        "top_typed": res_typed.top_logprobs[0] if res_typed.top_logprobs else None,
                    }
                )
            max_abs_delta_b = max(max_abs_delta_b, delta)
            status_house = "established" if p_house >= TAU else "not_established"
            status_typed = "established" if p_typed >= TAU else "not_established"
            fid_house = parse_house_fact_id(res_house.text) if status_house == "established" else None
            fid_typed = parse_house_fact_id(res_typed.text) if status_typed == "established" else None
            if status_house != status_typed or fid_house != fid_typed:
                flips_b.append({"row": i, "id": row.get("id"), "p_house": p_house, "p_typed": p_typed})
            if (i + 1) % 50 == 0:
                print(f"  (b) {i + 1}/{len(rows)}  max|dp| so far: {max_abs_delta_b:.3e}  flips: {len(flips_b)}")

        # -- (c) noise floor, back-to-back: HouseJudge vs itself, same prompt twice in a row --------------
        # Tends to hit vLLM's prefix cache on the repeat -- a TIGHT floor, not necessarily a fair one against
        # (b), where house/typed calls for the SAME prompt are separated by a call for a DIFFERENT prompt.
        max_abs_delta_c = 0.0
        n_noise_sampled = 0
        for i, row in enumerate(rows):
            if i % SAMPLE_EVERY != 0:
                continue
            r1 = house._complete(row["prompt"])  # noqa: SLF001
            r2 = house._complete(row["prompt"])  # noqa: SLF001
            if not r1.top_logprobs or not r2.top_logprobs:
                continue
            p1 = p_established_from_top_logprobs(r1.top_logprobs[0])
            p2 = p_established_from_top_logprobs(r2.top_logprobs[0])
            max_abs_delta_c = max(max_abs_delta_c, abs(p1 - p2))
            n_noise_sampled += 1

        # -- (c2) noise floor, interleaved: HouseJudge vs itself, with an unrelated prompt in between ------
        # Emulates (b)'s own access pattern exactly (house_i, [other call], house_i again) using ONLY
        # HouseJudge -- isolates whether (b)'s gap comes from batch-composition-dependent backend
        # nondeterminism (present here too, since nothing about this loop differs from typed) or from an
        # actual difference between the two code paths (absent here by construction: same object, same call).
        max_abs_delta_c2 = 0.0
        n_noise_sampled_c2 = 0
        for i, row in enumerate(rows):
            if i % SAMPLE_EVERY != 0 or i + 1 >= len(rows):
                continue
            r1 = house._complete(row["prompt"])  # noqa: SLF001
            house._complete(rows[i + 1]["prompt"])  # noqa: SLF001 -- the interleaving call, result unused
            r2 = house._complete(row["prompt"])  # noqa: SLF001
            if not r1.top_logprobs or not r2.top_logprobs:
                continue
            p1 = p_established_from_top_logprobs(r1.top_logprobs[0])
            p2 = p_established_from_top_logprobs(r2.top_logprobs[0])
            max_abs_delta_c2 = max(max_abs_delta_c2, abs(p1 - p2))
            n_noise_sampled_c2 += 1
    finally:
        ChatClient.complete = _orig_complete  # type: ignore[method-assign]

    print()
    print(f"(b) end-to-end: {len(rows)} prompts, flips={len(flips_b)}, max|dp|={max_abs_delta_b:.3e}")
    print(f"(b) request-body sample: {len(body_mismatches)} mismatches out of {len(range(0, len(rows), SAMPLE_EVERY))} sampled")
    print(f"(c) noise floor, back-to-back (n={n_noise_sampled}): max|dp|={max_abs_delta_c:.3e}")
    print(f"(c2) noise floor, interleaved, same object (n={n_noise_sampled_c2}): max|dp|={max_abs_delta_c2:.3e}")

    floor = max(max_abs_delta_c, max_abs_delta_c2)
    passed = len(flips_b) == 0 and max_abs_delta_b <= max(floor, 1e-9) and not body_mismatches
    result = {
        "n_prompts": len(rows),
        "b_flips": flips_b,
        "b_large_deltas": sorted(large_deltas, key=lambda d: -d["delta"])[:10],  # type: ignore[arg-type,return-value]
        "b_max_abs_delta_p": max_abs_delta_b,
        "c2_max_abs_delta_p_interleaved_noise_floor": max_abs_delta_c2,
        "c2_n_sampled": n_noise_sampled_c2,
        "b_body_mismatches": body_mismatches,
        "c_max_abs_delta_p_noise_floor": max_abs_delta_c,
        "c_n_sampled": n_noise_sampled,
        "house_model": house.model,
        "typed_model": typed_house.decoder.model,
        "passed": passed,
    }
    out_path = results_dir / "typed_layer_parity_e2e_result.json"
    out_path.write_text(json.dumps(result, indent=2))
    print(f"result: {out_path}")
    print("PASS" if passed else "FAIL")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
