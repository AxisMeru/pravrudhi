"""Conditions B/C/D evaluation over the law-QA held-out set, plus paired comparison.

Condition B: grounded prompt (retrieval.build_grounded_prompt), frozen model.
Condition C: B + ephemeral test-time-training (ttt.adapt) on the retrieved passage
text only, reset (ttt.restore) after every query; a regression-probe gate
(gate.decide) decides whether the post-adaptation answer is kept or the
pre-adaptation answer is served instead.
Condition D: identical machinery to C, except the LoRA modules start each query
from a *persistent* snapshot produced by loop.py's gated consolidation, rather
than from the zero-initialized state -- see loop.py.

Model-and-torch-dependent calls (generation, NLL scoring, LoRA adapt/snapshot/
restore, Track B's score_law_qa) are wired through the small `_generate` /
`_sequence_nll` / `_adapt` / `_snapshot` / `_restore` / `_merged_delta_norm` /
`_score_law_qa` / `_citation_correct` / `_is_abstention_answer` module-level
functions below, each of which does its heavy import (torch, or Track B's own
`/trackB` path) lazily, inside the function body. This mirrors ttt.py's own
lazy-import-of-model_io pattern and, more importantly, means this module can be
imported and unit-tested on the host (no torch, no /trackB mount) by
monkeypatching those names -- see tests/test_evaluate.py.

Critical model constraint (370M byte-level checkpoint, trained at seq_len 512):
grounded prompts stay short -- k=3 passages, each passage truncated to
<= MAX_PASSAGE_BYTES at a sentence/space boundary, total prompt logged and
checked against MAX_PROMPT_BYTES. The ephemeral TTT text (concatenated
retrieved passages) is separately capped at ADAPT_MAX_BYTES so it fits in one
forward pass at the model's trained sequence length.
"""

from __future__ import annotations

import dataclasses
import json
import sys
import time
from pathlib import Path
from typing import Any, Callable

from . import retrieval as retrieval_mod
from . import stats

TRACKB_ROOT = Path("/trackB")
DEFAULT_CHECKPOINT = Path("/trackB-local/m7/m7_retry_checkpoint.pt")
DEFAULT_HELDOUT = TRACKB_ROOT / "data" / "eval" / "law_qa_heldout_v3.jsonl"
DEFAULT_TRAIN = TRACKB_ROOT / "data" / "sft" / "law_v3_train.jsonl"
DEFAULT_PROBE_GENERAL = TRACKB_ROOT / "data" / "sft" / "instruct_v1_val.jsonl"

# LoRA targets: attention blocks' q/o projections ONLY -- see the "mamba mixer
# cannot be LoRA-wrapped" finding below. `linear_module_names()` reports both
# 21 Mamba2 blocks' `blocks.<i>.mixer.{in_proj,out_proj}` and 3 attention
# blocks' `blocks.<i>.mixer.{qkv,out_proj}`; the module contract asked for
# both, but wrapping a Mamba2 mixer's in_proj/out_proj in a LoRALinear crashes
# at inference: `mamba_ssm`'s fused CUDA path reads `self.out_proj.weight`
# directly (`mamba2.py`'s `forward`, `outproj_weight=self.out_proj.weight`)
# rather than calling `self.out_proj(x)`, so a LoRALinear wrapper (no
# `.weight` attribute) raises `AttributeError: 'LoRALinear' object has no
# attribute 'weight'`. `CausalSelfAttention.forward` (train_130m.py) calls
# `self.qkv(x)` / `self.out_proj(x)` as ordinary module calls, so only the 3
# attention blocks' q/o projections are actually LoRA-injectable with the
# `ttt.LoRALinear` wrapper as implemented. Filed as F6 in
# docs/research-spikes/2026-09-13-ttt-rsi-track-b/FIXES-FOR-MAIN-SESSIONS.md.
# The regex is built dynamically from `model_io.attention_block_indices`
# (see `attention_lora_target_regex`) rather than hardcoded, since which
# block indices use attention depends on `attention_every`.
LORA_TARGET_REGEX_TEMPLATE = r"blocks\.({idx})\.mixer\.(qkv|out_proj)$"
LORA_R = 8
LORA_ALPHA = 16

K_PASSAGES = 3
MAX_PASSAGE_BYTES = 350
MAX_PROMPT_BYTES = 1400
ADAPT_MAX_BYTES = 480  # concatenated retrieved-passage text fed to ttt.adapt
MAX_NEW_TOKENS_GROUNDED = 96
STOP_STRINGS = ["\n\n", "\nQuestion:"]

DEFAULT_TTT_CFG = {"steps": 4, "lr": 1e-3, "threshold": 0.15}

_ABSTAIN_KIND = "law_abstain"

# ---------------------------------------------------------------------------
# Lazy backends -- imported only when actually invoked, and the sole hook
# tests use to stub out torch/model/Track-B-script dependencies.
# ---------------------------------------------------------------------------


def _generate(model, tok, prompts, max_new_tokens, stop):
    from . import model_io

    return model_io.generate(model, tok, prompts, max_new_tokens=max_new_tokens, stop=stop)


def _sequence_nll(model, tok, prompt, continuation):
    from . import model_io

    return model_io.sequence_nll(model, tok, prompt, continuation)


def _tensor_nll_loss(model, tok, prompt: str, continuation: str):
    """Same math as `model_io.sequence_nll` (mean NLL over `continuation`
    bytes only), but returns a tensor still attached to the autograd graph.

    Needed because `ttt.adapt`'s default `loss_fn` (when none is passed)
    calls `model_io.sequence_nll(...)`, which returns a plain detached
    `float` (`.item()`), then re-wraps it with `torch.as_tensor` -- a leaf
    tensor with no `grad_fn`, so `loss.backward()` cannot flow gradients
    back into the LoRA parameters. Its `import model_io` (unqualified, not
    `from . import model_io`) also fails to resolve inside this package, so
    it silently falls through to a second, also-broken fallback that calls
    `model(input_ids=..., labels=...)` -- a HuggingFace-style signature this
    model's `forward(tokens, boundary, roles)` does not have. Filed as F7 in
    docs/research-spikes/2026-09-13-ttt-rsi-track-b/FIXES-FOR-MAIN-SESSIONS.md.
    `ttt.adapt` and `loop.consolidate` are therefore always called with this
    function as an explicit `loss_fn`, never the library default.
    """
    import torch

    device = next(model.parameters()).device
    prompt_ids = tok.encode(prompt)
    cont_ids = tok.encode(continuation)
    all_ids = prompt_ids + cont_ids
    if len(all_ids) < 2:
        return torch.zeros((), device=device, requires_grad=True)

    n_prompt = len(prompt_ids)
    tokens = torch.tensor([all_ids], dtype=torch.long, device=device)
    zeros = torch.zeros_like(tokens)
    with torch.autocast(device.type, dtype=torch.bfloat16, enabled=(device.type == "cuda")):
        logits = model(tokens, zeros, zeros)[0]

    targets = tokens[0, 1:]
    preds = logits[:-1]
    j_start = max(n_prompt - 1, 0)
    if j_start >= targets.shape[0]:
        return torch.zeros((), device=device, requires_grad=True)

    sel_preds = preds[j_start:].float()
    sel_targets = targets[j_start:]
    return torch.nn.functional.cross_entropy(sel_preds, sel_targets, reduction="mean")


def _adapt(model, tok, text, loras, steps, lr):
    from . import ttt as ttt_mod

    loss_fn = lambda m, t: _tensor_nll_loss(m, tok, "", t)
    return ttt_mod.adapt(model, tok, text, loras, steps=steps, lr=lr, loss_fn=loss_fn)


def _snapshot(loras):
    from . import ttt as ttt_mod

    return ttt_mod.snapshot(loras)


def _restore(loras, snap):
    from . import ttt as ttt_mod

    ttt_mod.restore(loras, snap)


def _merged_delta_norm(loras):
    from . import ttt as ttt_mod

    return ttt_mod.merged_delta_norm(loras)


def attention_lora_target_regex(model) -> str:
    from . import model_io

    indices = model_io.attention_block_indices(model)
    return LORA_TARGET_REGEX_TEMPLATE.format(idx="|".join(str(i) for i in indices))


def _inject_lora(model, target_regex=None, r=LORA_R, alpha=LORA_ALPHA):
    from . import ttt as ttt_mod

    if target_regex is None:
        target_regex = attention_lora_target_regex(model)
    return ttt_mod.inject_lora(model, target_regex, r=r, alpha=alpha)


def _save_lora_state(loras, path) -> None:
    import torch

    torch.save(_snapshot(loras), path)


def _load_lora_state(loras, path) -> None:
    import torch

    snap = torch.load(path, map_location="cpu", weights_only=True)
    _restore(loras, snap)


def _score_law_qa_module():
    sys.path.insert(0, str(TRACKB_ROOT / "scripts" / "eval"))
    import score_law_qa  # type: ignore[import-not-found]

    return score_law_qa


def _score_law_qa(heldout_path, answers_path) -> dict:
    return _score_law_qa_module().score(Path(heldout_path), Path(answers_path))


def _citation_correct(answer_text: str, act: str, section: str) -> bool:
    return _score_law_qa_module()._citation_correct(answer_text, act, section)


def _is_abstention_answer(answer_text: str) -> bool:
    return _score_law_qa_module()._is_abstention_answer(answer_text)


# ---------------------------------------------------------------------------
# Pure-python helpers (safe to unit test without torch).
# ---------------------------------------------------------------------------


def load_jsonl(path) -> list[dict]:
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                out.append(json.loads(line))
    return out


def write_jsonl(path, rows: list[dict]) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def truncate_passage_text(text: str, max_bytes: int = MAX_PASSAGE_BYTES) -> str:
    """Truncate `text` to at most `max_bytes` UTF-8 bytes, cutting at the last
    sentence or word boundary within the window rather than mid-word/mid-byte."""
    raw = text.encode("utf-8")
    if len(raw) <= max_bytes:
        return text
    window = raw[:max_bytes]
    for sep, keep in ((b". ", 2), (b"; ", 2), (b", ", 2), (b" ", 1)):
        idx = window.rfind(sep)
        if idx > max_bytes * 0.4:
            window = window[: idx + keep]
            break
    return window.decode("utf-8", errors="ignore").rstrip()


def truncate_passages(passages: list, max_bytes: int = MAX_PASSAGE_BYTES) -> list:
    return [dataclasses.replace(p, text=truncate_passage_text(p.text, max_bytes)) for p in passages]


def adapt_text_from_passages(passages: list, max_bytes: int = ADAPT_MAX_BYTES) -> str:
    """Self-supervised TTT text: the retrieved passages' text ONLY -- never the
    question or any answer -- concatenated and hard-capped to fit the model's
    trained sequence length in one forward pass."""
    text = "\n\n".join(p.text for p in passages)
    raw = text.encode("utf-8")
    if len(raw) <= max_bytes:
        return text
    return raw[:max_bytes].decode("utf-8", errors="ignore")


def retrieve_and_build_prompt(store, question: str, k: int = K_PASSAGES):
    """Returns (prompt, truncated_passages, prompt_bytes)."""
    passages = store.search(question, k)
    trunc = truncate_passages(passages)
    prompt = retrieval_mod.build_grounded_prompt(question, trunc)
    return prompt, trunc, len(prompt.encode("utf-8"))


# ---------------------------------------------------------------------------
# Answer-record construction (used by both B and C/D paths).
# ---------------------------------------------------------------------------


def build_answer_record(qid: str, text: str, prompt_bytes: int, passages: list, gate_info: dict | None = None) -> dict:
    answer = retrieval_mod.parse_answer(text)
    is_grounded = retrieval_mod.grounded(answer, passages)
    record = {
        "id": qid,
        "answer": text,
        "prompt_bytes": prompt_bytes,
        "grounded": is_grounded,
        "cited": bool(answer.citations),
        "abstained": answer.abstained,
    }
    if gate_info is not None:
        record["gate"] = gate_info
    return record


# ---------------------------------------------------------------------------
# run_condition
# ---------------------------------------------------------------------------


def run_condition(
    cond: str,
    model,
    tok,
    store,
    items: list[dict],
    out_dir,
    *,
    loras: list | None = None,
    ttt_cfg: dict | None = None,
    probe=None,
    ledger=None,
    k: int = K_PASSAGES,
    max_new_tokens: int = MAX_NEW_TOKENS_GROUNDED,
    stop: list[str] | None = None,
    heldout_path=DEFAULT_HELDOUT,
) -> dict:
    if cond not in ("B", "C", "D"):
        raise ValueError(f"unknown condition: {cond!r}")
    if cond in ("C", "D") and (loras is None or probe is None):
        raise ValueError(f"condition {cond} requires loras and probe")

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stop = STOP_STRINGS if stop is None else stop
    ttt_cfg = DEFAULT_TTT_CFG if ttt_cfg is None else ttt_cfg

    peak_vram_before = _peak_vram_reset()

    prompt_byte_lengths: list[int] = []
    over_budget = 0
    answers: list[dict] = []

    base_snap = None
    probe_before = None
    if cond in ("C", "D"):
        base_snap = _snapshot(loras)
        probe_before = probe.nll(model, tok, _sequence_nll)

    t0 = time.perf_counter()
    for rec in items:
        qid, question = rec["id"], rec["prompt"]
        prompt, passages, pb = retrieve_and_build_prompt(store, question, k)
        prompt_byte_lengths.append(pb)
        if pb > MAX_PROMPT_BYTES:
            over_budget += 1

        if cond == "B":
            text = _generate(model, tok, [prompt], max_new_tokens, stop)[0]
            record = build_answer_record(qid, text, pb, passages)
        else:
            pre_text = _generate(model, tok, [prompt], max_new_tokens, stop)[0]
            adapt_text = adapt_text_from_passages(passages)
            _adapt(model, tok, adapt_text, loras, ttt_cfg.get("steps", 4), ttt_cfg.get("lr", 1e-3))
            post_text = _generate(model, tok, [prompt], max_new_tokens, stop)[0]
            probe_after = probe.nll(model, tok, _sequence_nll)
            post_answer = retrieval_mod.parse_answer(post_text)
            post_grounded = retrieval_mod.grounded(post_answer, passages)
            decision = _decide(
                probe_before, probe_after, post_grounded,
                threshold=ttt_cfg.get("threshold", 0.15),
                delta_norm=_merged_delta_norm(loras),
            )
            if ledger is not None:
                ledger.append(qid, decision)
            _restore(loras, base_snap)

            chosen_text = post_text if decision.accepted else pre_text
            record = build_answer_record(qid, chosen_text, pb, passages, gate_info=dataclasses.asdict(decision))
        answers.append(record)
    t1 = time.perf_counter()

    write_jsonl(out_dir / "answers.jsonl", answers)
    peak_vram_mib = _peak_vram(peak_vram_before)

    score = _score_law_qa(heldout_path, out_dir / "answers.jsonl")
    extra = _extra_metrics(items, answers)

    report = {
        "condition": cond,
        "n_items": len(items),
        "wall_clock_seconds": {"total": t1 - t0, "per_item": (t1 - t0) / len(items) if items else 0.0},
        "peak_vram_mib": peak_vram_mib,
        "prompt_bytes": {
            "mean": sum(prompt_byte_lengths) / len(prompt_byte_lengths) if prompt_byte_lengths else 0.0,
            "max": max(prompt_byte_lengths) if prompt_byte_lengths else 0,
            "over_budget_count": over_budget,
            "budget": MAX_PROMPT_BYTES,
        },
        "score": score,
        **extra,
    }
    if cond in ("C", "D") and ledger is not None:
        report["gate_summary"] = ledger.summary()
    (out_dir / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def _decide(probe_before, probe_after, grounded_ok, threshold, delta_norm):
    from . import gate as gate_mod

    return gate_mod.decide(probe_before, probe_after, grounded_ok, threshold=threshold, delta_norm=delta_norm)


def _peak_vram_reset():
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.reset_peak_memory_stats()
    except ImportError:
        pass
    return None


def _peak_vram(_before):
    try:
        import torch

        if torch.cuda.is_available():
            return torch.cuda.max_memory_allocated() / (1024 * 1024)
    except ImportError:
        pass
    return None


def _extra_metrics(items: list[dict], answers: list[dict]) -> dict:
    """grounded_rate, hallucinated_citation_rate, abstention_correct (on the
    abstain items), and per-kind gold-citation hit rate with Wilson intervals."""
    by_id = {a["id"]: a for a in answers}
    n = len(answers)
    grounded_n = sum(1 for a in answers if a["grounded"])
    hallucinated_n = sum(1 for a in answers if a["cited"] and not a["grounded"])

    abstain_items = [r for r in items if r["kind"] == _ABSTAIN_KIND]
    abstain_correct = sum(1 for r in abstain_items if by_id.get(r["id"], {}).get("abstained"))

    per_kind: dict[str, dict] = {}
    kinds: dict[str, list[dict]] = {}
    for r in items:
        kinds.setdefault(r["kind"], []).append(r)
    for kind, recs in kinds.items():
        hits = 0
        total = 0
        for r in recs:
            a = by_id.get(r["id"])
            if a is None:
                total += 1
                continue
            total += 1
            if _citation_correct(a["answer"], r["act"], r["section"]):
                hits += 1
        lo, hi = stats.wilson(hits, total) if total else (0.0, 1.0)
        per_kind[kind] = {"successes": hits, "total": total, "rate": hits / total if total else 0.0,
                          "ci_low": lo, "ci_high": hi}

    return {
        "grounded_rate": grounded_n / n if n else 0.0,
        "hallucinated_citation_rate": hallucinated_n / n if n else 0.0,
        "abstention_correct": {
            "successes": abstain_correct, "total": len(abstain_items),
            "rate": abstain_correct / len(abstain_items) if abstain_items else 0.0,
        },
        "per_kind_citation_hit_rate": per_kind,
    }


# ---------------------------------------------------------------------------
# compare
# ---------------------------------------------------------------------------


def compare(cond_a_dir, cond_b_dir, heldout_path=DEFAULT_HELDOUT, out_path=None) -> dict:
    """Paired McNemar + bootstrap on per-item 'gold citation present in
    answer' (score_law_qa's own matching logic, item-level boolean) and on
    'grounded', over the items answered by both conditions."""
    heldout_by_id = {r["id"]: r for r in load_jsonl(heldout_path)}
    a_by_id = {r["id"]: r for r in load_jsonl(Path(cond_a_dir) / "answers.jsonl")}
    b_by_id = {r["id"]: r for r in load_jsonl(Path(cond_b_dir) / "answers.jsonl")}
    common_ids = [i for i in heldout_by_id if i in a_by_id and i in b_by_id]

    cite_a, cite_b, grounded_a, grounded_b = [], [], [], []
    for qid in common_ids:
        rec = heldout_by_id[qid]
        cite_a.append(_citation_correct(a_by_id[qid]["answer"], rec["act"], rec["section"]))
        cite_b.append(_citation_correct(b_by_id[qid]["answer"], rec["act"], rec["section"]))
        grounded_a.append(bool(a_by_id[qid].get("grounded")))
        grounded_b.append(bool(b_by_id[qid].get("grounded")))

    result: dict[str, Any] = {"n": len(common_ids)}
    for name, (aa, bb) in {
        "gold_citation_present": (cite_a, cite_b),
        "grounded": (grounded_a, grounded_b),
    }.items():
        if not aa:
            result[name] = {"n": 0}
            continue
        b_disc, c_disc = stats.paired_table(aa, bb)
        p = stats.mcnemar_exact(b_disc, c_disc)
        lo, hi = stats.bootstrap_diff(aa, bb)
        result[name] = {
            "n": len(aa),
            "a_rate": sum(aa) / len(aa),
            "b_rate": sum(bb) / len(bb),
            "mcnemar_b": b_disc,
            "mcnemar_c": c_disc,
            "mcnemar_p": p,
            "bootstrap_diff_95ci": [lo, hi],
        }

    if out_path is not None:
        Path(out_path).write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_probe():
    from . import gate as gate_mod

    return gate_mod.RegressionProbe.from_files(str(DEFAULT_PROBE_GENERAL), n_general=16, seed=0)


def main(argv: list[str] | None = None) -> int:
    import argparse

    from . import model_io

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--condition", choices=["B", "C", "D"], required=True)
    parser.add_argument("--checkpoint", type=Path, default=DEFAULT_CHECKPOINT)
    parser.add_argument("--heldout", type=Path, default=DEFAULT_HELDOUT)
    parser.add_argument("--train", type=Path, default=DEFAULT_TRAIN)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--device", type=str, default="cuda:0")
    parser.add_argument("--limit", type=int, default=None, help="use only the first N held-out items")
    parser.add_argument("--persistent-lora", type=Path, default=None, help="condition D: path to a saved persistent LoRA state")
    parser.add_argument("--steps", type=int, default=DEFAULT_TTT_CFG["steps"])
    parser.add_argument("--lr", type=float, default=DEFAULT_TTT_CFG["lr"])
    parser.add_argument("--threshold", type=float, default=DEFAULT_TTT_CFG["threshold"])
    args = parser.parse_args(argv)

    model, tok = model_io.load_model(args.checkpoint, device=args.device)
    store = retrieval_mod.PassageStore.from_law_files(args.train, args.heldout)
    items = load_jsonl(args.heldout)
    if args.limit:
        items = items[: args.limit]

    loras = None
    probe = None
    ledger = None
    if args.condition in ("C", "D"):
        loras = _inject_lora(model)
        probe = _build_probe()
        from . import gate as gate_mod

        ledger = gate_mod.GateLedger(str(args.out))
        if args.condition == "D" and args.persistent_lora:
            _load_lora_state(loras, args.persistent_lora)

    ttt_cfg = {"steps": args.steps, "lr": args.lr, "threshold": args.threshold}
    report = run_condition(
        args.condition, model, tok, store, items, args.out,
        loras=loras, ttt_cfg=ttt_cfg, probe=probe, ledger=ledger, heldout_path=args.heldout,
    )
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
