"""The RSI controller: per-query ephemeral TTT -> gate -> accept/reject ledger
-> gated consolidation of a PERSISTENT LoRA on the accepted, checker-verified
pairs -> re-evaluate on held-out (condition D) -> next round.

One set of `ttt.LoRALinear` modules is injected once, at the same targets used
by evaluate.py (`evaluate.LORA_TARGET_REGEX`, r=8), and is reused across the
whole run:

  - During a round's query stream, each query is ephemeral exactly like
    condition C: the LoRA state is reset to the round's starting snapshot
    (`persistent_snap`, zero-initialized before round 1) before every query's
    adaptation, and restored to it again right after -- so within-round
    adaptation never leaks between queries.
  - After the stream, `consolidate()` resumes training the SAME LoRA modules
    (starting again from `persistent_snap`, one shared optimizer this time,
    not a fresh one per example) via prompt-masked next-byte cross-entropy
    SFT on the accepted (grounded prompt, generated answer) pairs. If the
    regression probe on the consolidated weights regresses beyond threshold,
    the whole consolidation is rejected and `persistent_snap` is restored
    unchanged; otherwise the consolidated weights become the new
    `persistent_snap` (saved to disk) and are evaluated on held-out as
    condition D via `evaluate.run_condition` -- which starts from whatever
    state the passed-in `loras` are already in, so no special-casing is
    needed there.

Model/torch-heavy calls go through `evaluate`'s lazy `_generate` / `_adapt` /
`_snapshot` / `_restore` / `_merged_delta_norm` / `_sequence_nll` functions
(referenced as `evaluate.<name>` so tests can monkeypatch them), keeping this
module importable and unit-testable on the host with no torch installed.
"""

from __future__ import annotations

import json
import random
import time
from pathlib import Path

from . import evaluate
from . import retrieval as retrieval_mod
from . import stats

DEFAULT_N_STREAM = 300
DEFAULT_SEED = 0
CONSOLIDATE_LR = 2e-4
CONSOLIDATE_MAX_GRAD_NORM = 1.0
CONSOLIDATE_THRESHOLD = 0.15


def sample_stream(train_path, n: int, rounds: int, seed: int = DEFAULT_SEED) -> list[list[dict]]:
    """Deterministic (seed-fixed) sample of `n * rounds` distinct train-split
    records, split into `rounds` chunks of `n` -- round r sees fresh queries,
    never round r-1's. Only `prompt`/`id`/`kind` are read; gold `target`
    fields are never used by the loop (query stream only, per the module
    contract)."""
    records = evaluate.load_jsonl(train_path)
    rng = random.Random(seed)
    idx = list(range(len(records)))
    rng.shuffle(idx)
    total = n * rounds
    if total > len(records):
        raise ValueError(f"requested {total} stream items but only {len(records)} train records exist")
    chosen = idx[:total]
    return [
        [{"id": records[i].get("id", str(i)), "prompt": records[i]["prompt"]}
         for i in chosen[r * n : (r + 1) * n]]
        for r in range(rounds)
    ]


def run_round(model, tok, store, stream_items: list[dict], loras, probe, ledger, ttt_cfg: dict,
              k: int = evaluate.K_PASSAGES) -> tuple[list[tuple[str, str]], dict]:
    """One pass over `stream_items`: ephemeral TTT + gate per query. Returns
    (accepted_pairs, round_stream_stats) where accepted_pairs is a list of
    (grounded_prompt, generated_answer) for gate-accepted queries."""
    base_snap = evaluate._snapshot(loras)
    probe_before = probe.nll(model, tok, evaluate._sequence_nll)

    accepted: list[tuple[str, str]] = []
    n_accepted = n_rejected = 0
    for rec in stream_items:
        qid, question = rec["id"], rec["prompt"]
        prompt, passages, _pb = evaluate.retrieve_and_build_prompt(store, question, k)

        adapt_text = evaluate.adapt_text_from_passages(passages)
        evaluate._adapt(model, tok, adapt_text, loras, ttt_cfg.get("steps", 4), ttt_cfg.get("lr", 1e-3))
        text = evaluate._generate(model, tok, [prompt], evaluate.MAX_NEW_TOKENS_GROUNDED, evaluate.STOP_STRINGS)[0]
        probe_after = probe.nll(model, tok, evaluate._sequence_nll)
        answer = retrieval_mod.parse_answer(text)
        is_grounded = retrieval_mod.grounded(answer, passages)

        decision = evaluate._decide(
            probe_before, probe_after, is_grounded,
            threshold=ttt_cfg.get("threshold", 0.15),
            delta_norm=evaluate._merged_delta_norm(loras),
        )
        ledger.append(qid, decision)
        evaluate._restore(loras, base_snap)

        if decision.accepted:
            n_accepted += 1
            accepted.append((prompt, text))
        else:
            n_rejected += 1

    return accepted, {"n_stream": len(stream_items), "n_accepted": n_accepted, "n_rejected": n_rejected}


def consolidate(model, tok, loras, accepted_pairs: list[tuple[str, str]], probe,
                 persistent_snap, *, lr: float = CONSOLIDATE_LR,
                 max_grad_norm: float = CONSOLIDATE_MAX_GRAD_NORM,
                 threshold: float = CONSOLIDATE_THRESHOLD) -> tuple[bool, dict, list]:
    """SFT the persistent LoRA on `accepted_pairs` (prompt-masked next-byte CE
    via `evaluate._tensor_nll_loss(model, tok, prompt, continuation)` --
    "mean NLL over `continuation` bytes only", i.e. exactly a prompt-masked
    loss, kept attached to the autograd graph unlike the float-returning
    `_sequence_nll`/`model_io.sequence_nll` used for probe scoring), one
    shared AdamW optimizer across the whole epoch
    (unlike ephemeral `adapt`, which uses a fresh optimizer per call), single
    example per step (batching by exact length is unnecessary here since each
    step's loss is independent). Gated by the regression probe: if it
    regresses beyond `threshold`, the whole consolidation is rejected and
    `persistent_snap` is restored unchanged.

    Returns (accepted, stats, new_snapshot_to_use) -- new_snapshot_to_use is
    the consolidated snapshot if accepted, else `persistent_snap` again.
    """
    evaluate._restore(loras, persistent_snap)
    probe_before = probe.nll(model, tok, evaluate._sequence_nll)

    if accepted_pairs:
        import torch  # local: torch-heavy, keeps this importable on the host

        params = [p for m in loras for p in (m.lora_A, m.lora_B)]
        opt = torch.optim.AdamW(params, lr=lr)
        model.train()
        try:
            for prompt, answer in accepted_pairs:
                loss_t = evaluate._tensor_nll_loss(model, tok, prompt, answer)
                opt.zero_grad()
                loss_t.backward()
                torch.nn.utils.clip_grad_norm_(params, max_grad_norm)
                opt.step()
        finally:
            model.eval()

    probe_after = probe.nll(model, tok, evaluate._sequence_nll)
    decision = evaluate._decide(probe_before, probe_after, True, threshold=threshold, delta_norm=None)
    # Consolidation has no per-item groundedness signal of its own; only the
    # probe-regression check applies (mirrors `gate.decide` semantics with
    # `require_grounded=False`, expressed here via `grounded=True`).
    decision.reason = "ok" if decision.accepted else "consolidation_probe_regression"

    consolidate_stats = {
        "n_accepted_pairs": len(accepted_pairs),
        "probe_before": probe_before,
        "probe_after": probe_after,
        "probe_delta_rel": decision.probe_delta_rel,
        "accepted": decision.accepted,
        "reason": decision.reason,
    }

    if decision.accepted:
        new_snap = evaluate._snapshot(loras)
        return True, consolidate_stats, new_snap
    evaluate._restore(loras, persistent_snap)
    return False, consolidate_stats, persistent_snap


def in_sample_sanity_check(model, tok, dataset_path, *, n_citation: int = 30, n_abstain: int = 10,
                            seed: int = 0, min_citation_hit_rate: float = 0.5,
                            max_spurious_abstain_rate: float = 0.2) -> dict:
    """Round-1b mandatory gate (2026-09-13 pivot, after round 1's mode
    collapse): before spending ~13 minutes on a held-out eval, generate on a
    deterministic sample of the model's OWN TRAINING prompts (gold is fine
    here -- these are training examples, not held-out) and require the
    consolidated state to actually reproduce citations it was just trained
    on, and not abstain when the gold passage IS in its own prompt. Failing
    this means the SFT run itself is broken (wrong target, mode collapse,
    undertraining, ...) and a held-out run would just reproduce round 1's
    failure at 13x the cost."""
    from . import retrieval as retrieval_mod

    examples = evaluate.load_jsonl(dataset_path)
    citation_pool = [e for e in examples if e["kind"] != "law_abstain" and not e.get("synthetic_abstain")]
    abstain_pool = [e for e in examples if e["kind"] == "law_abstain" or e.get("synthetic_abstain")]

    rng = random.Random(seed)
    citation_sample = rng.sample(citation_pool, min(n_citation, len(citation_pool)))
    abstain_sample = rng.sample(abstain_pool, min(n_abstain, len(abstain_pool)))

    citation_results = []
    for e in citation_sample:
        text = evaluate._generate(model, tok, [e["prompt"]], evaluate.MAX_NEW_TOKENS_GROUNDED, evaluate.STOP_STRINGS)[0]
        hit = evaluate._citation_correct(text, e["act"], e["section"])
        spurious_abstain = retrieval_mod.parse_answer(text).abstained
        citation_results.append({"id": e["id"], "answer": text, "hit": hit, "spurious_abstain": spurious_abstain})

    abstain_results = []
    for e in abstain_sample:
        text = evaluate._generate(model, tok, [e["prompt"]], evaluate.MAX_NEW_TOKENS_GROUNDED, evaluate.STOP_STRINGS)[0]
        correct = retrieval_mod.parse_answer(text).abstained
        abstain_results.append({"id": e["id"], "answer": text, "correct": correct})

    n_cite = len(citation_results)
    citation_hit_rate = sum(1 for r in citation_results if r["hit"]) / n_cite if n_cite else 0.0
    spurious_abstain_rate = sum(1 for r in citation_results if r["spurious_abstain"]) / n_cite if n_cite else 0.0
    n_abst = len(abstain_results)
    abstain_correct_rate = sum(1 for r in abstain_results if r["correct"]) / n_abst if n_abst else 0.0

    passed = citation_hit_rate >= min_citation_hit_rate and spurious_abstain_rate <= max_spurious_abstain_rate
    return {
        "passed": passed,
        "n_citation": n_cite, "citation_hit_rate": citation_hit_rate,
        "spurious_abstain_rate": spurious_abstain_rate,
        "n_abstain": n_abst, "abstain_correct_rate": abstain_correct_rate,
        "min_citation_hit_rate": min_citation_hit_rate, "max_spurious_abstain_rate": max_spurious_abstain_rate,
        "citation_examples": citation_results, "abstain_examples": abstain_results,
    }


def in_sample_sanity_check_scoring(model, tok, dataset_path, *, n_citation: int = 30, n_abstain: int = 10,
                                    seed: int = 0, min_citation_hit_rate: float = 0.5,
                                    max_spurious_abstain_rate: float = 0.2) -> dict:
    """Scoring-mode counterpart of `in_sample_sanity_check` (F12
    greedy-decoding-trap fix, 2026-09-13 round 2): instead of free-generating
    and pattern-matching the result, reconstruct the exact passages/question
    each sampled training prompt was built from (`evaluate.parse_passages_from_prompt`
    / `parse_question_from_prompt` -- exact, since they re-parse the prompt
    the model actually saw, not a fresh retrieval call) and use
    `evaluate.build_candidates` + `score_candidates` exactly as
    `evaluate.run_condition_scoring` does at held-out time."""
    examples = evaluate.load_jsonl(dataset_path)
    citation_pool = [e for e in examples if e["kind"] != "law_abstain" and not e.get("synthetic_abstain")]
    abstain_pool = [e for e in examples if e["kind"] == "law_abstain" or e.get("synthetic_abstain")]

    rng = random.Random(seed)
    citation_sample = rng.sample(citation_pool, min(n_citation, len(citation_pool)))
    abstain_sample = rng.sample(abstain_pool, min(n_abstain, len(abstain_pool)))

    def score_one(e):
        passages = evaluate.parse_passages_from_prompt(e["prompt"])
        candidates = evaluate.build_candidates(passages, e["kind"])
        result = evaluate.score_candidates(model, tok, e["prompt"], candidates)
        text, source = candidates[result["best_idx"]]
        return text, source, result["margin"]

    citation_results = []
    for e in citation_sample:
        text, source, margin = score_one(e)
        hit = source is not None and (source.act, source.section) == (e["act"], e["section"])
        citation_results.append({"id": e["id"], "answer": text, "hit": hit,
                                  "spurious_abstain": source is None, "margin": margin})

    abstain_results = []
    for e in abstain_sample:
        text, source, margin = score_one(e)
        abstain_results.append({"id": e["id"], "answer": text, "correct": source is None, "margin": margin})

    n_cite = len(citation_results)
    citation_hit_rate = sum(1 for r in citation_results if r["hit"]) / n_cite if n_cite else 0.0
    spurious_abstain_rate = sum(1 for r in citation_results if r["spurious_abstain"]) / n_cite if n_cite else 0.0
    n_abst = len(abstain_results)
    abstain_correct_rate = sum(1 for r in abstain_results if r["correct"]) / n_abst if n_abst else 0.0

    passed = citation_hit_rate >= min_citation_hit_rate and spurious_abstain_rate <= max_spurious_abstain_rate
    return {
        "passed": passed,
        "n_citation": n_cite, "citation_hit_rate": citation_hit_rate,
        "spurious_abstain_rate": spurious_abstain_rate,
        "n_abstain": n_abst, "abstain_correct_rate": abstain_correct_rate,
        "min_citation_hit_rate": min_citation_hit_rate, "max_spurious_abstain_rate": max_spurious_abstain_rate,
        "citation_examples": citation_results, "abstain_examples": abstain_results,
    }


def sft_train(model, tok, loras, pairs: list[tuple[str, str]], *, lr: float, epochs: int = 1,
              max_grad_norm: float = 1.0, log_every: int = 100) -> list[float]:
    """Prompt-masked next-byte CE SFT over `pairs` (each `(prompt, target)`),
    ONE shared AdamW optimizer across every step of every epoch, batch size 1
    (see evaluate._tensor_nll_loss / F9 in FIXES-FOR-MAIN-SESSIONS.md for why
    a hand-rolled tensor-returning loss is used instead of `ttt.adapt`'s
    broken default). Returns the loss logged every `log_every` steps."""
    import torch

    params = [p for m in loras for p in (m.lora_A, m.lora_B)]
    opt = torch.optim.AdamW(params, lr=lr)
    model.train()
    logged: list[float] = []
    step = 0
    try:
        for _epoch in range(epochs):
            for prompt, target in pairs:
                loss_t = evaluate._tensor_nll_loss(model, tok, prompt, target)
                opt.zero_grad()
                loss_t.backward()
                torch.nn.utils.clip_grad_norm_(params, max_grad_norm)
                opt.step()
                step += 1
                if step % log_every == 0:
                    loss_val = float(loss_t.detach().item())
                    logged.append(loss_val)
                    print(f"[sft_train] step {step}/{epochs * len(pairs)} loss={loss_val:.4f}", flush=True)
    finally:
        model.eval()
    return logged


def consolidate_from_dataset(
    model, tok, loras, dataset_path, probe, persistent_snap, *,
    lr: float = 3e-4, epochs: int = 2, max_grad_norm: float = 1.0,
    threshold: float = CONSOLIDATE_THRESHOLD, log_every: int = 100,
    retry_halved_lr: bool = True,
) -> tuple[bool, dict, list]:
    """Phase 2: SFT a fresh persistent LoRA on the harness-built grounded-SFT
    set (`grounded_data.py`'s output). Gated exactly like `consolidate()`
    (regression probe must not regress beyond `threshold`); on rejection,
    retries once at half the learning rate before giving up (per the
    2026-09-13 pivot instructions) -- a second failure is reported, not
    silently swallowed, and the LoRA is left at `persistent_snap`."""
    examples = evaluate.load_jsonl(dataset_path)
    pairs = [(e["prompt"], e["target"]) for e in examples]

    def attempt(lr_value: float):
        evaluate._restore(loras, persistent_snap)
        probe_before = probe.nll(model, tok, evaluate._sequence_nll)
        losses = sft_train(model, tok, loras, pairs, lr=lr_value, epochs=epochs,
                            max_grad_norm=max_grad_norm, log_every=log_every)
        probe_after = probe.nll(model, tok, evaluate._sequence_nll)
        decision = evaluate._decide(probe_before, probe_after, True, threshold=threshold, delta_norm=None)
        decision.reason = "ok" if decision.accepted else "consolidation_probe_regression"
        return decision, losses, probe_before, probe_after

    decision, losses, probe_before, probe_after = attempt(lr)
    retried = False
    if not decision.accepted and retry_halved_lr:
        retried = True
        decision, losses, probe_before, probe_after = attempt(lr / 2)

    consolidate_stats = {
        "n_examples": len(pairs),
        "lr": lr if not retried else lr / 2,
        "epochs": epochs,
        "retried_at_half_lr": retried,
        "probe_before": probe_before,
        "probe_after": probe_after,
        "probe_delta_rel": decision.probe_delta_rel,
        "accepted": decision.accepted,
        "reason": decision.reason,
        "logged_losses": losses,
    }

    if decision.accepted:
        new_snap = evaluate._snapshot(loras)
        return True, consolidate_stats, new_snap
    evaluate._restore(loras, persistent_snap)
    return False, consolidate_stats, persistent_snap


def run_loop(
    model, tok, store, run_dir, *,
    heldout_items: list[dict],
    train_path=evaluate.DEFAULT_TRAIN,
    n_stream: int = DEFAULT_N_STREAM,
    rounds: int = 2,
    seed: int = DEFAULT_SEED,
    ttt_cfg: dict | None = None,
    heldout_path=evaluate.DEFAULT_HELDOUT,
    initial_persistent_lora=None,
    persistent_target_regex: str | None = None,
    persistent_r: int = evaluate.LORA_R,
    persistent_alpha: int = evaluate.LORA_ALPHA,
) -> dict:
    """`initial_persistent_lora`, if given, is a snapshot file saved by
    Phase 2's `consolidate_from_dataset` (loop.py `sft` CLI) or an earlier
    round of this same loop -- its `persistent_target_regex`/`_r`/`_alpha`
    MUST match what produced that file (LoRA shapes must agree for
    `torch.load` + `restore` to work); by default this uses
    `evaluate.persistent_lora_target_regex` (attention q/o + all-block MLP,
    Phase 2's target set) so a Phase 2 checkpoint loads directly."""
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    ttt_cfg = evaluate.DEFAULT_TTT_CFG if ttt_cfg is None else ttt_cfg

    from . import gate as gate_mod

    probe = gate_mod.RegressionProbe.from_files(str(evaluate.DEFAULT_PROBE_GENERAL), n_general=16, seed=seed)
    target_regex = persistent_target_regex or evaluate.persistent_lora_target_regex(model)
    loras = evaluate._inject_lora(model, target_regex=target_regex, r=persistent_r, alpha=persistent_alpha)
    persistent_snap = evaluate._snapshot(loras)  # round-0 persistent state: zero-init (no adaptation)
    if initial_persistent_lora is not None:
        evaluate._load_lora_state(loras, initial_persistent_lora)
        persistent_snap = evaluate._snapshot(loras)

    stream_chunks = sample_stream(train_path, n_stream, rounds, seed=seed)

    round_reports = []
    for r in range(1, rounds + 1):
        round_dir = run_dir / f"round{r}"
        round_dir.mkdir(parents=True, exist_ok=True)

        evaluate._restore(loras, persistent_snap)
        stream_ledger = gate_mod.GateLedger(str(round_dir / "stream_gate"))
        t0 = time.perf_counter()
        accepted_pairs, stream_stats = run_round(
            model, tok, store, stream_chunks[r - 1], loras, probe, stream_ledger, ttt_cfg,
        )
        t1 = time.perf_counter()

        accepted, consolidate_stats, persistent_snap = consolidate(
            model, tok, loras, accepted_pairs, probe, persistent_snap,
        )
        t2 = time.perf_counter()

        evaluate._restore(loras, persistent_snap)
        evaluate._save_lora_state(loras, run_dir / f"persistent_lora_round{r}.pt")

        eval_ledger = gate_mod.GateLedger(str(round_dir / "D"))
        eval_report = evaluate.run_condition(
            "D", model, tok, store, heldout_items, round_dir / "D",
            loras=loras, ttt_cfg=ttt_cfg, probe=probe, ledger=eval_ledger, heldout_path=heldout_path,
        )
        t3 = time.perf_counter()

        round_report = {
            "round": r,
            "stream": {**stream_stats, "gate_summary": stream_ledger.summary(),
                       "wall_clock_seconds": t1 - t0},
            "consolidation": {**consolidate_stats, "wall_clock_seconds": t2 - t1},
            "eval_D": eval_report,
            "eval_D_wall_clock_seconds": t3 - t2,
        }
        round_reports.append(round_report)
        (round_dir / "round_report.json").write_text(json.dumps(round_report, indent=2), encoding="utf-8")

    report = {
        "n_stream_per_round": n_stream,
        "rounds": round_reports,
        "seed": seed,
        "ttt_cfg": ttt_cfg,
    }
    (run_dir / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    write_report_md(report, run_dir / "report.md")
    return report


def write_report_md(report: dict, path) -> None:
    """Fallback markdown renderer, used only if prototypes/nyaya_ttt_rsi/report.py
    does not exist (see main())."""
    lines = ["# nyaya_ttt_rsi RSI loop report", ""]
    lines.append(f"seed={report['seed']}  n_stream_per_round={report['n_stream_per_round']}")
    lines.append("")
    lines.append("| round | stream n | accepted | rejected | consolidation accepted | probe delta | D citation_recall | D abstention |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for rr in report["rounds"]:
        s, c, e = rr["stream"], rr["consolidation"], rr["eval_D"]
        cite = e.get("score", {}).get("overall", {}).get("citation_recall", {})
        abst = e.get("abstention_correct", {})
        lines.append(
            f"| {rr['round']} | {s['n_stream']} | {s['n_accepted']} | {s['n_rejected']} | "
            f"{c['accepted']} | {c['probe_delta_rel']:.4f} | "
            f"{cite.get('rate', 0.0):.4f} ({cite.get('successes', 0)}/{cite.get('total', 0)}) | "
            f"{abst.get('rate', 0.0):.4f} ({abst.get('successes', 0)}/{abst.get('total', 0)}) |"
        )
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")


def main_sft(argv: list[str] | None = None) -> int:
    """Phase 2 CLI: SFT a fresh persistent LoRA on grounded_data.py's output
    (no query stream, no per-query ephemeral TTT/gate -- that is `main`'s
    `loop` subcommand, Phase 4)."""
    import argparse

    from . import gate as gate_mod
    from . import model_io

    parser = argparse.ArgumentParser(description=main_sft.__doc__)
    parser.add_argument("--checkpoint", type=Path, default=evaluate.DEFAULT_CHECKPOINT)
    parser.add_argument("--dataset", type=Path, required=True, help="grounded_data.py's grounded_sft.jsonl")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--device", type=str, default="cuda:0")
    parser.add_argument("--lr", type=float, default=CONSOLIDATE_LR)
    parser.add_argument("--epochs", type=int, default=2)
    parser.add_argument("--threshold", type=float, default=CONSOLIDATE_THRESHOLD)
    parser.add_argument("--log-every", type=int, default=100)
    parser.add_argument("--round", type=int, default=1)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args(argv)

    args.out.mkdir(parents=True, exist_ok=True)
    model, tok = model_io.load_model(args.checkpoint, device=args.device)
    loras = evaluate._inject_lora(model, target_regex=evaluate.persistent_lora_target_regex(model),
                                   r=16, alpha=32)
    persistent_snap = evaluate._snapshot(loras)
    probe = gate_mod.RegressionProbe.from_files(str(evaluate.DEFAULT_PROBE_GENERAL), n_general=16, seed=0)

    t0 = time.perf_counter()
    accepted, stats_out, new_snap = consolidate_from_dataset(
        model, tok, loras, args.dataset, probe, persistent_snap,
        lr=args.lr, epochs=args.epochs, threshold=args.threshold, log_every=args.log_every,
    )
    t1 = time.perf_counter()

    sanity = None
    if accepted:
        evaluate._restore(loras, new_snap)
        evaluate._save_lora_state(loras, args.out / f"persistent_lora_round{args.round}.pt")
        sanity = in_sample_sanity_check(model, tok, args.dataset, seed=args.seed)
        (args.out / f"sanity_check_round{args.round}.json").write_text(json.dumps(sanity, indent=2), encoding="utf-8")

    report = {"phase": "2_sft_consolidation", "accepted": accepted, "wall_clock_seconds": t1 - t0,
              "sanity_check_passed": sanity["passed"] if sanity else None, **stats_out}
    (args.out / f"sft_round{args.round}_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "logged_losses"}, indent=2))
    if sanity is not None:
        print(json.dumps({k: v for k, v in sanity.items() if not k.endswith("_examples")}, indent=2))
    if not accepted:
        return 1
    return 0 if sanity["passed"] else 2


def main(argv: list[str] | None = None) -> int:
    import argparse

    from . import model_io

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", type=Path, default=evaluate.DEFAULT_CHECKPOINT)
    parser.add_argument("--heldout", type=Path, default=evaluate.DEFAULT_HELDOUT)
    parser.add_argument("--train", type=Path, default=evaluate.DEFAULT_TRAIN)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--device", type=str, default="cuda:0")
    parser.add_argument("--rounds", type=int, default=2)
    parser.add_argument("--stream", type=int, default=DEFAULT_N_STREAM)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--steps", type=int, default=evaluate.DEFAULT_TTT_CFG["steps"])
    parser.add_argument("--lr", type=float, default=evaluate.DEFAULT_TTT_CFG["lr"])
    parser.add_argument("--threshold", type=float, default=evaluate.DEFAULT_TTT_CFG["threshold"])
    parser.add_argument("--persistent-lora", type=Path, default=None,
                         help="start from this saved persistent LoRA state (e.g. Phase 2's output) instead of zero-init")
    args = parser.parse_args(argv)

    model, tok = model_io.load_model(args.checkpoint, device=args.device)
    store = retrieval_mod.PassageStore.from_law_files(args.train, args.heldout)
    heldout_items = evaluate.load_jsonl(args.heldout)
    ttt_cfg = {"steps": args.steps, "lr": args.lr, "threshold": args.threshold}

    report = run_loop(
        model, tok, store, args.out,
        heldout_items=heldout_items, train_path=args.train,
        n_stream=args.stream, rounds=args.rounds, seed=args.seed,
        ttt_cfg=ttt_cfg, heldout_path=args.heldout,
        initial_persistent_lora=args.persistent_lora,
    )

    try:
        from . import report as report_mod  # type: ignore

        if hasattr(report_mod, "render"):
            report_mod.render(report, Path(args.out))
    except ImportError:
        pass

    print(json.dumps({"rounds": len(report["rounds"])}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
