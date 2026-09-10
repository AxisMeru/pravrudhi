"""`pravrudhi study noise-floor`: the first real evidence. R rotations × S seeds of the unmodified trainee,
each a kernel-launched job, kernel-scored, admitted as a real observe row; then σ estimates →
variance.json."""

from __future__ import annotations

import json
import math
import time
from pathlib import Path
from typing import Any

from pravrudhi.application.spine import (
    expected_hashes,
    resolve_model_snapshot,
    run_eval_job,
    score_job,
    write_job_inputs,
)
from pravrudhi_kernel.ledger import LedgerWriter
from pravrudhi_kernel.metrics import draw_rotation, record_exposure
from pravrudhi_kernel.metrics.pool import load_manifest
from pravrudhi_kernel.sandbox import admit_observation, ensure_kernel_state
from pravrudhi_kernel.sandbox.runner import docker_available
from pravrudhi_kernel.sandbox.state import read_secret
from pravrudhi_kernel.stats import wilson_ci

BASELINE_ID = "c-0000"


def _sd(xs: list[float]) -> float:
    n = len(xs)
    if n < 2:
        return 0.0
    m = sum(xs) / n
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1))


def noise_floor(
    root: Path,
    *,
    model: str,
    pool_dir: Path,
    template: Path,
    rotations: int,
    seeds: int,
    k: int,
    exposure_cap: int,
    temperature: float,
    max_new_tokens: int,
    batch_size: int,
    night: int,
    gpu_cost_per_hour: float = 1.0,
    out: Path | None = None,
    log: Any = print,
) -> dict[str, Any]:
    state = ensure_kernel_state(root, docker_available=docker_available())
    if state.isolation != "container":
        raise RuntimeError("noise-floor study requires container isolation (docker)")
    secret = read_secret(state)
    snap = resolve_model_snapshot(model)
    manifest = load_manifest(pool_dir)
    ledger = root / "research" / "ledger.jsonl"
    w = LedgerWriter.open(ledger, "0.1.0")
    bucket = {"task_family": manifest["bench"], "target_model": model, "corpus": f"{manifest['bench']}-train"}
    harness_dir = root / "harness"
    if BASELINE_ID not in {json.loads(line).get("candidate_id") for line in ledger.read_text().splitlines()}:
        w.append(
            "propose",
            "kernel",
            {
                "op": "baseline",
                "edit_family": "baseline",
                "strategy": "none",
                "note": "unmodified trainee; the incumbent every candidate is paired against",
            },
            epoch=0,
            night=night,
            cycle=0,
            candidate_id=BASELINE_ID,
            surface="W3.adapter",
            bucket=bucket,
            provenance="agama",
        )
    w.append(
        "audit",
        "kernel",
        {
            "kind": "study_start",
            "severity": "info",
            "study": "noise_floor",
            "design": {
                "rotations": rotations,
                "seeds": seeds,
                "k": k,
                "exposure_cap": exposure_cap,
                "temperature": temperature,
                "max_new_tokens": max_new_tokens,
                "template": template.name,
            },
        },
        epoch=0,
        night=night,
    )
    values: dict[str, list[float]] = {}
    value_ref: float | None = None
    rows: list[dict[str, Any]] = []
    cycle = 0
    for r in range(rotations):
        rot = draw_rotation(pool_dir, night, f"{BASELINE_ID}-r{r}", secret, k=k, exposure_cap=exposure_cap)
        record_exposure(pool_dir, rot)
        for s in range(seeds):
            cycle += 1
            job_dir = Path(state.jobs_dir) / f"nf-n{night}-r{r}-s{s}-{int(time.time())}"
            write_job_inputs(job_dir, pool_dir, rot, template)
            res, meta = run_eval_job(
                state,
                job_dir,
                snap,
                None,
                seed=s,
                temperature=temperature,
                max_new_tokens=max_new_tokens,
                batch_size=batch_size,
            )
            if res.exit_code != 0 or meta is None:
                w.append(
                    "audit",
                    "kernel",
                    {
                        "kind": "job_failed",
                        "severity": "medium",
                        "run_id": job_dir.name,
                        "exit_code": res.exit_code,
                        "stderr_tail": res.stderr_tail[-800:],
                    },
                    epoch=0,
                    night=night,
                    cycle=cycle,
                    candidate_id=BASELINE_ID,
                    surface="W3.adapter",
                )
                log(f"rotation {r} seed {s}: job FAILED exit {res.exit_code}")
                continue
            scores, ref = score_job(job_dir, pool_dir, rot)
            exp = expected_hashes(job_dir, pool_dir, harness_dir, snap)
            cost = res.wall_s / 3600.0 * gpu_cost_per_hour
            spend, obs = admit_observation(
                w,
                expected=exp,
                job_meta=meta,
                per_item_scores=scores,
                per_item_ref=str(ref),
                run_id=job_dir.name,
                candidate_id=BASELINE_ID,
                surface="W3.adapter",
                bucket=bucket,
                epoch=0,
                night=night,
                cycle=cycle,
                seed=s,
                rotation_id=rot.rotation_id,
                value_ref=value_ref,
                cost_gpu_h=cost,
                wall_s=res.wall_s,
                peak_gib=res.peak_gib_smi,
                isolation=state.isolation,
                stage="screen",
                extra={"study": "noise_floor", "rotation_index": r},
            )
            v = obs.payload["observed"]["value"]
            if value_ref is None:
                value_ref = v
            values.setdefault(rot.rotation_id, []).append(v)
            rows.append(
                {
                    "seq": obs.seq,
                    "rotation": rot.rotation_id,
                    "seed": s,
                    "value": v,
                    "n": k,
                    "tok_s": meta.get("tok_s"),
                    "peak_gib": res.peak_gib_smi,
                    "wall_s": res.wall_s,
                }
            )
            log(f"rotation {r} ({rot.rotation_id}) seed {s}: pass_rate={v:.4f} n={k} seq={obs.seq} wall={res.wall_s:.0f}s")
    all_v = [v for vs in values.values() for v in vs]
    within = [_sd(vs) for vs in values.values() if len(vs) >= 2]
    rot_means = [sum(vs) / len(vs) for vs in values.values()]
    sigma_seed = math.sqrt(sum(x * x for x in within) / len(within)) if within else 0.0
    sigma_rot = _sd(rot_means)
    mean = sum(all_v) / len(all_v) if all_v else 0.0
    total_items = k * len(all_v)
    k_pass = round(mean * total_items)
    lo, hi = wilson_ci(int(k_pass), total_items) if total_items else (0.0, 0.0)
    z = [(v - mean) / sigma_seed for v in all_v] if sigma_seed > 0 else []
    theta = sorted(abs(x) for x in z)[int(0.99 * (len(z) - 1))] if z else None
    var = {
        "bench": manifest["bench"],
        "model": model,
        "study": "noise_floor",
        "night": night,
        "n_runs": len(all_v),
        "rotations": len(values),
        "seeds_per_rotation": seeds,
        "k_items": k,
        "mean_pass_rate": mean,
        "wilson_95": [lo, hi],
        "n_items_total": total_items,
        "sigma_seed": sigma_seed,
        "sigma_rot": sigma_rot,
        "sigma_total": _sd(all_v),
        "theta_surprise_abs_z_p99": theta,
        "ledger_seqs": [r["seq"] for r in rows],
        "runs": rows,
        "labels": "model-measured; screen tier; single model; unmodified trainee (A/A); isolation container",
    }
    # One file per track, not one file per machine: `night.py` reads `variance.json` as the model track's floor
    # and the harness track already keeps its own `variance_harness.json`. A study on a different objective's
    # pool writing to the shared name would re-point every later gsm8k night's boundary at a legal-domain
    # sigma, which is the failure ADR-0025 records in the archiving branch just below.
    dest = out if out is not None else root / "research" / "prereg" / "variance.json"
    # A study on a new pool or trainee must not erase the one it replaces. Every noise floor is the parameter that
    # a night's boundary was set from, so an overwritten file makes that night's evidence unreproducible: this was
    # found when an evidence document for an earlier pool silently began quoting a later pool's sigma (ADR-0025).
    if dest.exists():
        prev = json.loads(dest.read_text())
        if (prev.get("model"), prev.get("bench")) != (var["model"], var["bench"]):
            slug = f"{prev.get('bench', 'unknown')}_{str(prev.get('model', 'unknown')).split('/')[-1]}".lower()
            archive = dest.with_name(f"variance_archive_{slug}.json")
            if not archive.exists():
                archive.write_text(json.dumps(prev, indent=2, sort_keys=True) + "\n")
    dest.write_text(json.dumps(var, indent=2, sort_keys=True) + "\n")
    w.append(
        "audit",
        "kernel",
        {
            "kind": "study_end",
            "severity": "info",
            "study": "noise_floor",
            "summary": {
                kk: var[kk] for kk in ("n_runs", "mean_pass_rate", "wilson_95", "sigma_seed", "sigma_rot", "sigma_total")
            },
            "variance_file_sha256": __import__("hashlib").sha256(dest.read_bytes()).hexdigest(),
        },
        epoch=0,
        night=night,
    )
    return var
