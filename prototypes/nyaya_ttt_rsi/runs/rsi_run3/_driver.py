"""STEP 1 driver: RSI round 3 on the 370M, targeted at the title kind
(law_cite_to_title), through the gate.

Pipeline (one process, one model load, GPU serialized):
  1. Load the 370M + inject the persistent-LoRA-shaped adapter (r=16,
     alpha=32, evaluate.persistent_lora_target_regex), load round-1's saved
     state (runs/rsi_run1e/persistent_lora_round1.pt).
  2. Recalibrate abstention with THIS state (round-1 LoRA), k=4, tuned
     store, dev-chosen lambda grid (evaluate.calibrate_scoring_with_rank_prior,
     the same "current harness" S4t itself uses) -- "recalibrated", not
     reused from the old S4t calibration file.
  3. Build the round-3 pseudo-labeled training set (no gold read for
     labels): grounded_data.sample_round_stream (2x law_citation_retrieval
     oversample, excluding runs/rsi_run1e/grounded_sft.jsonl's own ids),
     grounded_data.DEFAULT_ABSTAIN_FRAC forced-miss subset (target=ABSTAIN_PHRASE,
     built the SAME way grounded_data.build_example's force_abstain path
     does), the remaining items pseudo-labeled by the recalibrated harness
     (accept only non-abstained; target = the model's OWN selected
     passage's canonical citation, never the record's own gold), plus a
     shuffled-passage second copy for every accepted law_cite_to_title item.
  4. Measure per-kind dev pre-abstention selection accuracy on a TRAIN dev
     slice (frac_miss=0, gold always shown) BEFORE training, at the
     round-1 LoRA state.
  5. Train: continue from the round-1 LoRA (loop.consolidate_from_dataset,
     lr=loop.CONSOLIDATE_LR, epochs=2, probe-gated with one halved-lr
     retry).
  6. Measure the SAME per-kind dev pre-abstention selection accuracy AFTER
     training, and combine with the probe decision via
     grounded_data.gate_round_accepts (probe <=15% relative regression AND
     no kind's dev accuracy drops >5 points) -- the task's stated gate,
     stricter than consolidate_from_dataset's probe-only gate.
  7. If accepted: save runs/rsi_run3/persistent_lora_round3.pt, recalibrate
     abstention again (state changed) with the dev-chosen lambda grid AND
     lambda=0, evaluate on the held-out 690 exactly as S4t, per-kind
     pre-abstention accuracy (forced-always-cite pass), paired vs S4t and
     S4t_f.
  8. If rejected: report why and stop -- no eval, no save, gate never
     loosened.

Run inside ttt-lab:
  docker exec ttt-lab python3 /lab/prototypes/nyaya_ttt_rsi/runs/rsi_run3/_driver.py run
  docker exec ttt-lab python3 /lab/prototypes/nyaya_ttt_rsi/runs/rsi_run3/_driver.py compare
"""
import json
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, "/lab")
from prototypes.nyaya_ttt_rsi import evaluate, grounded_data, loop as loop_mod, model_io
from prototypes.nyaya_ttt_rsi import retrieval as retrieval_mod
from prototypes.nyaya_ttt_rsi import retrieval_tuning
from prototypes.nyaya_ttt_rsi import gate as gate_mod

RUN_DIR = Path("/lab/prototypes/nyaya_ttt_rsi/runs/rsi_run3")
CHECKPOINT = evaluate.DEFAULT_CHECKPOINT
ROUND1_LORA = Path("/lab/prototypes/nyaya_ttt_rsi/runs/rsi_run1e/persistent_lora_round1.pt")
ROUND1_SFT_IDS_FILE = Path("/lab/prototypes/nyaya_ttt_rsi/runs/rsi_run1e/grounded_sft.jsonl")
POINTWISE_DIR = Path("/lab/prototypes/nyaya_ttt_rsi/runs/pointwise_run1")

N_STREAM = 1200
SEED = 3


def log(obj):
    print(json.dumps(obj, indent=2, default=str), flush=True)


def load_everything():
    model, tok = model_io.load_model(CHECKPOINT, device="cuda:0")
    best_config = json.loads(
        Path("/lab/prototypes/nyaya_ttt_rsi/runs/retrieval_tuning/results.json").read_text()
    )["best_config"]
    store_plain = retrieval_mod.PassageStore.from_law_files(evaluate.DEFAULT_TRAIN, evaluate.DEFAULT_HELDOUT)
    store = retrieval_tuning.TunedStore(store_plain.passages, **best_config)
    return model, tok, store


def load_round1_lora(model):
    target_regex = evaluate.persistent_lora_target_regex(model)
    loras = evaluate._inject_lora(model, target_regex=target_regex, r=16, alpha=32)
    evaluate._load_lora_state(loras, ROUND1_LORA)
    return loras


def per_kind_dev_selection_accuracy(model, tok, store, *, template_calibration: dict, k: int = 4,
                                     n: int = 300, seed: int = 99) -> dict:
    """Per-kind pre-abstention selection accuracy on a TRAIN dev slice with
    gold ALWAYS shown (frac_miss=0) -- the "can the selector pick gold when
    gold is there" number the round-3 gate compares before vs after
    training, independent of abstention. Uses each kind's own template
    lambda_prior (kind name == template name for every citation kind on
    this corpus -- see evaluate.classify_question_template), not a single
    global lambda, consistent with how the dataset itself was built."""
    per_template = template_calibration["per_template"]
    global_cal = template_calibration["global"]
    examples = evaluate.build_natural_dev_examples(evaluate.DEFAULT_TRAIN, store, k=k, n=n, seed=seed, frac_miss=0.0)
    scored = evaluate.score_scoring_mode_dev_examples(model, tok, examples)
    # score_scoring_mode_dev_examples silently drops any example whose
    # candidate list is empty (build_candidates(..., include_abstain=False)
    # on zero passages) -- re-derive the SAME filter here so `scored` and
    # `kinds` stay aligned by position (mirrors evaluate.
    # calibrate_scoring_with_rank_prior_per_template's own re-zip fix).
    kinds = [e["kind"] for e in examples
             if evaluate.build_candidates(e["passages"], e["kind"], include_abstain=False)]
    by_kind: dict[str, list[bool]] = {}
    for se, kind in zip(scored, kinds):
        if kind == "law_abstain":
            continue
        ex = {"kind": kind}
        lambda_prior = per_template.get(ex["kind"], global_cal)["lambda_prior"]
        sel = evaluate.select_scoring_with_rank_prior(se["nlls"], lambda_prior)
        idx = sel["best_idx"]
        correct = (idx is not None and se["sources"][idx] is not None
                   and (se["sources"][idx].act, se["sources"][idx].section) == (se["act"], se["section"]))
        by_kind.setdefault(ex["kind"], []).append(correct)
    return {kind: sum(vals) / len(vals) for kind, vals in by_kind.items() if vals}


def build_round3_dataset(model, tok, store, *, template_calibration, seed=SEED):
    """`template_calibration` is a `calibrate_scoring_with_rank_prior_per_template`
    result: each item's own question is classified by
    `evaluate.classify_question_template` (surface form only) and
    pseudo-labeled with THAT template's (tau_m, delta_m, lambda_prior) --
    2026-09-13 fix, since a single global threshold abstains on ~91% of
    title-kind items while abstaining on ~0% of the other two (see
    evaluate.classify_question_template's docstring)."""
    per_template = template_calibration["per_template"]
    global_cal = template_calibration["global"]
    exclude_ids = set()
    if ROUND1_SFT_IDS_FILE.exists():
        exclude_ids = {r["id"] for r in evaluate.load_jsonl(ROUND1_SFT_IDS_FILE)}
        exclude_source = str(ROUND1_SFT_IDS_FILE)
    else:
        exclude_source = None

    records = evaluate.load_jsonl(evaluate.DEFAULT_TRAIN)
    sampled = grounded_data.sample_round_stream(
        records, N_STREAM, oversample_kind="law_citation_retrieval", oversample_factor=2.0,
        exclude_ids=exclude_ids, seed=seed,
    )

    rng = random.Random(seed)
    n_abstain = int(round(len(sampled) * grounded_data.DEFAULT_ABSTAIN_FRAC))
    abstain_ids = set(id(r) for r in rng.sample(sampled, n_abstain)) if n_abstain else set()

    examples: list[dict] = []
    n_forced_abstain = n_pseudo_accepted = n_pseudo_rejected = n_purity_correct = 0
    n_shuffle_copies = n_rejected_ungrounded = 0
    by_kind_accepted: dict[str, int] = {}

    for rec in sampled:
        act, section = rec["act"], rec["section"]
        gold = store.lookup(act, section)
        if gold is None:
            continue

        if id(rec) in abstain_ids:
            passages = grounded_data._select_passages(store, rec["prompt"], gold, evaluate.K_PASSAGES, exclude_gold=True)
            rng.shuffle(passages)
            prompt, trunc, _pb, _dropped = evaluate.render_prompt(rec["prompt"], passages, evaluate.PROMPT_CONFIG)
            target = retrieval_mod.ABSTAIN_PHRASE
            if not retrieval_mod.grounded(retrieval_mod.parse_answer(target), trunc):
                n_rejected_ungrounded += 1
                continue
            n_forced_abstain += 1
            examples.append({"id": rec["id"], "kind": rec["kind"], "prompt": prompt,
                             "target": target + grounded_data.TARGET_STOP_SUFFIX, "synthetic_abstain": True,
                             "pseudo": False})
            continue

        # Natural retrieval (no gold forcing) -- this is what the harness
        # actually sees at inference time, and what it is being pseudo-labeled on.
        passages = store.search(rec["prompt"], evaluate.K_PASSAGES)
        if not passages:
            continue
        prompt, trunc, _pb, _dropped = evaluate.render_prompt(rec["prompt"], passages, evaluate.PROMPT_CONFIG)
        candidates = evaluate.build_candidates(trunc, rec["kind"], include_abstain=False)
        if not candidates:
            continue
        tmpl = evaluate.classify_question_template(rec["prompt"])
        cal = per_template.get(tmpl, global_cal)
        tau_m, delta_m, lambda_prior = cal["tau_m"], cal["delta_m"], cal["lambda_prior"]
        result = evaluate.score_candidates(model, tok, prompt, candidates)
        sel = evaluate.select_scoring_with_rank_prior(result["total_nlls"], lambda_prior)
        best_score, gap = sel["best_score"], sel["gap"]
        should_abstain = (best_score is None) or (best_score < tau_m) or (gap < delta_m)
        if should_abstain:
            n_pseudo_rejected += 1
            continue

        _cand_text, picked = candidates[sel["best_idx"]]
        target = evaluate.CANONICAL_CITATION_FORMAT.format(section=picked.section, act=picked.act)
        if not retrieval_mod.grounded(retrieval_mod.parse_answer(target), trunc):
            n_rejected_ungrounded += 1
            continue

        is_correct = (picked.act, picked.section) == (act, section)  # purity: record only, never used as a label
        n_pseudo_accepted += 1
        n_purity_correct += int(is_correct)
        by_kind_accepted[rec["kind"]] = by_kind_accepted.get(rec["kind"], 0) + 1

        full_target = target + grounded_data.TARGET_STOP_SUFFIX
        examples.append({"id": rec["id"], "kind": rec["kind"], "prompt": prompt, "target": full_target,
                         "synthetic_abstain": False, "pseudo": True, "pseudo_correct": is_correct})

        if rec["kind"] == "law_cite_to_title":
            copy = grounded_data.shuffle_passages_copy(prompt, full_target, trunc, rng)
            if copy is not None:
                n_shuffle_copies += 1
                examples.append({"id": rec["id"] + ":shuffled", "kind": rec["kind"], "prompt": copy["prompt"],
                                 "target": copy["target"], "synthetic_abstain": False, "pseudo": True,
                                 "pseudo_correct": is_correct})

    stats = {
        "n_sampled": len(sampled),
        "exclude_ids_source": exclude_source, "n_excluded_available": len(exclude_ids),
        "n_forced_abstain": n_forced_abstain,
        "n_pseudo_accepted": n_pseudo_accepted, "n_pseudo_rejected_abstained": n_pseudo_rejected,
        "n_rejected_ungrounded": n_rejected_ungrounded,
        "purity": n_purity_correct / n_pseudo_accepted if n_pseudo_accepted else 0.0,
        "n_shuffle_copies_added": n_shuffle_copies,
        "by_kind_accepted": by_kind_accepted,
        "n_examples_total": len(examples),
    }
    return examples, stats


def stage_run():
    t_start = time.perf_counter()
    model, tok, store = load_everything()
    loras = load_round1_lora(model)
    persistent_snap = evaluate._snapshot(loras)

    # 2026-09-13 fix: per-TEMPLATE calibration (evaluate.classify_question_
    # template + calibrate_scoring_with_rank_prior_per_template), not one
    # global (tau_m, delta_m, lambda_prior) -- a single global threshold was
    # found (STEP 0, M4t) to abstain on ~91% of title-kind items while
    # abstaining on ~0% of the other two. This is "the current harness" the
    # task asks round 3 to pseudo-label with.
    t0 = time.perf_counter()
    cal = evaluate.calibrate_scoring_with_rank_prior_per_template(
        model, tok, store, evaluate.DEFAULT_TRAIN, k=4, n=500, seed=1,
        lambda_grid=(0.0, 0.25, 0.5, 1.0), frac_miss=0.2,
    )
    t1 = time.perf_counter()
    (RUN_DIR / "calibration_harness_round1.json").write_text(json.dumps(cal, indent=2))
    log({"calibrate_harness_wall_s": t1 - t0,
         "per_template": {t: {"tau_m": c["tau_m"], "delta_m": c["delta_m"], "lambda_prior": c["lambda_prior"],
                              "abstain_on_synthetic_miss": c["abstain_on_synthetic_miss"]}
                          for t, c in cal["per_template"].items()},
         "global": {"tau_m": cal["global"]["tau_m"], "lambda_prior": cal["global"]["lambda_prior"]}})

    t2 = time.perf_counter()
    examples, data_stats = build_round3_dataset(model, tok, store, template_calibration=cal)
    t3 = time.perf_counter()
    evaluate.write_jsonl(RUN_DIR / "round3_pseudo_sft.jsonl", examples)
    (RUN_DIR / "round3_data_stats.json").write_text(json.dumps(data_stats, indent=2))
    log({"build_data_wall_s": t3 - t2, **data_stats})

    t4 = time.perf_counter()
    dev_before = per_kind_dev_selection_accuracy(model, tok, store, template_calibration=cal)
    t5 = time.perf_counter()
    log({"dev_before_wall_s": t5 - t4, "dev_before": dev_before})

    probe = gate_mod.RegressionProbe.from_files(str(evaluate.DEFAULT_PROBE_GENERAL), n_general=16, seed=0)
    t6 = time.perf_counter()
    accepted_probe, train_stats, new_snap = loop_mod.consolidate_from_dataset(
        model, tok, loras, RUN_DIR / "round3_pseudo_sft.jsonl", probe, persistent_snap,
        lr=loop_mod.CONSOLIDATE_LR, epochs=2, threshold=loop_mod.CONSOLIDATE_THRESHOLD, log_every=200,
    )
    t7 = time.perf_counter()
    log({"train_wall_s": t7 - t6, "accepted_probe": accepted_probe,
         "probe_delta_rel": train_stats["probe_delta_rel"], "reason": train_stats["reason"]})
    (RUN_DIR / "train_stats.json").write_text(json.dumps(train_stats, indent=2))

    evaluate._restore(loras, new_snap)
    t8 = time.perf_counter()
    dev_after = per_kind_dev_selection_accuracy(model, tok, store, template_calibration=cal)
    t9 = time.perf_counter()
    log({"dev_after_wall_s": t9 - t8, "dev_after": dev_after})

    accepted, reason = grounded_data.gate_round_accepts(
        train_stats["probe_delta_rel"], dev_before, dev_after,
        probe_threshold=loop_mod.CONSOLIDATE_THRESHOLD, max_kind_drop=0.05,
    )
    gate_decision = {"accepted": accepted, "reason": reason, "dev_before": dev_before, "dev_after": dev_after,
                     "probe_delta_rel": train_stats["probe_delta_rel"], "accepted_probe_only": accepted_probe}
    (RUN_DIR / "gate_decision.json").write_text(json.dumps(gate_decision, indent=2))
    log({"GATE_DECISION": gate_decision})

    report = {"data_stats": data_stats, "train_stats": {k: v for k, v in train_stats.items() if k != "logged_losses"},
              "dev_before": dev_before, "dev_after": dev_after, "gate": gate_decision,
              "harness_calibration": {k: v for k, v in cal.items() if k != "grid"},
              "wall_clock_seconds_total": time.perf_counter() - t_start}

    if not accepted:
        evaluate._restore(loras, persistent_snap)
        (RUN_DIR / "report.json").write_text(json.dumps(report, indent=2))
        log({"STOP": "gate rejected round 3 -- not saving, not evaluating", "reason": reason})
        return

    evaluate._save_lora_state(loras, RUN_DIR / "persistent_lora_round3.pt")

    t10 = time.perf_counter()
    cal3 = evaluate.calibrate_scoring_with_rank_prior(
        model, tok, store, evaluate.DEFAULT_TRAIN, k=4, n=500, seed=1,
        lambda_grid=(0.0, 0.25, 0.5, 1.0), frac_miss=0.2,
    )
    t11 = time.perf_counter()
    (RUN_DIR / "calibration_round3.json").write_text(json.dumps(cal3, indent=2))
    log({"calibrate_round3_wall_s": t11 - t10, "tau_m": cal3["tau_m"], "delta_m": cal3["delta_m"],
         "lambda_prior": cal3["lambda_prior"]})

    heldout_items = evaluate.load_jsonl(evaluate.DEFAULT_HELDOUT)
    t12 = time.perf_counter()
    eval_report = evaluate.run_condition_scoring_with_rank_prior(
        model, tok, store, heldout_items, RUN_DIR / "S4t_round3",
        k=4, tau_m=cal3["tau_m"], delta_m=cal3["delta_m"], lambda_prior=cal3["lambda_prior"],
        heldout_path=evaluate.DEFAULT_HELDOUT, label="S4t_round3",
    )
    t13 = time.perf_counter()
    log({"eval_wall_s": t13 - t12, "eval_recall": eval_report["score"]["overall"]["citation_recall"],
         "eval_gold_selected": eval_report["gold_selected_overall"]})

    t14 = time.perf_counter()
    forced = evaluate.run_condition_scoring_with_rank_prior(
        model, tok, store, heldout_items, RUN_DIR / "S4t_round3_forced_cite",
        k=4, tau_m=float("-inf"), delta_m=float("-inf"), lambda_prior=cal3["lambda_prior"],
        heldout_path=evaluate.DEFAULT_HELDOUT, label="S4t_round3_forced_cite",
    )
    t15 = time.perf_counter()
    log({"forced_wall_s": t15 - t14, "pre_abstention_selection_accuracy": forced["gold_selected_overall"]})

    t16 = time.perf_counter()
    cal3_l0 = evaluate.calibrate_scoring_with_rank_prior(
        model, tok, store, evaluate.DEFAULT_TRAIN, k=4, n=500, seed=1, lambda_grid=(0.0,), frac_miss=0.2,
    )
    eval_l0 = evaluate.run_condition_scoring_with_rank_prior(
        model, tok, store, heldout_items, RUN_DIR / "S4t_round3_lambda0",
        k=4, tau_m=cal3_l0["tau_m"], delta_m=cal3_l0["delta_m"], lambda_prior=0.0,
        heldout_path=evaluate.DEFAULT_HELDOUT, label="S4t_round3_lambda0",
    )
    t17 = time.perf_counter()
    log({"lambda0_wall_s": t17 - t16, "lambda0_recall": eval_l0["score"]["overall"]["citation_recall"]})

    # 2026-09-13 fix: ALSO report the per-template-calibrated eval (the
    # actual harness round 3 was pseudo-labeled with) alongside the global
    # (S4t-comparable) numbers above.
    t18 = time.perf_counter()
    cal3_template = evaluate.calibrate_scoring_with_rank_prior_per_template(
        model, tok, store, evaluate.DEFAULT_TRAIN, k=4, n=500, seed=1,
        lambda_grid=(0.0, 0.25, 0.5, 1.0), frac_miss=0.2,
    )
    t19 = time.perf_counter()
    (RUN_DIR / "calibration_round3_per_template.json").write_text(json.dumps(cal3_template, indent=2))
    eval_template = evaluate.run_condition_scoring_with_rank_prior_per_template(
        model, tok, store, heldout_items, RUN_DIR / "S4t_round3_template",
        k=4, template_calibration=cal3_template, heldout_path=evaluate.DEFAULT_HELDOUT, label="S4t_round3_template",
    )
    t20 = time.perf_counter()
    log({"template_calibration_wall_s": t19 - t18, "template_eval_wall_s": t20 - t19,
         "template_recall": eval_template["score"]["overall"]["citation_recall"],
         "template_gold_selected": eval_template["gold_selected_overall"],
         "template_per_kind": eval_template["per_kind_gold_selected_rate"],
         "template_false_abstain_when_shown": eval_template.get("false_abstain_when_shown"),
         "template_abstention_correct": eval_template.get("abstention_correct")})

    report["eval"] = eval_report
    report["eval_forced_pre_abstention"] = forced["gold_selected_overall"]
    report["eval_lambda0"] = {"recall": eval_l0["score"]["overall"]["citation_recall"],
                              "gold_selected": eval_l0["gold_selected_overall"]}
    report["eval_per_template"] = eval_template
    report["wall_clock_seconds_total"] = time.perf_counter() - t_start
    (RUN_DIR / "report.json").write_text(json.dumps(report, indent=2))
    log({"DONE": True, "wall_clock_seconds_total": report["wall_clock_seconds_total"]})


def stage_compare():
    pairs = [("S4t_round3", "S4t"), ("S4t_round3", "S4t_f"),
             ("S4t_round3_template", "S4t"), ("S4t_round3_template", "S4t_f"),
             ("S4t_round3_template", "S4t_round3")]
    for a, b in pairs:
        a_dir = RUN_DIR / a
        b_dir = RUN_DIR / b if (RUN_DIR / b / "answers.jsonl").exists() else POINTWISE_DIR / b
        if not (a_dir / "answers.jsonl").exists() or not (b_dir / "answers.jsonl").exists():
            log({"skip": f"{a}_vs_{b}", "reason": "answers.jsonl missing"})
            continue
        out = evaluate.compare(a_dir, b_dir, heldout_path=evaluate.DEFAULT_HELDOUT,
                               out_path=RUN_DIR / f"paired_{a}_vs_{b}.json")
        log({f"paired_{a}_vs_{b}": out.get("gold_selected", out.get("gold_citation_present", out))})


if __name__ == "__main__":
    stage = sys.argv[1] if len(sys.argv) > 1 else "run"
    if stage == "run":
        stage_run()
    elif stage == "compare":
        stage_compare()
    else:
        raise SystemExit(f"unknown stage {stage!r}")
