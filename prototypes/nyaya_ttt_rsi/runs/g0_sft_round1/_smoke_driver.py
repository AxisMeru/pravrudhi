"""One-off smoke driver (run dir, not part of the module contract): loads the
just-saved g0 1.13B checkpoint via generate_megatron.py, generates for 3
held-out grounded prompts built through evaluate.py's own render path
(retrieve_and_build_prompt -> render_prompt -> build_candidates), prints
candidate NLLs for one item, and smoke-tests lora_megatron.inject_lora_generic
(module count + before/after-injection forward-pass identity at B=0)."""

import json
import sys
import time

sys.path.insert(0, "/lab")
sys.path.insert(0, "/lab/prototypes/nyaya_ttt_rsi/g0")

import torch  # noqa: E402

from prototypes.nyaya_ttt_rsi import evaluate, retrieval as retrieval_mod  # noqa: E402
import generate_megatron  # noqa: E402
import lora_megatron  # noqa: E402

CKPT = "/lab/prototypes/nyaya_ttt_rsi/runs/g0_sft_round1/final.pt"
CONFIG = "/trackB/configs/train/nemotron_h_1b.yaml"

t0 = time.perf_counter()
model, tok = generate_megatron.load_model(CKPT, device="cuda:0", config_path=CONFIG)
t1 = time.perf_counter()
print(json.dumps({"load_wall_seconds": t1 - t0}), flush=True)

store = retrieval_mod.PassageStore.from_law_files(evaluate.DEFAULT_TRAIN, evaluate.DEFAULT_HELDOUT)
heldout_items = evaluate.load_jsonl(evaluate.DEFAULT_HELDOUT)
items = heldout_items[:3]

import os  # noqa: E402

LORA_ONLY = os.environ.get("LORA_ONLY") == "1"

built = []
for item in (items if not LORA_ONLY else items[:1]):
    prompt, passages_used, prompt_bytes, dropped = evaluate.retrieve_and_build_prompt(
        store, item["prompt"]
    )
    built.append((item, prompt, passages_used, prompt_bytes, dropped))

if not LORA_ONLY:
    # --- 1) generate for the 3 held-out prompts ---
    gen_texts = generate_megatron.generate(
        model, tok, [b[1] for b in built], max_new_tokens=48, stop=["\n\n"]
    )
    gen_results = [
        {"id": item["id"], "kind": item["kind"], "prompt_bytes": pb, "dropped_a_passage": dropped,
         "generated": text}
        for (item, _p, _pu, pb, dropped), text in zip(built, gen_texts, strict=True)
    ]
    print(json.dumps({"generate_smoke": gen_results}, indent=2, ensure_ascii=False), flush=True)

    # --- 2) candidate NLLs for ONE item (the first) ---
    item0, prompt0, passages0, pb0, dropped0 = built[0]
    candidates0 = evaluate.build_candidates(passages0, item0["kind"])
    scored = evaluate.score_candidates(model, tok, prompt0, candidates0, by="total")
    print(
        json.dumps(
            {
                "candidate_nll_smoke": {
                    "id": item0["id"],
                    "kind": item0["kind"],
                    "candidates": [text for text, _src in candidates0],
                    "mean_nlls": scored["mean_nlls"],
                    "total_nlls": scored["total_nlls"],
                    "best_idx": scored["best_idx"],
                    "best_candidate": candidates0[scored["best_idx"]][0],
                    "margin": scored["margin"],
                }
            },
            indent=2,
            ensure_ascii=False,
        ),
        flush=True,
    )

# --- 3) lora_megatron.inject_lora_generic smoke: module count + B=0 identity ---
probe_ids = [[1, 2, 3, 4, 5, 6, 7, 8]]
x = torch.tensor(probe_ids, dtype=torch.long, device="cuda:0")
zeros = torch.zeros_like(x)
with torch.no_grad():
    logits_before = model(x, zeros, zeros).clone()

wrapped = lora_megatron.inject_lora_generic(model, target_regex=".*", r=8, alpha=16)
n_wrapped = len(wrapped)

with torch.no_grad():
    logits_after = model(x, zeros, zeros).clone()

identical = torch.equal(logits_before, logits_after)
max_abs_diff = float((logits_before - logits_after).abs().max().item())

print(
    json.dumps(
        {
            "lora_smoke": {
                "n_wrapped_modules": n_wrapped,
                "wrapped_module_names_sample": [type(m.base).__name__ for m in wrapped[:5]],
                "logits_identical_before_after_injection": identical,
                "max_abs_diff": max_abs_diff,
            }
        },
        indent=2,
    ),
    flush=True,
)
