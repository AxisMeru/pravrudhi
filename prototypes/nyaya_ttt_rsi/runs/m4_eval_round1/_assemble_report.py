"""One-off assembly (run dir, not part of the module contract): builds
report.json in the schema prototypes/nyaya_ttt_rsi/report.py expects, from
the raw artifacts _driver.py wrote for m4_eval_round1 (STEP 0: the 1.13B
Megatron checkpoint through the same scoring-mode + BM25-rank-prior harness
used for S4t/S4t_f in runs/pointwise_run1, plus per-template calibration)."""
import json
import sys
from pathlib import Path

sys.path.insert(0, "/lab")
from prototypes.nyaya_ttt_rsi import stats

RUN_DIR = Path("/lab/prototypes/nyaya_ttt_rsi/runs/m4_eval_round1")
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
        forced_per_kind = forced_report.get("per_kind_gold_selected_rate", {})
        for kind in ("law_citation_retrieval", "law_cite_to_title", "law_lookup"):
            if kind in forced_per_kind:
                m[f"pre_abstention_{kind}_kind"] = metric_from_counter(forced_per_kind[kind])
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
        "M4t": RUN_DIR / "M4t",
        "M4t_template": RUN_DIR / "M4t_template",
        "M8t": RUN_DIR / "M8t",
        "M8t_template": RUN_DIR / "M8t_template",
        "S4t_f": POINTWISE_DIR / "S4t_f",
        "P16f": POINTWISE_DIR / "P16f",
    }
    descriptions = {
        "M4t": "1.13B Megatron, k=4, PROMPT_CONFIG unchanged, tuned store, global selection-correct calib.",
        "M4t_template": "1.13B Megatron, k=4, PROMPT_CONFIG unchanged, tuned store, PER-TEMPLATE calib.",
        "M8t": "1.13B Megatron, k=8, body=200B/title=90B/budget=1400B, tuned store, global selection-correct calib.",
        "M8t_template": "1.13B Megatron, k=8, body=200B/title=90B/budget=1400B, tuned store, PER-TEMPLATE calib.",
        "S4t_f": "370M frozen, k=4 scoring mode + BM25-rank prior, TUNED retrieval, selection-correct calib. (pointwise_run1)",
        "P16f": "370M frozen, pointwise k=16, plain BM25, selection-correct calib. (pointwise_run1)",
    }
    forced_dirs = {"M4t": RUN_DIR / "M4t_forced_cite", "M8t": RUN_DIR / "M8t_forced_cite"}

    for name, path in named.items():
        report = try_load(path / "report.json")
        if report is None:
            notes.append(f"{name}: report.json missing at {path} -- run did not complete, omitted.")
            continue
        forced_report = try_load(forced_dirs[name] / "report.json") if name in forced_dirs else None
        conditions[name] = {"name": descriptions[name], "metrics": condition_metrics(report, forced_report),
                             "timing": timing(report)}

    lambda0 = {}
    for tag in ("M4t", "M8t"):
        r0 = try_load(RUN_DIR / f"{tag}_lambda0" / "report.json")
        if r0:
            lambda0[tag] = {
                "recall": r0["score"]["overall"]["citation_recall"],
                "gold_selected": r0["gold_selected_overall"],
            }
    if lambda0:
        notes.append(f"lambda=0 (no BM25-rank prior) comparison: {json.dumps(lambda0)}.")

    pair_specs = [
        ("M4t", "S4t_f", "S4t_f"), ("M4t", "P16f", "P16f"),
        ("M8t", "S4t_f", "S4t_f"), ("M8t", "P16f", "P16f"), ("M8t", "M4t", "M4t"),
        ("M4t_template", "S4t_f", "S4t_f"), ("M4t_template", "M4t", "M4t"),
        ("M8t_template", "S4t_f", "S4t_f"), ("M8t_template", "M8t", "M8t"),
    ]
    for a, b_tag, b_label in pair_specs:
        pfile = RUN_DIR / f"paired_{a}_vs_{b_tag}.json"
        paired = try_load(pfile)
        if paired is None:
            notes.append(f"paired comparison {a} vs {b_label}: {pfile.name} missing.")
            continue
        paired_all += paired_rows(paired, a, b_label)
        paired_all += paired_rows(paired, a, b_label, metric_key="gold_citation_present",
                                  metric_name="gold-citation present (690, lenient substring match)")

    for tag in ("M4t", "M8t"):
        cal = try_load(RUN_DIR / f"calibration_{tag}.json")
        if cal is None:
            continue
        notes.append(f"{tag} GLOBAL calibration (dev n={cal['n_examples']}, k={cal['k']}): "
                     f"lambda_prior={cal['lambda_prior']}, tau_m={cal['tau_m']:.4f}, delta_m={cal['delta_m']:.4f}, "
                     f"balanced_accuracy={cal['balanced_accuracy']:.4f}, confusion={cal['confusion']}, "
                     f"abstain_on_synthetic_miss={cal['abstain_on_synthetic_miss']}.")
        cal_t = try_load(RUN_DIR / f"calibration_{tag}_per_template.json")
        if cal_t is None:
            continue
        for tmpl, c in cal_t["per_template"].items():
            notes.append(f"{tag} PER-TEMPLATE calibration [{tmpl}] (n={c['n_examples']}): "
                         f"lambda_prior={c['lambda_prior']}, tau_m={c['tau_m']:.4f}, delta_m={c['delta_m']:.4f}, "
                         f"balanced_accuracy={c['balanced_accuracy']:.4f}, confusion={c['confusion']}, "
                         f"abstain_on_synthetic_miss={c['abstain_on_synthetic_miss']}.")

    recall_note = try_load(POINTWISE_DIR / "recall.json")
    if recall_note:
        notes.append(f"Retrieval recall@k reused from pointwise_run1 (same tuned store): "
                     f"plain {recall_note['plain']}; tuned {recall_note['tuned']}.")

    report = {
        "run": "m4_eval_round1",
        "checkpoint": "g0_sft_round1/final.pt (1.13B, Megatron-Core, SFT round 1)",
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
