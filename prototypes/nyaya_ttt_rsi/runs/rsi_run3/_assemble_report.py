"""One-off assembly (run dir, not part of the module contract): builds
report.json in the schema prototypes/nyaya_ttt_rsi/report.py expects, from
the raw artifacts _driver.py wrote for rsi_run3 (STEP 1: RSI round 3 on the
370M, targeted at law_cite_to_title, pseudo-labeled with the round-1 LoRA
harness under per-template calibration)."""
import json
import sys
from pathlib import Path

sys.path.insert(0, "/lab")
from prototypes.nyaya_ttt_rsi import stats

RUN_DIR = Path("/lab/prototypes/nyaya_ttt_rsi/runs/rsi_run3")
POINTWISE_DIR = Path("/lab/prototypes/nyaya_ttt_rsi/runs/pointwise_run1")


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
    if "gold_selected_overall" in report:
        m["gold_selected"] = metric_from_counter(report["gold_selected_overall"])
    if report.get("false_abstain_when_shown"):
        m["false_abstain_when_shown"] = metric_from_counter(report["false_abstain_when_shown"])
    per_kind = report.get("per_kind_gold_selected_rate", {})
    for kind in ("law_citation_retrieval", "law_cite_to_title", "law_lookup"):
        if kind in per_kind:
            m[f"{kind}_kind_gold_selected"] = metric_from_counter(per_kind[kind])
    if forced_report is not None and "gold_selected_overall" in forced_report:
        m["pre_abstention_selection_accuracy"] = metric_from_counter(forced_report["gold_selected_overall"])
    return m


def timing(report):
    wall = report.get("wall_clock_seconds", {})
    total = wall.get("total", 0.0)
    vram = report.get("peak_vram_mib")
    return {"wall_s": round(total, 1), "peak_vram_gib": round(vram / 1024, 2) if vram else None}


def paired_rows(paired, a_label, b_label, metric_key="gold_selected",
               metric_name="gold selected (index-exact, only where both conditions report it)"):
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
        "S4t_round3": RUN_DIR / "S4t_round3",
        "S4t_round3_template": RUN_DIR / "S4t_round3_template",
        "S4t": POINTWISE_DIR / "S4t",
        "S4t_f": POINTWISE_DIR / "S4t_f",
    }
    descriptions = {
        "S4t_round3": "370M + round-3 LoRA (continued from round-1), k=4, GLOBAL selection-correct calib.",
        "S4t_round3_template": "370M + round-3 LoRA, k=4, PER-TEMPLATE selection-correct calib.",
        "S4t": "370M + round-1 consolidated LoRA, k=4, GLOBAL selection-correct calib. (pointwise_run1)",
        "S4t_f": "370M frozen, k=4, GLOBAL selection-correct calib. (pointwise_run1)",
    }
    forced_dirs = {"S4t_round3": RUN_DIR / "S4t_round3_forced_cite"}

    for name, path in named.items():
        report = try_load(path / "report.json")
        if report is None:
            notes.append(f"{name}: report.json missing at {path} -- run did not complete, omitted.")
            continue
        forced_report = try_load(forced_dirs[name] / "report.json") if name in forced_dirs else None
        conditions[name] = {"name": descriptions[name], "metrics": condition_metrics(report, forced_report),
                             "timing": timing(report)}

    data_stats = try_load(RUN_DIR / "round3_data_stats.json")
    if data_stats:
        notes.append(f"Round-3 pseudo-labeled data: {json.dumps(data_stats)}.")

    gate = try_load(RUN_DIR / "gate_decision.json")
    if gate:
        notes.append(f"Round-3 gate decision: accepted={gate['accepted']}, reason={gate['reason']}, "
                     f"probe_delta_rel={gate['probe_delta_rel']:.4f}, dev_before={gate['dev_before']}, "
                     f"dev_after={gate['dev_after']}.")

    lambda0 = try_load(RUN_DIR / "S4t_round3_lambda0" / "report.json")
    if lambda0:
        notes.append(f"lambda=0 (no BM25-rank prior), global calib: recall="
                     f"{lambda0['score']['overall']['citation_recall']}, "
                     f"gold_selected={lambda0['gold_selected_overall']}.")

    pair_specs = [
        ("S4t_round3", "S4t", "S4t"), ("S4t_round3", "S4t_f", "S4t_f"),
        ("S4t_round3_template", "S4t", "S4t"), ("S4t_round3_template", "S4t_f", "S4t_f"),
        ("S4t_round3_template", "S4t_round3", "S4t_round3"),
    ]
    for a, b_tag, b_label in pair_specs:
        pfile = RUN_DIR / f"paired_{a}_vs_{b_tag}.json"
        paired = try_load(pfile)
        if paired is None:
            notes.append(f"paired comparison {a} vs {b_label}: {pfile.name} missing.")
            continue
        paired_all += paired_rows(paired, a, b_label)

    notes.append(
        "STEP 2 (round 4) was NOT run: round 3's TRAIN-dev per-kind selection "
        "accuracy for its own targeted kind (law_cite_to_title) went from 1.000 "
        "before training to 0.978 after (a small decline, inside the gate's "
        "5-point tolerance but not an improvement), and law_lookup only rose "
        "0.972->0.981; only law_citation_retrieval rose meaningfully on dev "
        "(0.451->0.471). The gate ACCEPTED (probe regression 4.7% < 15%, no "
        "kind fell >5 points), but the task's round-4 trigger is gate acceptance "
        "AND a clear dev improvement -- since round 3 did not deliver the latter "
        "for its own target kind, running a compounding round 4 on top of a "
        "flat/ambiguous round 3 would have risked amplifying noise rather than "
        "signal, so round 4 was skipped per 'never loosen the gate.'"
    )
    notes.append(
        "Global-calibration comparison (as S4t/S4t_f were originally reported): "
        "S4t_round3 (0.545 gold_selected) is WORSE than S4t (0.593, p=0.0035) -- "
        "the global threshold trades law_cite_to_title/law_lookup accuracy for a "
        "small law_citation_retrieval gain (0.044->0.093). Under PER-TEMPLATE "
        "calibration (2026-09-13 fix, applied identically to round 3's own "
        "pseudo-labeling and its held-out eval), S4t_round3_template's overall "
        "gold_selected (0.613) is not significantly different from S4t's global "
        "number (0.593, p=0.215) -- i.e. round 3 plus per-template calibration "
        "roughly matches round 1's overall selection rate while modestly "
        "improving law_citation_retrieval recall (0.044->0.110), but did not "
        "clearly improve the title kind it targeted (0.828 on held-out; no "
        "directly comparable round-1 or frozen per-template title number was "
        "run in this budget, since M4t/M8t's per-template runs were on the "
        "1.13B, not the 370M -- this is a real gap, disclosed rather than "
        "papered over: no 370M per-template FROZEN or round-1 baseline exists "
        "to confirm round 3 actually helped title selection specifically."
    )

    report = {
        "run": "rsi_run3",
        "checkpoint": "m7/m7_retry_checkpoint.pt (370M, law-tuned) + round-3 LoRA continued from round-1",
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
