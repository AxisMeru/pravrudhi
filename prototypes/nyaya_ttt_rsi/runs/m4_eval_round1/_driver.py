"""STEP 0 driver: evaluate the 1.13B Megatron checkpoint through the same
scoring-mode + BM25-rank-prior harness used for S4t/S4t_f (runs/pointwise_run1),
on the tuned retrieval store, with the 2026-09-13 synthetic-absent-gold
calibration fix (evaluate.build_natural_dev_examples's frac_miss).

Conditions:
  M4t -- k=4, PROMPT_CONFIG unchanged (comparable to S4t_f).
  M8t -- k=8, body=200B, title=90B, prompt budget=1400B (room the 1.13B has
         via its measured 1536-byte trained sequence length, g0/README.md).

For each condition: calibrate on the dev-chosen lambda grid (also forcing
lambda=0 separately), run the held-out 690 at the calibrated threshold, and
run a forced-always-cite pass (tau=delta=-inf) for pre-abstention selection
accuracy. No LoRA on the 1.13B in this step.

Run inside ttt-lab:
  docker exec ttt-lab python3 /lab/prototypes/nyaya_ttt_rsi/runs/m4_eval_round1/_driver.py <stage>
stages: m4t | m8t | compare
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, "/lab")
from prototypes.nyaya_ttt_rsi import evaluate
from prototypes.nyaya_ttt_rsi import retrieval as retrieval_mod
from prototypes.nyaya_ttt_rsi import retrieval_tuning
from prototypes.nyaya_ttt_rsi import model_io

RUN_DIR = Path("/lab/prototypes/nyaya_ttt_rsi/runs/m4_eval_round1")
CHECKPOINT = Path("/lab/prototypes/nyaya_ttt_rsi/runs/g0_sft_round1/final.pt")
POINTWISE_DIR = Path("/lab/prototypes/nyaya_ttt_rsi/runs/pointwise_run1")

M8T_CONFIG = evaluate.PromptConfig(k=8, body_max_bytes=200, title_max_bytes=90,
                                    max_prompt_bytes=1400, instruction=evaluate.INSTRUCTION)


def log(obj):
    print(json.dumps(obj, indent=2), flush=True)


def peak_vram_gib():
    import torch
    return torch.cuda.max_memory_allocated() / (1024 ** 3)


def load_everything():
    import torch
    torch.cuda.reset_peak_memory_stats()
    t0 = time.perf_counter()
    model, tok = model_io.load_model(CHECKPOINT, device="cuda:0")
    t1 = time.perf_counter()
    best_config = json.loads(
        Path("/lab/prototypes/nyaya_ttt_rsi/runs/retrieval_tuning/results.json").read_text()
    )["best_config"]
    store_plain = retrieval_mod.PassageStore.from_law_files(evaluate.DEFAULT_TRAIN, evaluate.DEFAULT_HELDOUT)
    store_tuned = retrieval_tuning.TunedStore(store_plain.passages, **best_config)
    heldout_items = evaluate.load_jsonl(evaluate.DEFAULT_HELDOUT)
    log({"load_wall_s": t1 - t0, "load_peak_vram_gib": peak_vram_gib(), "backend": model._nyaya_backend})
    return model, tok, store_tuned, heldout_items


def run_condition_full(model, tok, store, heldout_items, *, k, tag, config):
    """Dev-chosen lambda grid calibration + held-out eval + forced-cite pass,
    then a SEPARATE lambda=0-forced calibration + held-out eval, mirroring
    _driver2.py's scoring_prior_condition but for the 1.13B and adding the
    lambda=0 comparison the task requires."""
    import torch

    torch.cuda.reset_peak_memory_stats()
    t0 = time.perf_counter()
    cal = evaluate.calibrate_scoring_with_rank_prior(
        model, tok, store, evaluate.DEFAULT_TRAIN, k=k, n=500, seed=1,
        lambda_grid=(0.0, 0.25, 0.5, 1.0), frac_miss=0.2, config=config,
    )
    t1 = time.perf_counter()
    (RUN_DIR / f"calibration_{tag}.json").write_text(json.dumps(cal, indent=2))
    log({f"calibration_{tag}_wall_s": t1 - t0, "tau_m": cal["tau_m"], "delta_m": cal["delta_m"],
         "lambda_prior": cal["lambda_prior"], "balanced_accuracy": cal["balanced_accuracy"],
         "abstain_on_synthetic_miss": cal["abstain_on_synthetic_miss"], "confusion": cal["confusion"]})

    t2 = time.perf_counter()
    report = evaluate.run_condition_scoring_with_rank_prior(
        model, tok, store, heldout_items, RUN_DIR / tag,
        k=k, tau_m=cal["tau_m"], delta_m=cal["delta_m"], lambda_prior=cal["lambda_prior"],
        heldout_path=evaluate.DEFAULT_HELDOUT, label=tag, config=config,
    )
    t3 = time.perf_counter()
    vram = peak_vram_gib()
    log({f"{tag}_wall_s": t3 - t2, f"{tag}_peak_vram_gib": vram,
         f"{tag}_gold_selected": report["gold_selected_overall"],
         f"{tag}_recall": report["score"]["overall"]["citation_recall"]})

    t4 = time.perf_counter()
    forced = evaluate.run_condition_scoring_with_rank_prior(
        model, tok, store, heldout_items, RUN_DIR / f"{tag}_forced_cite",
        k=k, tau_m=float("-inf"), delta_m=float("-inf"), lambda_prior=cal["lambda_prior"],
        heldout_path=evaluate.DEFAULT_HELDOUT, label=f"{tag}_forced_cite", config=config,
    )
    t5 = time.perf_counter()
    log({f"{tag}_forced_wall_s": t5 - t4, f"{tag}_pre_abstention_selection_accuracy": forced["gold_selected_overall"]})

    # lambda=0 forced comparison (task requirement: "ALSO lambda=0 reported").
    t6 = time.perf_counter()
    cal0 = evaluate.calibrate_scoring_with_rank_prior(
        model, tok, store, evaluate.DEFAULT_TRAIN, k=k, n=500, seed=1,
        lambda_grid=(0.0,), frac_miss=0.2, config=config,
    )
    t7 = time.perf_counter()
    (RUN_DIR / f"calibration_{tag}_lambda0.json").write_text(json.dumps(cal0, indent=2))
    report0 = evaluate.run_condition_scoring_with_rank_prior(
        model, tok, store, heldout_items, RUN_DIR / f"{tag}_lambda0",
        k=k, tau_m=cal0["tau_m"], delta_m=cal0["delta_m"], lambda_prior=0.0,
        heldout_path=evaluate.DEFAULT_HELDOUT, label=f"{tag}_lambda0", config=config,
    )
    t8 = time.perf_counter()
    log({f"{tag}_lambda0_calibration_wall_s": t7 - t6, f"{tag}_lambda0_eval_wall_s": t8 - t7,
         f"{tag}_lambda0_recall": report0["score"]["overall"]["citation_recall"],
         f"{tag}_lambda0_gold_selected": report0["gold_selected_overall"]})

    return report, cal, forced, report0, cal0


def stage_m4t(model, tok, store, heldout_items):
    run_condition_full(model, tok, store, heldout_items, k=4, tag="M4t", config=evaluate.PROMPT_CONFIG)


def stage_m8t(model, tok, store, heldout_items):
    run_condition_full(model, tok, store, heldout_items, k=8, tag="M8t", config=M8T_CONFIG)


def run_per_template(model, tok, store, heldout_items, *, k, tag, config):
    """2026-09-13 fix (coordinator request): a single global (tau_m,
    delta_m, lambda_prior) does not serve every question kind -- see
    evaluate.classify_question_template's docstring. Calibrates per-template
    on the SAME dev-slice construction (selection-correct label + synthetic
    absent-gold negatives) and evaluates on the 690 with the matching
    template's thresholds, alongside the already-computed global result."""
    t0 = time.perf_counter()
    cal = evaluate.calibrate_scoring_with_rank_prior_per_template(
        model, tok, store, evaluate.DEFAULT_TRAIN, k=k, n=500, seed=1,
        lambda_grid=(0.0, 0.25, 0.5, 1.0), frac_miss=0.2, config=config,
    )
    t1 = time.perf_counter()
    (RUN_DIR / f"calibration_{tag}_per_template.json").write_text(json.dumps(cal, indent=2))
    log({f"calibration_{tag}_per_template_wall_s": t1 - t0,
         "per_template": {t: {"tau_m": c["tau_m"], "delta_m": c["delta_m"], "lambda_prior": c["lambda_prior"],
                              "abstain_on_synthetic_miss": c["abstain_on_synthetic_miss"]}
                          for t, c in cal["per_template"].items()}})

    t2 = time.perf_counter()
    report = evaluate.run_condition_scoring_with_rank_prior_per_template(
        model, tok, store, heldout_items, RUN_DIR / f"{tag}_template",
        k=k, template_calibration=cal, heldout_path=evaluate.DEFAULT_HELDOUT, label=f"{tag}_template", config=config,
    )
    t3 = time.perf_counter()
    log({f"{tag}_template_wall_s": t3 - t2, f"{tag}_template_recall": report["score"]["overall"]["citation_recall"],
         f"{tag}_template_gold_selected": report["gold_selected_overall"],
         f"{tag}_template_per_kind": report["per_kind_gold_selected_rate"],
         f"{tag}_template_false_abstain_when_shown": report.get("false_abstain_when_shown"),
         f"{tag}_template_abstention_correct": report.get("abstention_correct")})
    return report, cal


def stage_m4t_template(model, tok, store, heldout_items):
    run_per_template(model, tok, store, heldout_items, k=4, tag="M4t", config=evaluate.PROMPT_CONFIG)


def stage_m8t_template(model, tok, store, heldout_items):
    run_per_template(model, tok, store, heldout_items, k=8, tag="M8t", config=M8T_CONFIG)


def stage_compare():
    pairs = [
        ("M4t", "S4t_f"), ("M4t", "P16f"),
        ("M8t", "S4t_f"), ("M8t", "P16f"), ("M8t", "M4t"),
        ("M4t_template", "S4t_f"), ("M4t_template", "M4t"),
        ("M8t_template", "S4t_f"), ("M8t_template", "M8t"),
    ]
    for a, b in pairs:
        a_dir = RUN_DIR / a
        b_dir = POINTWISE_DIR / b if (POINTWISE_DIR / b / "answers.jsonl").exists() else RUN_DIR / b
        if not (a_dir / "answers.jsonl").exists() or not (b_dir / "answers.jsonl").exists():
            log({"skip": f"{a}_vs_{b}", "reason": "answers.jsonl missing"})
            continue
        out = evaluate.compare(a_dir, b_dir, heldout_path=evaluate.DEFAULT_HELDOUT,
                               out_path=RUN_DIR / f"paired_{a}_vs_{b}.json")
        log({f"paired_{a}_vs_{b}": out.get("gold_selected", out.get("gold_citation_present", out))})


if __name__ == "__main__":
    stage = sys.argv[1] if len(sys.argv) > 1 else "compare"
    if stage == "compare":
        stage_compare()
        raise SystemExit(0)

    model, tok, store, heldout_items = load_everything()
    if stage == "m4t":
        stage_m4t(model, tok, store, heldout_items)
    elif stage == "m8t":
        stage_m8t(model, tok, store, heldout_items)
    elif stage == "m4t_template":
        stage_m4t_template(model, tok, store, heldout_items)
    elif stage == "m8t_template":
        stage_m8t_template(model, tok, store, heldout_items)
    else:
        raise SystemExit(f"unknown stage {stage!r}")
