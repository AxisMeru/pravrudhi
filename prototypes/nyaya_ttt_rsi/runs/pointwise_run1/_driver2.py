"""Second driver (run dir, not part of the module contract): recalibration
on selection-correctness (2026-09-13 fix for a degenerate gold-shown
calibration under high retrieval recall) plus a BM25-rank-prior sweep for
the k=4 scoring-mode tuned-store conditions. Run inside ttt-lab:
`docker exec ttt-lab python3 /lab/prototypes/nyaya_ttt_rsi/runs/pointwise_run1/_driver2.py <stage>`
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, "/lab")
from prototypes.nyaya_ttt_rsi import evaluate, model_io
from prototypes.nyaya_ttt_rsi import retrieval as retrieval_mod
from prototypes.nyaya_ttt_rsi import retrieval_tuning

RUN_DIR = Path("/lab/prototypes/nyaya_ttt_rsi/runs/pointwise_run1")
PERSISTENT_LORA = Path("/lab/prototypes/nyaya_ttt_rsi/runs/rsi_run1e/persistent_lora_round1.pt")
BASELINE_DIR = Path("/lab/prototypes/nyaya_ttt_rsi/runs/rsi_run1e")


def log(obj):
    print(json.dumps(obj, indent=2), flush=True)


def load_everything():
    model, tok = model_io.load_model(evaluate.DEFAULT_CHECKPOINT, device="cuda:0")
    store_plain = retrieval_mod.PassageStore.from_law_files(evaluate.DEFAULT_TRAIN, evaluate.DEFAULT_HELDOUT)
    best_config = json.loads(
        Path("/lab/prototypes/nyaya_ttt_rsi/runs/retrieval_tuning/results.json").read_text()
    )["best_config"]
    store_tuned = retrieval_tuning.TunedStore(store_plain.passages, **best_config)
    heldout_items = evaluate.load_jsonl(evaluate.DEFAULT_HELDOUT)
    return model, tok, store_plain, store_tuned, heldout_items


def load_persistent_lora(model):
    target_regex = evaluate.persistent_lora_target_regex(model)
    loras = evaluate._inject_lora(model, target_regex=target_regex, r=16, alpha=32)
    evaluate._load_lora_state(loras, PERSISTENT_LORA)
    return loras


def pointwise_condition(model, tok, store, heldout_items, *, k, tag):
    """Recalibrates on selection_correct (the new default), reruns the
    held-out with the corrected thresholds, and ALSO reruns forced-always-
    cite (tau_m=delta_m=-inf) to get the PRE-abstention selection accuracy
    (`gold_selected_overall` of that forced run) -- the number that tells
    us what the model/selector can do, separate from the abstention rule."""
    t0 = time.perf_counter()
    cal = evaluate.calibrate_pointwise(model, tok, store, evaluate.DEFAULT_TRAIN, k=k, n=500, seed=1, frac_miss=0.3)
    t1 = time.perf_counter()
    (RUN_DIR / f"calibration_{tag}.json").write_text(json.dumps(cal, indent=2))
    log({f"calibration_{tag}_wall_s": t1 - t0, "tau_m": cal["tau_m"], "delta_m": cal["delta_m"],
         "lambda_prior": cal["lambda_prior"], "balanced_accuracy": cal["balanced_accuracy"],
         "confusion": cal["confusion"]})

    config = evaluate.PointwiseConfig(k=k, lambda_prior=cal["lambda_prior"])
    t2 = time.perf_counter()
    report = evaluate.run_condition_pointwise(
        model, tok, store, heldout_items, RUN_DIR / tag,
        config=config, tau_m=cal["tau_m"], delta_m=cal["delta_m"], lambda_prior=cal["lambda_prior"],
        heldout_path=evaluate.DEFAULT_HELDOUT, label=tag,
    )
    t3 = time.perf_counter()
    log({f"{tag}_wall_s": t3 - t2, f"{tag}_gold_selected": report["gold_selected_overall"],
         f"{tag}_recall": report["score"]["overall"]["citation_recall"]})

    t4 = time.perf_counter()
    forced = evaluate.run_condition_pointwise(
        model, tok, store, heldout_items, RUN_DIR / f"{tag}_forced_cite",
        config=config, tau_m=float("-inf"), delta_m=float("-inf"), lambda_prior=cal["lambda_prior"],
        heldout_path=evaluate.DEFAULT_HELDOUT, label=f"{tag}_forced_cite",
    )
    t5 = time.perf_counter()
    log({f"{tag}_forced_wall_s": t5 - t4, f"{tag}_pre_abstention_selection_accuracy": forced["gold_selected_overall"]})
    return report, cal, forced


def scoring_prior_condition(model, tok, store, heldout_items, *, k, tag):
    """Scoring-mode analogue: BM25-rank-prior fusion + selection-correct
    calibration (`calibrate_scoring_with_rank_prior` /
    `run_condition_scoring_with_rank_prior`), plus a forced-always-cite
    pass for pre-abstention selection accuracy."""
    t0 = time.perf_counter()
    cal = evaluate.calibrate_scoring_with_rank_prior(model, tok, store, evaluate.DEFAULT_TRAIN, k=k, n=500, seed=1)
    t1 = time.perf_counter()
    (RUN_DIR / f"calibration_{tag}.json").write_text(json.dumps(cal, indent=2))
    log({f"calibration_{tag}_wall_s": t1 - t0, "tau_m": cal["tau_m"], "delta_m": cal["delta_m"],
         "lambda_prior": cal["lambda_prior"], "balanced_accuracy": cal["balanced_accuracy"],
         "lambda_grid": cal["lambda_grid"], "confusion": cal["confusion"]})

    t2 = time.perf_counter()
    report = evaluate.run_condition_scoring_with_rank_prior(
        model, tok, store, heldout_items, RUN_DIR / tag,
        k=k, tau_m=cal["tau_m"], delta_m=cal["delta_m"], lambda_prior=cal["lambda_prior"],
        heldout_path=evaluate.DEFAULT_HELDOUT, label=tag,
    )
    t3 = time.perf_counter()
    log({f"{tag}_wall_s": t3 - t2, f"{tag}_gold_selected": report["gold_selected_overall"],
         f"{tag}_recall": report["score"]["overall"]["citation_recall"]})

    t4 = time.perf_counter()
    forced = evaluate.run_condition_scoring_with_rank_prior(
        model, tok, store, heldout_items, RUN_DIR / f"{tag}_forced_cite",
        k=k, tau_m=float("-inf"), delta_m=float("-inf"), lambda_prior=cal["lambda_prior"],
        heldout_path=evaluate.DEFAULT_HELDOUT, label=f"{tag}_forced_cite",
    )
    t5 = time.perf_counter()
    log({f"{tag}_forced_wall_s": t5 - t4, f"{tag}_pre_abstention_selection_accuracy": forced["gold_selected_overall"]})
    return report, cal, forced


def stage_plain_frozen(model, tok, store_plain, heldout_items):
    for k, tag in ((8, "P8f"), (16, "P16f"), (32, "P32f")):
        pointwise_condition(model, tok, store_plain, heldout_items, k=k, tag=tag)


def stage_plain_lora(model, tok, store_plain, heldout_items):
    load_persistent_lora(model)
    pointwise_condition(model, tok, store_plain, heldout_items, k=32, tag="P32")


def stage_tuned_frozen(model, tok, store_tuned, heldout_items):
    scoring_prior_condition(model, tok, store_tuned, heldout_items, k=4, tag="S4t_f")
    pointwise_condition(model, tok, store_tuned, heldout_items, k=8, tag="P8t_f")


def stage_tuned_lora(model, tok, store_tuned, heldout_items):
    load_persistent_lora(model)
    scoring_prior_condition(model, tok, store_tuned, heldout_items, k=4, tag="S4t")
    pointwise_condition(model, tok, store_tuned, heldout_items, k=8, tag="P8t")


def stage_compare():
    pairs = [
        ("P32f", "Bfrozen"), ("P32f", "Bprime"),
        ("P32", "Bfrozen"), ("P32", "Bprime"),
        ("S4t_f", "Bfrozen"), ("P8t_f", "Bfrozen"), ("P8t_f", "P32f"),
        ("S4t", "Bprime"), ("P8t", "Bprime"), ("P8t", "P32"),
        ("S4t_f", "P8t_f"), ("S4t", "P8t"),
    ]
    for a, b in pairs:
        a_dir = RUN_DIR / a
        b_dir = RUN_DIR / b if (RUN_DIR / b / "answers.jsonl").exists() else BASELINE_DIR / b
        if not (a_dir / "answers.jsonl").exists() or not (b_dir / "answers.jsonl").exists():
            log({"skip": f"{a}_vs_{b}", "reason": "answers.jsonl missing"})
            continue
        out = evaluate.compare(a_dir, b_dir, heldout_path=evaluate.DEFAULT_HELDOUT,
                               out_path=RUN_DIR / f"paired_{a}_vs_{b}.json")
        log({f"paired_{a}_vs_{b}": out.get("gold_citation_present", out)})


if __name__ == "__main__":
    stage = sys.argv[1] if len(sys.argv) > 1 else "compare"
    if stage == "compare":
        stage_compare()
        raise SystemExit(0)

    model, tok, store_plain, store_tuned, heldout_items = load_everything()
    if stage == "plain_frozen":
        stage_plain_frozen(model, tok, store_plain, heldout_items)
    elif stage == "plain_lora":
        stage_plain_lora(model, tok, store_plain, heldout_items)
    elif stage == "tuned_frozen":
        stage_tuned_frozen(model, tok, store_tuned, heldout_items)
    elif stage == "tuned_lora":
        stage_tuned_lora(model, tok, store_tuned, heldout_items)
    else:
        raise SystemExit(f"unknown stage {stage!r}")
