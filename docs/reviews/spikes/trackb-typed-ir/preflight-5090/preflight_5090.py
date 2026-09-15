"""P0-style preflight on the RTX 5090 for an HF 4B base (reviewer spike, 2026-09-15).

Measures, in one process, writing JSON:
  1. load wall-clock + resident VRAM (bf16)
  2. sequence-NLL scoring: gold passage vs 3 distractors on held-out law_citation_retrieval items
     (reimplements Track B's sequence_nll contract for AutoModelForCausalLM)
  3. LoRA r=64 one-step training memory at seq 2048, micro-batch 2, grad checkpointing
  4. resumable checkpoint save/restore continuity
Not a benchmark of the model; a measurement of the machine + primitive.
"""
from __future__ import annotations

import argparse
import json
import random
import time
from pathlib import Path

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer


def sequence_nll(model, tok, prompt: str, continuation: str) -> float:
    """Mean NLL (nats) over continuation tokens only; prompt is context. Same contract as
    prabhasa-samskrutam/src/prabhasa/application/retrieval/sequence_nll.py, HF-native."""
    device = next(model.parameters()).device
    p_ids = tok(prompt, add_special_tokens=False)["input_ids"]
    c_ids = tok(continuation, add_special_tokens=False)["input_ids"]
    ids = torch.tensor([p_ids + c_ids], device=device)
    with torch.no_grad():
        logits = model(ids).logits[0]
    targets = ids[0, 1:]
    preds = logits[:-1]
    j = max(len(p_ids) - 1, 0)
    if j >= targets.shape[0]:
        return 0.0
    return float(torch.nn.functional.cross_entropy(preds[j:].float(), targets[j:], reduction="mean").item())


def load_corpus(paths: list[Path]) -> dict[str, dict]:
    docs: dict[str, dict] = {}
    for p in paths:
        d = json.loads(p.read_text())
        for doc in d["documents"]:
            docs[doc.get("id") or f"{doc.get('act')}/{doc.get('section')}"] = doc
    return docs


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="Qwen/Qwen3-4B")
    ap.add_argument("--heldout", default="/home/ss/projects/prabhasa-samskrutam/data/eval/law_qa_heldout_v3.jsonl")
    ap.add_argument("--corpus-dir", default="/home/ss/projects/pravrudhi/research/nyaya/corpus")
    ap.add_argument("--n-items", type=int, default=48)
    ap.add_argument("--seq", type=int, default=2048)
    ap.add_argument("--out", default="preflight_5090.json")
    ap.add_argument("--skip-train", action="store_true")
    ap.add_argument("--ckpt-dir", default="/home/ss/fusion-project/prabhasa-nyaya/checkpoints/spike-preflight-5090/ckpt_step")
    args = ap.parse_args()
    random.seed(7)
    rep: dict = {"model": args.model, "device": torch.cuda.get_device_name(0), "seq": args.seq}

    t0 = time.time()
    tok = AutoTokenizer.from_pretrained(args.model)
    model = AutoModelForCausalLM.from_pretrained(args.model, torch_dtype=torch.bfloat16, device_map="cuda")
    model.eval()
    torch.cuda.synchronize()
    rep["load_s"] = round(time.time() - t0, 1)
    rep["vram_after_load_gib"] = round(torch.cuda.memory_allocated() / 2**30, 2)
    rep["n_params"] = sum(p.numel() for p in model.parameters())

    # 2. sequence-NLL selection on held-out citation items
    docs = load_corpus(sorted(Path(args.corpus_dir).glob("*.json")))
    by_source = {}
    for d in docs.values():
        key = f"{d.get('act')}/{d.get('section')}"
        by_source[key] = d
    items = [json.loads(l) for l in open(args.heldout)]
    items = [it for it in items if it["kind"] == "law_citation_retrieval"]
    random.shuffle(items)
    scored = 0
    correct = 0
    tokens_scored = 0
    t1 = time.time()
    doc_list = list(docs.values())
    for it in items[: args.n_items]:
        gold = None
        for d in doc_list:
            if d.get("act") == it["act"] and str(d.get("section")).split()[-1] == str(it["section"]).split()[-1]:
                gold = d
                break
        if gold is None:
            continue
        distractors = random.sample([d for d in doc_list if d is not gold and d.get("act") == it["act"]], 3)
        cands = [gold] + distractors
        random.shuffle(cands)
        prompt = f"Question: {it['prompt']}\nAnswer with the provision text.\nAnswer:"
        nlls = []
        for c in cands:
            text = (c.get("text") or c.get("body") or "")[:1200]
            nlls.append(sequence_nll(model, tok, prompt, " " + text))
            tokens_scored += len(tok(text, add_special_tokens=False)["input_ids"])
        best = cands[int(min(range(len(nlls)), key=lambda i: nlls[i]))]
        scored += 1
        correct += int(best is gold)
    torch.cuda.synchronize()
    dt = time.time() - t1
    rep["nll_items"] = scored
    rep["nll_select_gold_at_4"] = round(correct / scored, 4) if scored else None
    rep["nll_tokens_per_s"] = round(tokens_scored / dt, 1) if dt else None
    rep["vram_peak_inference_gib"] = round(torch.cuda.max_memory_allocated() / 2**30, 2)

    if args.skip_train:
        Path(args.out).write_text(json.dumps(rep, indent=1))
        print(json.dumps(rep, indent=1))
        return

    # 3. LoRA one-step memory
    from peft import LoraConfig, get_peft_model

    torch.cuda.reset_peak_memory_stats()
    model.gradient_checkpointing_enable()
    model.enable_input_require_grads()
    cfg = LoraConfig(r=64, lora_alpha=128, lora_dropout=0.05, bias="none", task_type="CAUSAL_LM",
                     target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"])
    pm = get_peft_model(model, cfg)
    pm.train()
    trainable = sum(p.numel() for p in pm.parameters() if p.requires_grad)
    rep["lora_trainable_params"] = trainable
    opt = torch.optim.AdamW([p for p in pm.parameters() if p.requires_grad], lr=1e-4)
    x = torch.randint(100, 20000, (2, args.seq), device="cuda")
    losses = []
    t2 = time.time()
    steps = 4
    for s in range(steps):
        out = pm(input_ids=x, labels=x)
        out.loss.backward()
        opt.step()
        opt.zero_grad(set_to_none=True)
        losses.append(float(out.loss.item()))
    torch.cuda.synchronize()
    rep["lora_steps"] = steps
    rep["lora_step_s"] = round((time.time() - t2) / steps, 2)
    rep["lora_peak_vram_gib"] = round(torch.cuda.max_memory_allocated() / 2**30, 2)
    rep["lora_losses"] = [round(v, 4) for v in losses]

    # 4. resumable checkpoint continuity
    ck = Path(args.ckpt_dir)  # /tmp is a small tmpfs; house rule 16 names the 5090 store
    pm.save_pretrained(str(ck))
    torch.save(opt.state_dict(), ck / "opt.pt")
    pm.eval()  # dropout off: the first run measured 'before' in train mode (LoRA dropout 0.05) and failed the ±1e-3 check
    before = sequence_nll(pm, tok, "The Indian Penal Code section 405 defines", " criminal breach of trust.")
    from peft import PeftModel

    base = AutoModelForCausalLM.from_pretrained(args.model, torch_dtype=torch.bfloat16, device_map="cuda")
    pm2 = PeftModel.from_pretrained(base, str(ck))
    pm2.eval()
    after = sequence_nll(pm2, tok, "The Indian Penal Code section 405 defines", " criminal breach of trust.")
    rep["ckpt_restore_nll_before_after"] = [round(before, 6), round(after, 6)]
    rep["ckpt_restore_ok"] = abs(before - after) < 1e-3
    Path(args.out).write_text(json.dumps(rep, indent=1))
    print(json.dumps(rep, indent=1))


if __name__ == "__main__":
    main()
