"""One-off assembly (run dir, not part of the module contract): builds
report.json in the schema prototypes/nyaya_ttt_rsi/report.py expects, from
the raw artifacts _driver.py/_driver2.py already wrote for pointwise_run1.
Reflects the 2026-09-13 selection-correctness recalibration (see
_driver2.py) -- the *_by_gold_shown directories/calibration files are the
superseded, degenerate-calibration results, kept on disk for the record
but not rendered as headline conditions here (summarized in a note only)."""
import json
import sys
from pathlib import Path

sys.path.insert(0, "/lab")
from prototypes.nyaya_ttt_rsi import stats

RUN_DIR = Path("/lab/prototypes/nyaya_ttt_rsi/runs/pointwise_run1")
BASELINE_DIR = Path("/lab/prototypes/nyaya_ttt_rsi/runs/rsi_run1e")


def load(p):
    return json.loads(Path(p).read_text())


def try_load(p):
    p = Path(p)
    return json.loads(p.read_text()) if p.exists() else None


def wilson_metric(successes, total):
    if total == 0:
        return {"rate": 0.0, "k": 0, "n": 0, "wilson": [0.0, 1.0]}
    lo, hi = stats.wilson(successes, total)
    return {"rate": successes / total, "k": successes, "n": total, "wilson": [lo, hi]}


def metric_from_counter(d):
    return wilson_metric(d["successes"], d["total"])


def condition_metrics(report, forced_report=None):
    m = {}
    score = report.get("score")
    if score and "overall" in score:
        m["citation_recall"] = metric_from_counter(score["overall"]["citation_recall"])
        m["citation_precision"] = metric_from_counter(score["overall"]["citation_precision"])
        m["abstention_correctness"] = metric_from_counter(score["overall"]["abstention_correctness"])
        if "law_lookup" in score:
            m["law_lookup_prefix_similarity_mean"] = {"rate": score["law_lookup"]["prefix_similarity_mean"]}
    if "gold_selected_overall" in report:
        m["gold_selected"] = metric_from_counter(report["gold_selected_overall"])
    if report.get("abstain_on_miss"):
        m["abstain_on_miss"] = metric_from_counter(report["abstain_on_miss"])
    if report.get("false_abstain_when_shown"):
        m["false_abstain_when_shown"] = metric_from_counter(report["false_abstain_when_shown"])
    per_kind = report.get("per_kind_gold_selected_rate", {})
    if "law_citation_retrieval" in per_kind:
        m["law_citation_retrieval_kind_gold_selected"] = metric_from_counter(per_kind["law_citation_retrieval"])
    if forced_report is not None and "gold_selected_overall" in forced_report:
        m["pre_abstention_selection_accuracy"] = metric_from_counter(forced_report["gold_selected_overall"])
        forced_per_kind = forced_report.get("per_kind_gold_selected_rate", {})
        if "law_citation_retrieval" in forced_per_kind:
            m["pre_abstention_law_citation_retrieval_kind"] = metric_from_counter(
                forced_per_kind["law_citation_retrieval"])
    return m


def timing(report):
    wall = report.get("wall_clock_seconds", {})
    total = wall.get("total", 0.0)
    vram = report.get("peak_vram_mib")
    return {"wall_s": round(total, 1), "peak_vram_gib": round(vram / 1024, 2) if vram else None}


def paired_rows(paired, a_label, b_label, metric_key="gold_citation_present", metric_name="gold-citation present (690, lenient substring match)"):
    rows = []
    p = paired.get(metric_key)
    if not p or p.get("n", 0) == 0:
        return rows
    rows.append({
        "a": a_label, "b": b_label, "metric": metric_name,
        "b_only": p["mcnemar_b"], "c_only": p["mcnemar_c"],
        "mcnemar_p": p["mcnemar_p"], "bootstrap_ci": p["bootstrap_diff_95ci"],
    })
    return rows


def main():
    conditions = {}
    notes = []
    paired_all = []

    named = {
        "B'(frozen)": BASELINE_DIR / "Bfrozen",
        "B'": BASELINE_DIR / "Bprime",
        "P8f": RUN_DIR / "P8f",
        "P16f": RUN_DIR / "P16f",
        "P32f": RUN_DIR / "P32f",
        "P32": RUN_DIR / "P32",
        "S4t_f": RUN_DIR / "S4t_f",
        "S4t": RUN_DIR / "S4t",
        "P8t_f": RUN_DIR / "P8t_f",
        "P8t": RUN_DIR / "P8t",
    }
    descriptions = {
        "B'(frozen)": "k=4 scoring mode, plain BM25, frozen 370M (rsi_run1e baseline)",
        "B'": "k=4 scoring mode, plain BM25, round-1 consolidated LoRA (rsi_run1e baseline)",
        "P8f": "pointwise (1 passage/prompt) k=8, plain BM25, frozen 370M, selection-correct calib.",
        "P16f": "pointwise k=16, plain BM25, frozen 370M, selection-correct calib.",
        "P32f": "pointwise k=32, plain BM25, frozen 370M, selection-correct calib.",
        "P32": "pointwise k=32, plain BM25, round-1 consolidated LoRA, selection-correct calib.",
        "S4t_f": "k=4 scoring mode + BM25-rank prior, TUNED retrieval, frozen 370M, selection-correct calib.",
        "S4t": "k=4 scoring mode + BM25-rank prior, TUNED retrieval, round-1 LoRA, selection-correct calib.",
        "P8t_f": "pointwise k=8, TUNED retrieval, frozen 370M, selection-correct calib.",
        "P8t": "pointwise k=8, TUNED retrieval, round-1 consolidated LoRA, selection-correct calib.",
    }
    forced_dirs = {name: RUN_DIR / f"{name}_forced_cite" for name in named if name not in ("B'(frozen)", "B'")}

    for name, path in named.items():
        report = try_load(path / "report.json")
        if report is None:
            notes.append(f"{name}: report.json missing at {path} -- run did not complete, omitted.")
            continue
        forced_report = try_load(forced_dirs[name] / "report.json") if name in forced_dirs else None
        conditions[name] = {"name": descriptions[name], "metrics": condition_metrics(report, forced_report),
                             "timing": timing(report)}

    # (a, b, b's display label) -- filenames use the raw driver tags
    # (Bfrozen/Bprime, matching rsi_run1e's own directory names), display
    # labels use the report-schema names (B'(frozen)/B').
    pair_specs = [
        ("P16f", "Bfrozen", "B'(frozen)"), ("P16f", "Bprime", "B'"), ("P16f", "P32f", "P32f"),
        ("P32f", "Bfrozen", "B'(frozen)"), ("P32f", "Bprime", "B'"),
        ("P32", "Bfrozen", "B'(frozen)"), ("P32", "Bprime", "B'"),
        ("S4t_f", "Bfrozen", "B'(frozen)"), ("P8t_f", "Bfrozen", "B'(frozen)"), ("P8t_f", "P32f", "P32f"),
        ("S4t", "Bprime", "B'"), ("P8t", "Bprime", "B'"), ("P8t", "P32", "P32"),
        ("S4t_f", "P8t_f", "P8t_f"), ("S4t", "P8t", "P8t"),
    ]
    for a, b_tag, b_label in pair_specs:
        pfile = RUN_DIR / f"paired_{a}_vs_{b_tag}.json"
        paired = try_load(pfile)
        if paired is None:
            notes.append(f"paired comparison {a} vs {b_label}: {pfile.name} missing.")
            continue
        paired_all += paired_rows(paired, a, b_label)
        paired_all += paired_rows(paired, a, b_label, metric_key="gold_selected",
                                  metric_name="gold selected (index-exact, only where both conditions report it)")

    recall = try_load(RUN_DIR / "recall.json")
    if recall:
        notes.append(f"Own-run BM25 retrieval recall@k (690 held-out): plain {recall['plain']}; "
                     f"tuned (retrieval_tuning.TunedStore, best_config) {recall['tuned']}.")

    notes.append(
        "2026-09-13 calibration correction: the FIRST calibration pass (kept on disk as "
        "*_by_gold_shown for the record) labeled a dev example positive whenever the gold passage "
        "was merely SHOWN among the retrieved top-k, regardless of whether the model's own arg-max "
        "actually picked it. With the tuned store's recall@4=0.98 this made the dev negative class "
        "nearly empty, and the resulting thresholds abstained on 72-82% of items where gold WAS "
        "shown (false_abstain_when_shown), collapsing citation_recall to ~0.02-0.06 across the board "
        "even though the underlying selector was fine. All numbers in this report's `conditions` and "
        "`paired` sections use the corrected label instead: positive iff the fused-score arg-max "
        "equals the item's own gold passage (see evaluate.calibrate_pointwise's and "
        "evaluate.calibrate_scoring_with_rank_prior's label_mode='selection_correct', the new "
        "default). `pre_abstention_selection_accuracy` (from a forced-always-cite rerun, "
        "tau=delta=-inf) is reported per condition as the selector's OWN ceiling, independent of "
        "the abstention rule."
    )

    for tag in named:
        cal = try_load(RUN_DIR / f"calibration_{tag}.json")
        if cal is None:
            continue
        grid_key = "grid" if "grid" in cal else "lambda_grid"
        grid_repr = [(g["lambda_prior"], round(g.get("balanced_accuracy", g.get("selection_accuracy", 0.0)), 4))
                     for g in cal.get(grid_key, [])]
        notes.append(f"{tag} calibration (dev n={cal['n_examples']}, k={cal['k']}, "
                     f"label_mode={cal.get('label_mode', 'selection_correct')}): "
                     f"lambda_prior={cal['lambda_prior']}, tau_m={cal['tau_m']:.4f}, "
                     f"delta_m={cal['delta_m']:.4f}, balanced_accuracy={cal['balanced_accuracy']:.4f}, "
                     f"confusion={cal['confusion']}, lambda_grid={grid_repr}.")

    notes.append(
        "Consistent finding across every condition that loads the round-1 consolidated LoRA "
        "(P32, S4t, P8t) vs its own frozen counterpart (P32f, S4t_f, P8t_f): the LoRA substantially "
        "HURTS the law_citation_retrieval-kind-specific citation_recall/gold_selected rate "
        "specifically (see law_citation_retrieval_kind_gold_selected per condition above), even "
        "though it improves or does not change gold_selected on the easier law_lookup/"
        "law_cite_to_title kinds and on overall selection accuracy. This mirrors the ALREADY-KNOWN "
        "rsi_run1e result that B'(frozen)=0.176 citation_recall beats B'(consolidated)=0.145 -- "
        "the round-1 LoRA was consolidated to help abstention/lookup/title behavior, at a real cost "
        "to hard-kind citation selection, and that cost transfers to every retrieval/prompt "
        "configuration tested here, not just the original k=4 plain-BM25 format it was trained under."
    )

    report = {
        "run": "pointwise_run1",
        "checkpoint": "m7/m7_retry_checkpoint.pt (370M, law-tuned)",
        "created": "2026-09-13",
        "n_heldout": 690,
        "conditions": conditions,
        "paired": paired_all,
        "notes": notes,
    }
    (RUN_DIR / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("wrote", RUN_DIR / "report.json")


if __name__ == "__main__":
    main()
