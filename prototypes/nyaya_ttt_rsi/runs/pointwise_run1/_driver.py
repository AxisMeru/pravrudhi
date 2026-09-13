"""One-off driver (run dir, not part of the module contract): pointwise
grounded-judgment suite (plain-BM25 P8f/P16f/P32f/P32, tuned-store
S4t_f/S4t/P8t_f/P8t) for `pointwise_run1`. Run inside ttt-lab:
`docker exec ttt-lab python3 /lab/prototypes/nyaya_ttt_rsi/runs/pointwise_run1/_driver.py <stage>`
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, "/lab")
from prototypes.nyaya_ttt_rsi import evaluate, gate as gate_mod, model_io
from prototypes.nyaya_ttt_rsi import retrieval as retrieval_mod
from prototypes.nyaya_ttt_rsi import retrieval_tuning
from prototypes.nyaya_ttt_rsi import loop as loop_mod

RUN_DIR = Path("/lab/prototypes/nyaya_ttt_rsi/runs/pointwise_run1")
RUN_DIR.mkdir(parents=True, exist_ok=True)
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
    return model, tok, store_plain, store_tuned, heldout_items, best_config


def stage_recall(model, tok, store_plain, store_tuned, heldout_items):
    """Own-run retrieval recall@k for both stores on the real 690 held-out
    (cheap, CPU-only, no GPU) -- provenance for the recall numbers quoted
    in the report, not taken on trust from the tuning agent's own claim."""
    out = {"plain": {}, "tuned": {}}
    for k in (4, 8, 16, 32):
        out["plain"][k] = retrieval_mod.retrieval_recall(store_plain, evaluate.DEFAULT_HELDOUT, k)
        out["tuned"][k] = retrieval_mod.retrieval_recall(store_tuned, evaluate.DEFAULT_HELDOUT, k)
    (RUN_DIR / "recall.json").write_text(json.dumps(out, indent=2))
    log(out)


def load_persistent_lora(model):
    target_regex = evaluate.persistent_lora_target_regex(model)
    loras = evaluate._inject_lora(model, target_regex=target_regex, r=16, alpha=32)
    evaluate._load_lora_state(loras, PERSISTENT_LORA)
    return loras


def calibrate_and_run_pointwise(model, tok, store, heldout_items, *, k, tag, lora_loaded):
    t0 = time.perf_counter()
    cal = evaluate.calibrate_pointwise(model, tok, store, evaluate.DEFAULT_TRAIN, k=k, n=500, seed=1, frac_miss=0.3)
    t1 = time.perf_counter()
    (RUN_DIR / f"calibration_{tag}.json").write_text(json.dumps(cal, indent=2))
    log({f"calibration_{tag}_wall_s": t1 - t0, "tau_m": cal["tau_m"], "delta_m": cal["delta_m"],
         "lambda_prior": cal["lambda_prior"], "balanced_accuracy": cal["balanced_accuracy"]})

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
    return report, cal


def calibrate_and_run_scoring(model, tok, store, heldout_items, *, k, tag):
    t0 = time.perf_counter()
    cal = loop_mod.calibrate_abstention(model, tok, store, evaluate.DEFAULT_TRAIN, n=500, seed=1, frac_miss=0.3, k=k)
    t1 = time.perf_counter()
    (RUN_DIR / f"calibration_{tag}.json").write_text(json.dumps(cal, indent=2))
    log({f"calibration_{tag}_wall_s": t1 - t0, "tau": cal["tau"], "delta": cal["delta"],
         "balanced_accuracy": cal["balanced_accuracy"]})

    t2 = time.perf_counter()
    report = evaluate.run_condition_calibrated(
        "B", model, tok, store, heldout_items, RUN_DIR / tag,
        tau=cal["tau"], delta=cal["delta"], k=k, heldout_path=evaluate.DEFAULT_HELDOUT, label=tag,
    )
    t3 = time.perf_counter()
    log({f"{tag}_wall_s": t3 - t2, f"{tag}_gold_selected": report["gold_selected_overall"],
         f"{tag}_recall": report["score"]["overall"]["citation_recall"]})
    return report, cal


def stage_smoke(model, tok, store_plain, heldout_items):
    """Cheap timing probe: k=8 pointwise, limit=20 items, calibration n=40 --
    to measure per-item wall time before committing to the full 690-item /
    500-dev-example runs."""
    items = heldout_items[:20]
    t0 = time.perf_counter()
    cal = evaluate.calibrate_pointwise(model, tok, store_plain, evaluate.DEFAULT_TRAIN, k=8, n=40, seed=1, frac_miss=0.3)
    t1 = time.perf_counter()
    config = evaluate.PointwiseConfig(k=8, lambda_prior=cal["lambda_prior"])
    report = evaluate.run_condition_pointwise(
        model, tok, store_plain, items, RUN_DIR / "_smoke",
        config=config, tau_m=cal["tau_m"], delta_m=cal["delta_m"], lambda_prior=cal["lambda_prior"],
        heldout_path=evaluate.DEFAULT_HELDOUT, label="smoke",
    )
    t2 = time.perf_counter()
    log({
        "calibration_wall_s_for_40_examples": t1 - t0,
        "eval_wall_s_for_20_items_k8": t2 - t1,
        "per_item_s": (t2 - t1) / 20,
        "cal": {k: v for k, v in cal.items() if k != "grid"},
    })


def stage_plain_frozen(model, tok, store_plain, heldout_items):
    for k in (8, 16, 32):
        calibrate_and_run_pointwise(model, tok, store_plain, heldout_items, k=k, tag=f"P{k}f", lora_loaded=False)


def stage_plain_lora(model, tok, store_plain, heldout_items):
    loras = load_persistent_lora(model)
    calibrate_and_run_pointwise(model, tok, store_plain, heldout_items, k=32, tag="P32", lora_loaded=True)


def stage_tuned_frozen(model, tok, store_tuned, heldout_items):
    calibrate_and_run_scoring(model, tok, store_tuned, heldout_items, k=4, tag="S4t_f")
    calibrate_and_run_pointwise(model, tok, store_tuned, heldout_items, k=8, tag="P8t_f", lora_loaded=False)


def stage_tuned_lora(model, tok, store_tuned, heldout_items):
    loras = load_persistent_lora(model)
    calibrate_and_run_scoring(model, tok, store_tuned, heldout_items, k=4, tag="S4t")
    calibrate_and_run_pointwise(model, tok, store_tuned, heldout_items, k=8, tag="P8t", lora_loaded=True)


def stage_compare():
    pairs = [
        ("P32f", "Bfrozen"), ("P32f", "Bprime"),
        ("P32", "Bfrozen"), ("P32", "Bprime"),
        ("S4t_f", "Bfrozen"), ("P8t_f", "Bfrozen"), ("P8t_f", "P32f"),
        ("S4t", "Bprime"), ("P8t", "Bprime"), ("P8t", "P32"),
    ]
    for a, b in pairs:
        a_dir = RUN_DIR / a if (RUN_DIR / a / "answers.jsonl").exists() else None
        b_dir = RUN_DIR / b if (RUN_DIR / b / "answers.jsonl").exists() else BASELINE_DIR / b
        if a_dir is None or not (RUN_DIR / a / "answers.jsonl").exists() or not b_dir.exists():
            log({"skip": f"{a}_vs_{b}", "reason": "answers.jsonl missing"})
            continue
        out = evaluate.compare(RUN_DIR / a, b_dir, heldout_path=evaluate.DEFAULT_HELDOUT,
                               out_path=RUN_DIR / f"paired_{a}_vs_{b}.json")
        log({f"paired_{a}_vs_{b}": out.get("gold_citation_present", out)})


if __name__ == "__main__":
    stage = sys.argv[1] if len(sys.argv) > 1 else "smoke"
    if stage == "compare":
        stage_compare()
        raise SystemExit(0)
    if stage == "recall":
        model, tok, store_plain, store_tuned, heldout_items, best_config = load_everything()
        stage_recall(model, tok, store_plain, store_tuned, heldout_items)
        raise SystemExit(0)

    model, tok, store_plain, store_tuned, heldout_items, best_config = load_everything()
    if stage == "smoke":
        stage_smoke(model, tok, store_plain, heldout_items)
    elif stage == "plain_frozen":
        stage_plain_frozen(model, tok, store_plain, heldout_items)
    elif stage == "plain_lora":
        stage_plain_lora(model, tok, store_plain, heldout_items)
    elif stage == "tuned_frozen":
        stage_tuned_frozen(model, tok, store_tuned, heldout_items)
    elif stage == "tuned_lora":
        stage_tuned_lora(model, tok, store_tuned, heldout_items)
    else:
        raise SystemExit(f"unknown stage {stage!r}")
