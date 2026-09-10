"""Post-night paired confirmation: the current incumbent adapter versus the unmodified baseline on a fresh rotation,
same sampling seed, per-item pairing. Writes two observe rows (stage confirm) and a `study_end` audit carrying the
paired statistics computed by the kernel's vendored functions. This is what the L4 domain gate cites."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import numpy as np
import yaml

from pravrudhi.application.spine import expected_hashes, resolve_model_snapshot, run_eval_job, score_job, write_job_inputs
from pravrudhi_kernel.ledger import LedgerWriter
from pravrudhi_kernel.ledger.verify import iter_events
from pravrudhi_kernel.metrics import answer_kind, draw_rotation, is_binary, record_exposure
from pravrudhi_kernel.sandbox import admit_observation, ensure_kernel_state
from pravrudhi_kernel.sandbox.observe import model_dir_hash
from pravrudhi_kernel.sandbox.runner import docker_available
from pravrudhi_kernel.sandbox.state import read_secret
from pravrudhi_kernel.stats import boot_ci_bca_g, boot_ci_bca_mean, hedges_g, permutation_p, wilson_ci


def current_incumbent(root: Path) -> dict[str, Any] | None:
    """The latest promoted candidate with its adapter path, from the ledger alone."""
    last = None
    for ev in iter_events(root / "research" / "ledger.jsonl"):
        if ev.kind == "promote" and ev.candidate_id:
            last = {
                "candidate_id": ev.candidate_id,
                "adapter": ev.payload.get("from_worktree"),
                "night": ev.night,
                "merge_commit": ev.payload.get("merge_commit"),
                "seq": ev.seq,
            }
    return last


def pass_interval(x: np.ndarray, *, binary: bool, seed: int) -> dict[str, Any]:
    """A 95% interval for one arm's pass rate, by the method its score scale admits.

    A Wilson interval is a confidence interval for a PROPORTION and needs a count of successes.
    `int(x.sum())` supplies one for any scale -- it type-checks, it stays inside `[0, n]`, and on the `set`
    kind's per-item Jaccard it silently truncates (66.7 successes becomes 66) and returns a number nobody can
    interpret. So Wilson is computed only where it means something, a bootstrap interval for the mean is
    reported in its place otherwise, and `method` names which one ran. ADR-0038.
    """
    n = int(x.size)
    if not n:
        return {"method": "wilson" if binary else "bca_mean", "ci95": None}
    if binary:
        return {"method": "wilson", "ci95": list(wilson_ci(int(x.sum()), n))}
    return {"method": "bca_mean", "ci95": list(boot_ci_bca_mean(x, n_boot=10_000, seed=seed))}


def paired_confirm(
    root: Path,
    *,
    night: int,
    candidate_id: str | None,
    seed: int,
    k: int,
    log: Any = print,
    night_config: Path | None = None,
) -> dict[str, Any]:
    cfg = yaml.safe_load((Path(night_config) if night_config else root / "research" / "prereg" / "lora_night.yaml").read_text())
    state = ensure_kernel_state(root, docker_available=docker_available())
    inc = current_incumbent(root)
    if candidate_id:
        adapter_dir = None
        for ev in iter_events(root / "research" / "ledger.jsonl"):
            if (
                ev.kind == "spend"
                and ev.candidate_id == candidate_id
                and ev.payload.get("phase") == "train"
                and ev.payload.get("steps")
            ):
                cand = Path(state.jobs_dir) / str(ev.payload["run_id"]) / "out" / "adapter"
                if (cand / "adapter_config.json").exists():
                    adapter_dir = cand
        inc = {"candidate_id": candidate_id, "adapter": str(adapter_dir) if adapter_dir else None, "night": night}
    if inc is None or not inc.get("adapter"):
        raise FileNotFoundError("no adapter to confirm for this candidate")
    adapter = Path(inc["adapter"])
    model_name = str(cfg["model"])
    if candidate_id:  # the candidate's own trainee, not whatever the config points at today
        for ev in iter_events(root / "research" / "ledger.jsonl"):
            if ev.kind == "propose" and ev.candidate_id == candidate_id and ev.bucket is not None:
                model_name = ev.bucket.target_model
    snap = resolve_model_snapshot(model_name)
    pool_dir = root / ".pravrudhi" / "kernel" / "pools" / str(cfg["bench"])
    w = LedgerWriter.open(root / "research" / "ledger.jsonl", "0.1.0")
    bucket = {"task_family": str(cfg["bench"]), "target_model": model_name, "corpus": f"{cfg['bench']}-train"}
    e = cfg["evaluation"]
    rot = draw_rotation(
        pool_dir, night, f"confirm-{inc['candidate_id']}-s{seed}", read_secret(state), k=k, exposure_cap=int(e["exposure_cap"])
    )
    record_exposure(pool_dir, rot)
    w.append(
        "audit",
        "kernel",
        {
            "kind": "study_start",
            "severity": "info",
            "study": "paired_confirm",
            "candidate": inc["candidate_id"],
            "rotation_id": rot.rotation_id,
            "k": k,
            "seed": seed,
        },
        epoch=0,
        night=night,
    )
    tpl = root / "harness" / "prompts" / "eval" / "gsm8k_v1.md"
    results: dict[str, dict[str, Any]] = {}
    for arm, ad in (("baseline", None), ("candidate", adapter)):
        jd = Path(state.jobs_dir) / f"confirm-n{night}-{arm}-{int(time.time())}"
        write_job_inputs(jd, pool_dir, rot, tpl)
        res, meta = run_eval_job(
            state,
            jd,
            snap,
            ad,
            seed=seed,
            temperature=float(e["temperature"]),
            max_new_tokens=int(e["max_new_tokens"]),
            batch_size=int(e["batch_size"]),
        )
        if res.exit_code != 0 or meta is None:
            raise RuntimeError(f"{arm} eval failed: {res.stderr_tail[-800:]}")
        scores, ref = score_job(jd, pool_dir, rot)
        exp = expected_hashes(jd, pool_dir, root / "harness", snap)
        if ad is not None:
            exp = exp.model_copy(update={"harness": model_dir_hash(ad)})
        results[arm] = {"scores": scores, "ref": ref, "res": res, "meta": meta, "exp": exp, "jd": jd}
        log(f"{arm}: pass_rate={sum(scores.values()) / len(scores):.4f} wall={res.wall_s:.0f}s")
    b, c = results["baseline"], results["candidate"]
    ids = sorted(b["scores"])
    xb = np.array([b["scores"][i] for i in ids], float)
    xc = np.array([c["scores"][i] for i in ids], float)
    d = xc - xb
    mean_b, mean_c = float(xb.mean()), float(xc.mean())
    # From the pool's own declared kind, not by inspecting the values: a fractional pool whose 100 items
    # happened to all score 0 or 1 would otherwise be labelled binary and get a Wilson interval that the next
    # rotation would invalidate.
    binary_scores = is_binary(answer_kind(pool_dir))
    ci_c = pass_interval(xc, binary=binary_scores, seed=seed)
    ci_b = pass_interval(xb, binary=binary_scores, seed=seed)
    admit_observation(
        w,
        expected=b["exp"],
        job_meta=b["meta"],
        per_item_scores=b["scores"],
        per_item_ref=str(b["ref"]),
        run_id=b["jd"].name,
        candidate_id="c-0000",
        surface="W3.adapter",
        bucket=bucket,
        epoch=0,
        night=night,
        cycle=None,
        seed=seed,
        rotation_id=rot.rotation_id,
        value_ref=None,
        cost_gpu_h=b["res"].wall_s / 3600,
        wall_s=b["res"].wall_s,
        peak_gib=b["res"].peak_gib_smi,
        isolation=state.isolation,
        stage="confirm",
        extra={"arm": "incumbent", "study": "paired_confirm", "paired_with": inc["candidate_id"]},
    )
    g_z = hedges_g(xc, xb)  # paired-design g on the two arms' per-item scores (n=100 each)
    stats = {
        "n_items": len(ids),
        "pass_baseline": mean_b,
        "pass_candidate": mean_c,
        "delta_mean": float(d.mean()),
        "delta_bca95": list(boot_ci_bca_mean(d, n_boot=10_000, seed=seed)),
        "g_av": g_z,
        "g_bca95": list(boot_ci_bca_g(xc, xb, n_boot=10_000, seed=seed)),
        "p_perm_paired": permutation_p(xc, xb, 50_000, seed, True),
        "wins": int((d > 0).sum()),
        "losses": int((d < 0).sum()),
        "ties": int((d == 0).sum()),
        # Which scale the per-item scores are on, always stated. Every field below depends on it, and a
        # reader cannot tell 0.667 from a truncated 0/1 count after the fact.
        "score_scale": "binary" if binary_scores else "fractional",
        # A Wilson interval is a confidence interval for a PROPORTION, so it needs a count of successes.
        # `int(xc.sum())` supplies one for any scale -- it type-checks, it stays inside [0, n], and on the
        # `set` kind's per-item Jaccard it silently truncates (66.7 successes becomes 66) and returns a number
        # nobody can interpret. So it is computed only where it means something, and a bootstrap interval for
        # the mean is reported in its place otherwise. ADR-0038.
        "pass_ci_candidate": ci_c,
        "pass_ci_baseline": ci_b,
        # Kept under their old names for readers that already parse them, and null on a scale where they do
        # not apply rather than quietly holding the interval of a truncated count.
        "wilson_candidate": ci_c["ci95"] if binary_scores else None,
        "wilson_baseline": ci_b["ci95"] if binary_scores else None,
        "mde_note": (
            "at n=100 paired binary items the minimum detectable paired effect at 80% power is about 0.08 in pass rate "
            "for typical discordance; effects below that are reported with their interval, not as detections"
            if binary_scores
            else "the paired MDE above was derived for binary items and does not transfer to a fractional "
            "score; no equivalent has been derived for this scale, so read delta_bca95 rather than a "
            "detection threshold"
        ),
    }
    admit_observation(
        w,
        expected=c["exp"],
        job_meta=c["meta"],
        per_item_scores=c["scores"],
        per_item_ref=str(c["ref"]),
        run_id=c["jd"].name,
        candidate_id=inc["candidate_id"],
        surface="W3.adapter",
        bucket=bucket,
        epoch=0,
        night=night,
        cycle=None,
        seed=seed,
        rotation_id=rot.rotation_id,
        value_ref=mean_b,
        cost_gpu_h=c["res"].wall_s / 3600,
        wall_s=c["res"].wall_s,
        peak_gib=c["res"].peak_gib_smi,
        isolation=state.isolation,
        stage="confirm",
        extra={
            "arm": "candidate",
            "study": "paired_confirm",
            "incumbent_run_id": b["jd"].name,
            "paired_stats": stats,
            "stats": {"boundary": "confirm", "note": "single fresh rotation; stated at screen tier"},
        },
    )
    w.append(
        "audit",
        "kernel",
        {
            "kind": "study_end",
            "severity": "info",
            "study": "paired_confirm",
            "candidate": inc["candidate_id"],
            "rotation_id": rot.rotation_id,
            "summary": stats,
        },
        epoch=0,
        night=night,
    )
    out = root / "research" / "prereg" / f"paired_confirm_night{night}.json"
    out.write_text(
        json.dumps(
            {"candidate": inc["candidate_id"], "rotation_id": rot.rotation_id, "seed": seed, **stats}, indent=2, sort_keys=True
        )
        + "\n"
    )
    log(json.dumps(stats))
    return stats
