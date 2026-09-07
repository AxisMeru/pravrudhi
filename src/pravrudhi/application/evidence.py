"""Render evidence documents from the ledger alone (make reproduce).

Deterministic: same ledger, same bytes.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from pravrudhi.application import objectives
from pravrudhi_kernel.ledger.replay import withdrawn_observations
from pravrudhi_kernel.ledger.verify import iter_events
from pravrudhi_kernel.schema import LedgerEvent
from pravrudhi_kernel.stats import wilson_ci


def render_objectives(root: Path) -> str:
    """The formal record said what was measured without saying what the user was trying to achieve.

    Missing comparisons could read as no effect if absent measurements were collapsed to zero. The objective's
    progress is the sole source of numeric evidence here, so this account cannot invent a different comparison.
    """
    ledger = Path(root) / "research" / "ledger.jsonl"
    declared = objectives.load_all(root)
    lines: list[str] = [
        "# Objectives",
        "",
        "Intent is quoted verbatim from the workspace objectives. Benchmark evidence is rendered through objective "
        "progress from the ledger's `audit{kind: external_eval}` rows alone (tier: external).",
        "",
    ]
    if not declared:
        lines += ["No objectives are declared in this workspace.", ""]
    for obj in declared:
        lines += [f"## {obj.id}", "", obj.intent, "", f"Track: `{obj.track}`", ""]
        if not ledger.exists():
            lines += [f"- {b.metric}: unmeasured — this workspace has no ledger yet." for b in obj.benchmarks]
            lines.append("")
            continue
        rows = objectives.progress(obj, ledger)
        measured: list[str] = []
        for row in rows:
            if row.state == "unmeasured":
                lines.append(f"- {row.benchmark}: unmeasured — {row.reason}.")
            elif row.state == "baseline_only":
                assert row.baseline is not None
                lines.append(f"- {row.benchmark}: baseline {row.baseline.value:.4f}; {row.reason}.")
            else:
                assert row.baseline is not None and row.latest is not None
                assert row.delta is not None and row.delta_lo is not None and row.delta_hi is not None
                measured.append(
                    f"| {row.benchmark} | {row.baseline.value:.4f} | {row.latest.value:.4f} | "
                    f"{row.delta:+.4f} | [{row.delta_lo:+.4f}, {row.delta_hi:+.4f}] |"
                )
        if measured:
            lines += [
                "",
                "| benchmark | baseline | current | difference | interval |",
                "|---|---|---|---|---|",
                *measured,
            ]
        lines.append("")
    lines += [
        "## Tensions",
        "",
        "External results are scored outside the kernel. A difference whose interval spans zero is not evidence "
        "of improvement. Objectives sharing a track share its evidence; the track does not identify which intent "
        "produced a result.",
        "",
    ]
    return "\n".join(lines)


def track_events(ledger: Path, track: str = "lora") -> Iterator[LedgerEvent]:
    """Ledger events of one track. A harness night block runs from a night_start with track=harness to its night_end;
    rows on the H3.prompt surface and audit rows tagged track H/harness belong to the harness track, rows on
    W3.adapter to the LoRA track, and untagged rows to the block they sit in (LoRA outside any harness block)."""
    in_harness = False
    withdrawn = withdrawn_observations(ledger)
    # candidates proposed on the harness surface belong to the harness track whatever surface a later row carries
    harness_cids = {ev.candidate_id for ev in iter_events(ledger) if ev.kind == "propose" and ev.surface == "H3.prompt"}
    for ev in iter_events(ledger):
        p = ev.payload
        if ev.kind == "observe" and ev.seq in withdrawn:
            continue  # ADR-0015: withdrawn by a sublate row; the row stays in the chain, not in the evidence
        if ev.kind == "audit" and p.get("kind") in ("night_start", "study_start"):
            in_harness = p.get("track") == "harness"  # a new block closes an unterminated one (ADR-0017)
        tagged_h = ev.surface == "H3.prompt" or p.get("track") in ("H", "harness") or ev.candidate_id in harness_cids
        if ev.surface == "W3.adapter" and not tagged_h:
            row_track = "lora"
        elif tagged_h or in_harness:
            row_track = "harness"
        else:
            row_track = "lora"
        if in_harness and ev.kind == "audit" and p.get("kind") == "night_end":
            in_harness = False
        if row_track == track:
            yield ev


def lora_events(ledger: Path) -> Iterator[LedgerEvent]:
    return track_events(ledger, "lora")


def _variance_for(prereg: Path, model: str | None, bench: str | None) -> Path | None:
    """The variance file measured for this model and pool (current or archived), else None."""
    for cand in sorted(prereg.glob("variance*.json")):
        try:
            v = json.loads(cand.read_text())
        except (OSError, ValueError):
            continue
        if v.get("model") == model and v.get("bench") == bench:
            return cand
    return None


def render_noise_floor(ledger: Path, variance: Path | None, study: int = 0) -> str:
    """Render the `study`-th noise-floor study (0 = the L3 study) from its own rows only."""
    rows = []
    design = None
    model = bench = None
    idx = -1
    for ev in iter_events(ledger):
        p = ev.payload
        if ev.kind == "audit" and p.get("kind") == "study_start" and p.get("study") == "noise_floor":
            idx += 1
            if idx == study:
                design = p.get("design")
            continue
        if idx != study:
            continue
        if ev.kind == "observe" and p.get("study") == "noise_floor":
            o = p["observed"]
            if ev.bucket is not None:
                model, bench = ev.bucket.target_model, ev.bucket.task_family
            rows.append(
                (
                    ev.seq,
                    p.get("rotation_id"),
                    p.get("seed_index"),
                    o["value"],
                    o["n_items"],
                    p["hashes"]["model"][:12],
                    p.get("isolation"),
                    p.get("job", {}).get("tok_s"),
                )
            )
    title = "L3 noise floor" if study == 0 else f"Noise floor study {study}"
    lines = [
        f"# {title} — rendered from research/ledger.jsonl",
        "",
        f"**Label: model-measured, screen tier, single model (unmodified {model or 'Qwen/Qwen3-4B'}), A/A design, "
        "isolation container.**",
        "",
    ]
    if design:
        lines += ["Design: " + ", ".join(f"{k}={v}" for k, v in sorted(design.items())), ""]
    lines += [
        "| seq | rotation | seed | pass_rate | n | model hash | isolation | tok/s |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for seq, rot, seed, v, n, mh, iso, tps in rows:
        lines.append(
            f"| {seq} | {rot} | {seed} | {v:.4f} | {n} | {mh} | {iso} | {tps:.1f} |"
            if tps
            else f"| {seq} | {rot} | {seed} | {v:.4f} | {n} | {mh} | {iso} | |"
        )
    if rows:
        n_tot = sum(r[4] for r in rows)
        k = round(sum(r[3] * r[4] for r in rows))
        lo, hi = wilson_ci(int(k), n_tot)
        lines += [
            "",
            f"Runs: {len(rows)}; items scored: {n_tot}; pooled pass rate {k / n_tot:.4f}, Wilson 95% [{lo:.4f}, {hi:.4f}].",
        ]
    # The variance line must describe *this* study. Matching on the model alone was not enough: after a later study
    # re-pointed variance.json at a different pool, a document for an earlier pool silently printed the new pool's
    # sigma, and stopped reproducing. A file is quoted only when both its model and its bench match this study, and
    # when none does the line is omitted rather than approximated.
    if variance is not None and model:
        variance = _variance_for(variance.parent, model, bench)
    if variance and variance.exists():
        v = json.loads(variance.read_text())
        if not model or (v.get("model") == model and v.get("bench") == bench):
            lines += [
                "",
                f"variance.json: sigma_seed={v['sigma_seed']:.4f} sigma_rot={v['sigma_rot']:.4f} "
                f"sigma_total={v['sigma_total']:.4f} "
                f"theta_surprise(|z| p99)={v['theta_surprise_abs_z_p99']}",
            ]
    return "\n".join(lines) + "\n"


def render_first_night(ledger: Path, night: int, track: str = "lora") -> str:
    """Per-candidate account of one night of one track, from the ledger alone."""
    from collections import Counter

    cands: dict[str, dict[str, Any]] = {}
    audits: list[dict[str, Any]] = []
    spent = 0.0
    withdrawn = withdrawn_observations(ledger)
    for ev in track_events(ledger, track):
        if ev.night != night:
            continue
        p, cid = ev.payload, ev.candidate_id
        if ev.kind == "propose" and cid and cid != "c-0000":
            cands[cid] = {
                "strategy": p.get("strategy"),
                "family": p.get("edit_family"),
                "selected": False,
                "deltas": [],
                "boundary": None,
                "canary": None,
                "outcome": "proposed",
                "hetvabhasa": None,
                "brier": None,
            }
        elif ev.kind == "select" and cid in cands:
            cands[cid]["selected"] = True
        elif ev.kind == "spend":
            spent += float(p.get("gpu_h") or 0.0)
        elif ev.kind == "observe" and cid in cands and p.get("arm") == "candidate":
            cands[cid]["deltas"].append(float(p["observed"]["delta_in"]))
            cands[cid]["boundary"] = (p.get("stats") or {}).get("boundary")
            cands[cid]["brier"] = p.get("brier")
            cands[cid]["outcome"] = "observed"
        elif ev.kind == "prune" and cid in cands:
            cands[cid]["outcome"] = "pruned"
            cands[cid]["hetvabhasa"] = p.get("hetvabhasa")
        elif ev.kind == "promote" and cid in cands:
            cands[cid]["outcome"] = "promotion_withdrawn" if ev.seq in withdrawn else "promoted"
        elif ev.kind == "skip" and cid in cands:
            cands[cid]["outcome"] = f"skipped:{p.get('reason')}"
        elif ev.kind == "audit":
            k = p.get("kind")
            if k == "canary" and cid in cands:
                cands[cid]["canary"] = "pass" if p.get("severity") == "info" else "fail"
            if k in (
                "strategy_switch_rate",
                "rethink_checkpoint",
                "rethink_declined",
                "night_end",
                "samples_verified",
                "job_failed",
                "decorative_controller",
            ):
                audits.append(
                    {
                        "kind": k,
                        **{
                            kk: vv
                            for kk, vv in p.items()
                            if kk
                            in (
                                "switches",
                                "n",
                                "wilson",
                                "spent_gpu_h",
                                "outcomes",
                                "n_kept",
                                "kept_rate",
                                "consecutive",
                                "reason",
                                "run_id",
                            )
                        },
                    }
                )
    title = f"L4 first night (night {night})" if track == "lora" else f"Harness track night {night}"
    label = (
        "**Label: model-measured, screen tier, one loop seed, paired on the same rotation and sampling seed; "
        "isolation container.**"
        if track == "lora"
        else "**Label: harness-measured (fixed model, mutable scaffold), screen tier, paired on the same MBPP+ rotation "
        "and sampling seed; hidden tests executed in the sandbox; isolation container.**"
    )
    lines = [
        f"# {title} — rendered from research/ledger.jsonl",
        "",
        label,
        "",
        "| candidate | strategy | family | selected | paired deltas | boundary | canary | outcome | hetvābhāsa | Brier |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for cid, c in cands.items():
        d = ", ".join(f"{x:+.3f}" for x in c["deltas"])
        b = "" if c["brier"] is None else f"{c['brier']:.3f}"
        lines.append(
            f"| {cid} | {c['strategy']} | {c['family']} | {'yes' if c['selected'] else ''} | {d} | {c['boundary'] or ''} | "
            f"{c['canary'] or ''} | {c['outcome']} | {c['hetvabhasa'] or ''} | {b} |"
        )
    outcomes = Counter(str(c["outcome"]) for c in cands.values())
    lines += [
        "",
        f"Candidates proposed: {len(cands)}; selected: {sum(1 for c in cands.values() if c['selected'])}; outcomes: "
        + ", ".join(f"{k}={v}" for k, v in sorted(outcomes.items()))
        + f"; GPU-hours charged (spend rows): {spent:.2f}.",
        "",
        "Audits:",
        "",
    ]
    for a in audits:
        lines.append("- " + ", ".join(f"{k}={v}" for k, v in a.items()))
    return "\n".join(lines) + "\n"


def render_nights_summary(ledger: Path, nights: tuple[int, ...]) -> str:
    """The numbers a gate cites for a set of nights, as JSON keyed so each can be cited `L4_summary.json:<key>`.

    Every value is computed from ledger rows of the given nights; nothing is hand-entered."""
    import math
    from collections import Counter

    rows = [ev for ev in lora_events(ledger) if ev.night in nights]
    prop = [r for r in rows if r.kind == "propose" and r.candidate_id != "c-0000"]
    obs_c = [r for r in rows if r.kind == "observe" and r.payload.get("arm") == "candidate"]
    obs_i = [r for r in rows if r.kind == "observe" and r.payload.get("arm") == "incumbent"]
    xs: dict[str, list[float]] = {}
    for r in obs_c:
        xs.setdefault(str(r.candidate_id), []).append(float(r.payload["observed"]["delta_in"]))
    prunes = [r for r in rows if r.kind == "prune"]
    pruned_ids = {r.candidate_id for r in prunes}
    all_d = [x for v in xs.values() for x in v]
    mean = sum(all_d) / len(all_d) if all_d else 0.0
    sd = math.sqrt(sum((x - mean) ** 2 for x in all_d) / (len(all_d) - 1)) if len(all_d) > 1 else 0.0
    briers = [float(r.payload["brier"]) for r in obs_c if r.payload.get("brier") is not None]
    gammas = [r.payload["gamma"] for r in rows if r.kind == "select"]
    sw = [r.payload for r in rows if r.kind == "audit" and r.payload.get("kind") == "strategy_switch_rate"]
    dec = [r.payload["decorative"] for r in rows if r.kind == "select"]
    out = {
        "nights": list(nights),
        "ledger_seq_range": [rows[0].seq, rows[-1].seq] if rows else None,
        "proposed": len(prop),
        "proposed_by_strategy": dict(Counter(str(r.payload.get("strategy")) for r in prop)),
        "candidates_observed": len(xs),
        "candidate_observe_rows": len(obs_c),
        "incumbent_observe_rows": len(obs_i),
        "observe_rows_all_kernel_pratyaksha_container": all(
            r.actor == "kernel" and r.provenance == "pratyaksha" and r.payload.get("isolation") == "container"
            for r in obs_c + obs_i
        ),
        "paired_delta_n": len(all_d),
        "paired_delta_mean": round(mean, 4),
        "paired_delta_sd": round(sd, 4),
        "paired_delta_min": min(all_d) if all_d else None,
        "paired_delta_max": max(all_d) if all_d else None,
        "pruned": len(prunes),
        "pruned_by_hetvabhasa": dict(Counter(str(r.payload.get("hetvabhasa")) for r in prunes)),
        "confirmed": sum(
            1
            for r in obs_c
            if (r.payload.get("stats") or {}).get("boundary") == "confirm" and r.payload.get("study") != "paired_confirm"
        ),
        "paired_confirm_studies": sum(1 for r in obs_c if r.payload.get("study") == "paired_confirm"),
        "promoted": sum(1 for r in rows if r.kind == "promote"),
        "continuing_at_close": {
            c: {"deltas": v, "mean": round(sum(v) / len(v), 4), "n": len(v)} for c, v in xs.items() if c not in pruned_ids
        },
        "job_failed": sum(1 for r in rows if r.kind == "audit" and r.payload.get("kind") == "job_failed"),
        "pool_exhausted": sum(1 for r in rows if r.kind == "audit" and r.payload.get("kind") == "pool_exhausted"),
        "gpu_h_spend_rows": round(sum(float(r.payload.get("gpu_h") or 0.0) for r in rows if r.kind == "spend"), 3),
        "brier_n": len(briers),
        "brier_mean": round(sum(briers) / len(briers), 3) if briers else None,
        "gamma_first": gammas[0] if gammas else None,
        "gamma_last": gammas[-1] if gammas else None,
        "decorative_cv_G_min": min(float(d["cv_G"]) for d in dec) if dec else None,
        "decorative_cv_G_max": max(float(d["cv_G"]) for d in dec) if dec else None,
        "decorative_mi_bits_max": max(float(d["mi_bits"]) for d in dec) if dec else None,
        "strategy_switch_rate_last": sw[-1] if sw else None,
        "rethink_checkpoints": sum(1 for r in rows if r.kind == "audit" and r.payload.get("kind") == "rethink_checkpoint"),
        "night_start_prereg_sha256": [
            r.payload.get("prereg_sha256") for r in rows if r.kind == "audit" and r.payload.get("kind") == "night_start"
        ][-1:],
    }
    return json.dumps(out, indent=2, sort_keys=True) + "\n"


def render_h1(ledger: Path, nights: tuple[int, ...], track: str = "lora") -> str:
    """Compare selection arms on the nights given: the H1 screen.

    CHARTER §2 H1 asks whether the EFE controller reaches Δ* with lower regret per GPU-hour than a greedy ratchet
    and a lineage Thompson sampler at matched experiment count, with Δ* defined as the 60th percentile of the greedy
    arm's gain distribution. This renders that comparison from the ledger and nothing else.

    A comparison in which any arm received no paired evaluation is reported as VOID rather than as a result. That is
    not a formality: the first attempt at this experiment gave one arm a full night and the others none because the
    sealed pool ran out, and an unguarded renderer would have shown the surviving arm winning.
    """
    arms: dict[str, dict[str, Any]] = {}
    night_arm: dict[int, str] = {}
    for ev in track_events(ledger, track):
        if ev.night not in nights:
            continue
        p = ev.payload
        if ev.kind == "audit" and p.get("kind") == "night_start" and p.get("selection_policy"):
            night_arm[ev.night] = str(p["selection_policy"])
        arm = night_arm.get(ev.night)
        if arm is None:
            continue
        a = arms.setdefault(
            arm, {"nights": set(), "gpu_h": 0.0, "deltas": [], "promoted": 0, "pruned": 0, "selected": 0, "proposed": 0}
        )
        a["nights"].add(ev.night)
        if ev.kind == "propose":
            a["proposed"] += 1
        elif ev.kind == "select":
            a["selected"] += 1
        elif ev.kind == "spend":
            a["gpu_h"] += float(p.get("gpu_h") or 0.0)
        elif ev.kind == "observe" and p.get("arm") == "candidate":
            a["deltas"].append(float(p["observed"]["delta_in"]))
        elif ev.kind == "promote":
            a["promoted"] += 1
        elif ev.kind == "prune":
            a["pruned"] += 1

    lines = [
        f"# H1 screen: selection arms on {track} nights {', '.join(str(n) for n in nights)}",
        "",
        "Rendered from the ledger alone. Arms differ in selection only: pairing, the sequential boundary, the "
        "canaries and every row shape are identical across arms.",
        "",
    ]
    starved = sorted(a for a, v in arms.items() if not v["deltas"])
    missing = [a for a in ("efe", "greedy") if a not in arms]
    if starved or missing:
        lines += [
            "**VOID — this is not a comparison.**",
            "",
            f"Arms with no paired evaluation: {', '.join(starved) or 'none'}. "
            f"Arms absent from these nights: {', '.join(missing) or 'none'}.",
            "",
            "An arm that never ran cannot lose. Re-run with a pool that can carry every arm before reading anything "
            "into the table below.",
            "",
        ]
    lines += [
        "| arm | nights | proposed | selected | paired evals | mean Δ | best Δ | promoted | pruned | GPU-h | Δ per GPU-h |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for arm in sorted(arms):
        v = arms[arm]
        d = v["deltas"]
        mean = sum(d) / len(d) if d else 0.0
        best = max(d) if d else 0.0
        per_h = (best / v["gpu_h"]) if v["gpu_h"] > 0 else 0.0
        lines.append(
            f"| {arm} | {','.join(str(n) for n in sorted(v['nights']))} | {v['proposed']} | {v['selected']} | "
            f"{len(d)} | {mean:+.4f} | {best:+.4f} | {v['promoted']} | {v['pruned']} | {v['gpu_h']:.3f} | {per_h:+.4f} |"
        )
    greedy = arms.get("greedy", {}).get("deltas") or []
    if greedy:
        ordered = sorted(greedy)
        idx = min(len(ordered) - 1, int(0.6 * (len(ordered) - 1)))
        delta_star = ordered[idx]
        lines += [
            "",
            f"Δ\\* (60th percentile of the greedy arm's gain distribution, CHARTER §2 H1, n={len(ordered)}): "
            f"{delta_star:+.4f}.",
        ]
        if delta_star <= 0:
            # The charter defines the target from the greedy arm's gains, which assumes that distribution has
            # positive mass. Against an incumbent most candidates cannot beat, it does not, and a non-positive
            # target would be met by doing nothing at all. Saying so beats printing a number that reads as a goal.
            lines += [
                "",
                "**Δ\\* is not usable on these nights.** It is defined from the greedy arm's gain distribution, and "
                "that distribution is centred below zero here because most candidates lose to the incumbent. A "
                "non-positive target is reached by proposing nothing, so regret-to-Δ\\* cannot be computed and no "
                "claim about reaching it is made. Δ\\* is meaningful while the incumbent is still weak; once it is "
                "strong the comparison needs a target defined some other way, which is an open pre-registration "
                "question rather than something to settle in a renderer.",
            ]
    else:
        lines += ["", "Δ\\* is not computable: the greedy arm has no gain distribution on these nights."]
    lines += [
        "",
        "## Tensions",
        "",
        "Best Δ per GPU-hour is a screen-tier proxy, not the charter's regret-per-GPU-hour to Δ\\*, which needs the "
        "arms run to a common target. Nights differ in their candidate sets because the proposer is re-run per night, "
        "so this is a randomised comparison across nights rather than a paired one.",
        "",
    ]
    return "\n".join(lines)


def render_search(ledger: Path) -> str:
    """The shape of the search and how often selection actually had a choice, from the ledger alone."""
    from pravrudhi.application.archive import ancestry_report, parent_map, selection_pressure

    parents = parent_map(ledger)
    report = ancestry_report(parents)
    pressure = selection_pressure(ledger)
    binding = [p for p in pressure if p.binding]
    recent = [p for p in pressure if p.night >= 7]
    recent_binding = [p for p in recent if p.binding]

    lines = [
        "# The shape of the search: branching, and whether selection had a choice",
        "",
        "Rendered from the ledger alone. Two measurements, both structural rather than statistical: how the",
        "candidate graph branches, and how often the budget actually forced the controller to leave something out.",
        "",
        "## Ancestry",
        "",
        "| quantity | value |",
        "|---|---|",
        f"| candidates proposed | {report.nodes} |",
        f"| roots | {report.roots} |",
        f"| distinct parents in the whole history | {report.distinct_parents} |",
        f"| deepest lineage | {report.max_depth} generation(s) |",
    ]
    if report.widest:
        cid, n = report.widest
        lines.append(f"| widest parent | `{cid}`, {n} children |")
    lines += [
        "",
        "Every candidate is proposed with its parent recorded, and until now nothing read the field back. The",
        "graph it describes is a star: the whole recorded history descends from a very small number of ancestors,",
        "because a candidate's parent is always whichever incumbent was standing when it was built.",
        "",
        "## Selection pressure",
        "",
        "A selection rule earns its keep only when the live pool exceeds what the budget can run. The live pool is",
        "every candidate proposed so far that has not been pruned or promoted, restricted to the evaluation benches",
        "the night worked on; a candidate measured on another pool is not an alternative to one measured on this",
        "pool, and counting it would inflate the apparent choice.",
        "",
        "| night | live pool | ran | declined |",
        "|---|---|---|---|",
    ]
    lines += [f"| {p.night} | {p.live} | {p.selected} | {p.declined} |" for p in pressure]
    lines += [
        "",
        f"The budget forced a choice on {len(binding)} of {len(pressure)} nights, declining "
        f"{sum(p.declined for p in pressure)} candidate-nights in total.",
        "",
    ]
    if recent:
        lines += [
            f"From night 7 onward that falls to {len(recent_binding)} of {len(recent)} nights, declining "
            f"{sum(p.declined for p in recent)}. On those nights the loop proposed about as many candidates as it",
            "could afford to run, so the pool equalled the budget and the controller ranked a set it was going to",
            "run in full regardless.",
            "",
        ]
    lines += [
        "## Tensions",
        "",
        "The live pool is reconstructed here from propose, prune and promote rows rather than read from the",
        "controller, so it is this renderer's reading of what was available rather than the pool the controller",
        "itself held. A candidate pruned during a night is counted as live for that night, because pruning follows",
        "evaluation and the candidate was available when the night chose.",
        "",
        "Selection pressure says nothing about whether the ranking was good. It says only how often ranking",
        "mattered. A night whose budget covered its whole pool cannot distinguish any two selection rules, and most",
        "recent nights are of that kind, which bounds what any retrospective comparison of arms can show.",
    ]
    return "\n".join(lines) + "\n"
