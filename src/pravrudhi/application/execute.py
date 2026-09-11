"""kriyā and pratyabhijñā for one candidate: train in the sandbox, evaluate paired against the incumbent on one
rotation and seed, let the kernel score and admit both arms, apply the sequential boundary, canaries, promote or prune."""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

from pravrudhi.application.discordance import discordance_fields
from pravrudhi.application.spine import IMAGE, expected_hashes, run_eval_job, score_job, write_job_inputs
from pravrudhi.targets import LoraRecipe
from pravrudhi_kernel.ledger import LedgerWriter, replay
from pravrudhi_kernel.metrics import draw_rotation, record_exposure, scorer_for
from pravrudhi_kernel.sandbox import JobSpec, KernelState, admit_observation, run_job
from pravrudhi_kernel.sandbox.observe import KernelHashes, model_dir_hash
from pravrudhi_kernel.sandbox.state import read_secret
from pravrudhi_kernel.stats import Variance, sequential_boundary

Log = Callable[[str], None]


class _HashesWithParent:
    """Wrapper for KernelHashes that adds harness_parent to model_dump output. This allows paired
    observations to record the incumbent adapter's hash so replay's rebase detection can fire."""

    def __init__(self, hashes: KernelHashes, harness_parent: str | None = None) -> None:
        self._hashes = hashes
        self._harness_parent = harness_parent

    def model_dump(self) -> dict[str, Any]:
        """Return hashes dict with harness_parent added if present."""
        result = self._hashes.model_dump()
        if self._harness_parent is not None:
            result["harness_parent"] = self._harness_parent
        return result

    def __getattr__(self, name: str) -> Any:
        """Delegate attribute access to the wrapped hashes object."""
        return getattr(self._hashes, name)


class NightContext:
    def __init__(
        self,
        root: Path,
        state: KernelState,
        snapshot: Path,
        pool_dir: Path,
        cfg: dict[str, Any],
        variance: dict[str, Any],
        train_rows: list[dict[str, str]],
        night: int,
        log: Log,
    ) -> None:
        self.root, self.state, self.snapshot, self.pool_dir, self.cfg, self.night, self.log = (
            root,
            state,
            snapshot,
            pool_dir,
            cfg,
            night,
            log,
        )
        self.train_rows = train_rows
        self.sigma_seed = float(variance["sigma_seed"])
        b = cfg["boundary"]
        dm = max(2 * self.sigma_seed, float(b["delta_min_floor"]))
        self.variance = Variance(
            bench=str(variance["bench"]),
            sigma_seed=max(self.sigma_seed, 1e-4),
            tau=dm,
            delta_min=dm,
            alpha_eff=float(b["alpha_eff"]),
            alpha_fut=float(b["alpha_fut"]),
            k_max=int(b["k_max"]),
            sigma_mode="adaptive" if str(b["sigma_mode"]) == "adaptive" else "fixed",
            n0=int(b["n0"]),
            min_n_confirm=int(b.get("min_n_confirm", 1)),
        )
        self.incumbent_id, self.incumbent_adapter = inherit_incumbent(root, state, str(cfg["model"]), log)
        self.kept_samples: dict[str, list[dict[str, str]]] = {}  # keyed by teacher
        self.anchor_nll_incumbent: float | None = None
        self.spent_gpu_h = 0.0
        self.hf_home = snapshot.parents[3]
        self.templates = root / "harness" / "prompts" / "eval"
        # How this track's answers are read, and therefore both which scorer verifies a rejection sample and
        # which template asks for it. The sampling path used to name `gsm8k_v1.md` and the numeric scorer in
        # three places, so a choice track would have prompted for a number and then refused every gold.
        # `training.template` may differ from the evaluation template; it defaults to it, which is the case
        # worth defaulting to, since training and evaluation prompts agreeing is the point.
        self.answer_kind = str(cfg.get("answer_kind") or "numeric")
        self.scorer = scorer_for(self.answer_kind)
        evaluation = cfg.get("evaluation") or {}
        training = cfg.get("training") or {}
        self.eval_template = str(evaluation.get("template") or "gsm8k_v1")
        self.train_template = str(training.get("template") or self.eval_template)
        self.sealed = _load_sealed(root / ".pravrudhi" / "kernel" / "sealed" / "predictions")

    def job_dir(self, tag: str) -> Path:
        d = Path(self.state.jobs_dir) / f"n{self.night}-{tag}-{int(time.time() * 1000) % 10**8}"
        (d / "in").mkdir(parents=True, exist_ok=True)
        (d / "out").mkdir(parents=True, exist_ok=True)
        return d

    def run(
        self,
        job: str,
        args: list[str],
        job_dir: Path,
        extra_mounts: dict[str, str] | None = None,
        timeout_s: int = 5400,
    ) -> tuple[Any, dict[str, Any] | None]:
        cont_model = "/models/" + str(self.snapshot.relative_to(self.hf_home))
        return self.run_raw(job, ["--model-dir", cont_model, *args], job_dir, extra_mounts, timeout_s)

    def run_raw(
        self,
        job: str,
        args: list[str],
        job_dir: Path,
        extra_mounts: dict[str, str] | None = None,
        timeout_s: int = 5400,
    ) -> tuple[Any, dict[str, Any] | None]:
        """Like run, but the caller supplies --model-dir (teacher sampling uses a different model)."""
        mounts = {str(job_dir / "in"): "/in", str(self.hf_home): "/models", **(extra_mounts or {})}
        spec = JobSpec(
            image=IMAGE,
            command=[job, *args],
            mounts_ro=mounts,
            output_dir=str(job_dir / "out"),
            gpu=True,
            network=False,
            timeout_s=timeout_s,
            user=f"{os.getuid()}:{os.getgid()}",
            env={"HF_HOME": "/models", "HF_HUB_OFFLINE": "1"},
        )
        res = run_job(spec)
        self.spent_gpu_h += res.wall_s / 3600.0 * float(self.cfg["budget"]["gpu_cost_per_hour"])
        meta_p = job_dir / "out" / "job_meta.json"
        return res, (json.loads(meta_p.read_text()) if meta_p.exists() else None)


def _load_sealed(d: Path) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    if d.exists():
        for p in sorted(d.glob("*.jsonl")):
            for line in p.read_text().splitlines():
                if line.strip():
                    r = json.loads(line)
                    out[r["candidate_id"]] = r
    return out


def ensure_samples(ctx: NightContext, w: LedgerWriter, teacher: str = "incumbent") -> list[dict[str, str]]:
    """One rejection-sampling job per night per teacher on train prompts; the kernel scorer keeps the verified ones."""
    if teacher in ctx.kept_samples:
        return ctx.kept_samples[teacher]
    s = ctx.cfg["sampling"]
    rows = ctx.train_rows[int(s["prompts_offset"]) : int(s["prompts_offset"]) + int(s["n_prompts"])]
    jd = ctx.job_dir("sample" if teacher == "incumbent" else "sample-teacher")
    (jd / "in" / "prompts.jsonl").write_text(
        "".join(json.dumps({"id": f"tr{i}", "question": r["question"]}) + "\n" for i, r in enumerate(rows))
    )
    (jd / "in" / "template.txt").write_text((ctx.templates / f"{ctx.train_template}.md").read_text())
    args = [
        "--n-samples",
        str(s["n_samples"]),
        "--temperature",
        str(s["temperature"]),
        "--max-new-tokens",
        str(s["max_new_tokens"]),
        "--batch-size",
        "32",
        "--seed",
        str(ctx.night),
    ]
    extra = {str(ctx.incumbent_adapter): "/adapter"} if (ctx.incumbent_adapter and teacher == "incumbent") else None
    if ctx.incumbent_adapter and teacher == "incumbent":
        args += ["--adapter-dir", "/adapter"]
    if teacher != "incumbent":
        from pravrudhi.application.spine import resolve_model_snapshot

        tsnap = resolve_model_snapshot(teacher)
        args = ["--model-dir", "/models/" + str(tsnap.relative_to(ctx.hf_home))] + args
        res, meta = ctx.run_raw("sample", args, jd, extra)
    else:
        res, meta = ctx.run("sample", args, jd, extra)
    w.append(
        "spend",
        "executor",
        {
            "gpu_h": res.wall_s / 3600,
            "wall_s": res.wall_s,
            "peak_gib": res.peak_gib_smi,
            "run_id": jd.name,
            "phase": "sample",
            "tokens_out": (meta or {}).get("tokens_generated"),
        },
        epoch=0,
        night=ctx.night,
    )
    if res.exit_code != 0 or meta is None:
        w.append(
            "audit",
            "kernel",
            {
                "kind": "job_failed",
                "severity": "high",
                "run_id": jd.name,
                "stderr_tail": res.stderr_tail[-800:],
            },
            epoch=0,
            night=ctx.night,
        )
        raise RuntimeError("sampling job failed")
    golds = {f"tr{i}": ctx.scorer.gold_answer(r["answer"]) for i, r in enumerate(rows)}
    kept: list[dict[str, str]] = []
    n_total = 0
    for line in (jd / "out" / "samples.jsonl").read_text().splitlines():
        r = json.loads(line)
        n_total += 1
        if ctx.scorer.score_completions({r["id"]: r["completion"]}, {r["id"]: golds[r["id"]]})[r["id"]] == 1:
            kept.append(
                {
                    "id": r["id"],
                    "prompt": (ctx.templates / f"{ctx.train_template}.md")
                    .read_text()
                    .replace("{question}", rows[int(r["id"][2:])]["question"]),
                    "completion": r["completion"],
                }
            )
    w.append(
        "audit",
        "kernel",
        {
            "kind": "samples_verified",
            "severity": "info",
            "n_samples": n_total,
            "n_kept": len(kept),
            "kept_rate": len(kept) / max(1, n_total),
            "run_id": jd.name,
        },
        epoch=0,
        night=ctx.night,
    )
    ctx.log(f"sampling ({teacher}): {len(kept)}/{n_total} verified-correct samples kept")
    ctx.kept_samples[teacher] = kept
    return kept


def _select_samples(kept: list[dict[str, str]], recipe: LoraRecipe, seed: int) -> list[dict[str, str]]:
    import random

    rng = random.Random(seed)
    by_prompt: dict[str, list[dict[str, str]]] = {}
    for k in kept:
        by_prompt.setdefault(k["id"], []).append(k)
    f = recipe.sft.filter
    chosen: list[dict[str, str]] = []
    for group in by_prompt.values():
        if f == "shortest_correct":
            chosen.append(min(group, key=lambda g: len(g["completion"])))
        elif f == "longest_correct":
            chosen.append(max(group, key=lambda g: len(g["completion"])))
        elif f == "diverse_correct":
            chosen.extend(group[:2])
        else:
            chosen.extend(group)
    rng.shuffle(chosen)
    return chosen[: recipe.sft.n_kept]


def inherit_incumbent(root: Path, state: KernelState, model: str, log: Any = print) -> tuple[str, Path | None]:
    """The night's incumbent is the latest promoted adapter for this trainee (recursion), else the base model.

    Read from the ledger alone: the last `promote` row on W3.adapter whose candidate was proposed for `model`, with the
    adapter located through the candidate's training spend row. A promoted candidate whose adapter directory is gone
    cannot be the incumbent; that case is logged and the base model is used."""
    from pravrudhi_kernel.ledger.replay import withdrawn_observations
    from pravrudhi_kernel.ledger.verify import iter_events

    target: dict[str, str] = {}
    run_ids: dict[str, str] = {}
    last: str | None = None
    withdrawn = withdrawn_observations(root / "research" / "ledger.jsonl")
    for ev in iter_events(root / "research" / "ledger.jsonl"):
        if ev.kind == "promote" and ev.seq in withdrawn:
            continue
        cid = ev.candidate_id
        if ev.kind == "propose" and cid and ev.bucket is not None:
            target[cid] = ev.bucket.target_model
        elif ev.kind == "spend" and cid and ev.payload.get("phase") == "train" and ev.payload.get("steps"):
            run_ids[cid] = str(ev.payload["run_id"])
        elif ev.kind == "promote" and cid and ev.surface == "W3.adapter" and target.get(cid) == model:
            last = cid
    if last is None:
        return "c-0000", None
    adapter = Path(state.jobs_dir) / run_ids.get(last, "") / "out" / "adapter"
    if not (adapter / "adapter_config.json").exists():
        log(f"incumbent {last} was promoted but its adapter is missing at {adapter}; using the base model")
        return "c-0000", None
    log(f"incumbent inherited from the ledger: {last} ({adapter.parent.parent.name})")
    return last, adapter


def init_args(ctx: NightContext, w: LedgerWriter, cid: str, recipe: LoraRecipe) -> tuple[list[str], dict[str, str] | None]:
    """ADR-0016: an SFT recipe with `init: incumbent` continues the incumbent adapter. When the incumbent is the base
    model there is nothing to continue; the fallback to base is audited so the candidate's lineage is explicit."""
    if recipe.strategy != "sft_rejection" or recipe.sft.init != "incumbent":
        return [], None
    if ctx.incumbent_adapter is None:
        w.append(
            "audit",
            "kernel",
            {"kind": "init_fallback", "severity": "info", "detail": "init=incumbent but the incumbent is the base model"},
            epoch=0,
            night=ctx.night,
            candidate_id=cid,
            surface="W3.adapter",
        )
        return [], None
    return ["--init-adapter", "/init"], {str(ctx.incumbent_adapter): "/init"}


def find_adapter(ctx: NightContext, cid: str) -> Path | None:
    """An adapter already trained for this candidate (any night), located through its spend row's run_id."""
    from pravrudhi_kernel.ledger.verify import iter_events

    for ev in iter_events(ctx.root / "research" / "ledger.jsonl"):
        if ev.kind == "spend" and ev.candidate_id == cid and ev.payload.get("phase") == "train" and ev.payload.get("steps"):
            cand = Path(ctx.state.jobs_dir) / str(ev.payload["run_id"]) / "out" / "adapter"
            if (cand / "adapter_config.json").exists():
                return cand
    return None


def train(ctx: NightContext, w: LedgerWriter, cid: str, recipe: LoraRecipe) -> Path | None:
    prior = find_adapter(ctx, cid)
    if prior is not None:
        ctx.log(f"{cid}: re-using adapter from {prior.parent.parent.name}")
        return prior
    jd = ctx.job_dir(f"train-{cid}")
    (jd / "in" / "recipe.json").write_text(json.dumps(recipe.model_dump(), sort_keys=True))
    if recipe.strategy == "sft_rejection":
        kept = ensure_samples(ctx, w, recipe.sft.teacher)
        rows = _select_samples(kept, recipe, seed=int(cid[2:]))
        (jd / "in" / "train.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
        args, mounts = init_args(ctx, w, cid, recipe)
        res, meta = ctx.run("train_sft", ["--seed", str(ctx.night), *args], jd, extra_mounts=mounts)
    else:
        g = ctx.cfg["grpo_prompts"]
        rows_g = ctx.train_rows[int(g["prompts_offset"]) : int(g["prompts_offset"]) + int(g["n_prompts"])]
        (jd / "in" / "prompts.jsonl").write_text(
            "".join(
                json.dumps({"id": f"g{i}", "question": r["question"], "gold": ctx.scorer.gold_answer(r["answer"])})
                + "\n"
                for i, r in enumerate(rows_g)
            )
        )
        (jd / "in" / "template.txt").write_text((ctx.templates / f"{ctx.train_template}.md").read_text())
        res, meta = ctx.run("train_grpo", ["--seed", str(ctx.night), "--fp32"], jd)
    w.append(
        "spend",
        "executor",
        {
            "gpu_h": res.wall_s / 3600,
            "wall_s": res.wall_s,
            "peak_gib": res.peak_gib_smi,
            "run_id": jd.name,
            "phase": "train",
            "steps": (meta or {}).get("steps"),
            "train_loss": (meta or {}).get("train_loss"),
        },
        epoch=0,
        night=ctx.night,
        candidate_id=cid,
        surface="W3.adapter",
    )
    if res.exit_code != 0 or meta is None or not (jd / "out" / "adapter").exists():
        w.append(
            "audit",
            "kernel",
            {
                "kind": "job_failed",
                "severity": "high",
                "run_id": jd.name,
                "exit_code": res.exit_code,
                "stderr_tail": res.stderr_tail[-1200:],
            },
            epoch=0,
            night=ctx.night,
            candidate_id=cid,
            surface="W3.adapter",
        )
        ctx.log(f"{cid}: training FAILED exit {res.exit_code}")
        return None
    ctx.log(
        f"{cid}: trained {recipe.strategy} steps={meta.get('steps')} loss={meta.get('train_loss'):.4f} wall={res.wall_s:.0f}s"
    )
    return jd / "out" / "adapter"


def _eval_arm(
    ctx: NightContext, rot: Any, seed: int, adapter: Path | None, template: str, tag: str
) -> tuple[Path, Any, dict[str, Any] | None]:
    jd = ctx.job_dir(f"eval-{tag}")
    write_job_inputs(jd, ctx.pool_dir, rot, ctx.templates / f"{template}.md")
    e = ctx.cfg["evaluation"]
    res, meta = run_eval_job(
        ctx.state,
        jd,
        ctx.snapshot,
        adapter,
        seed=seed,
        temperature=float(e["temperature"]),
        max_new_tokens=int(e["max_new_tokens"]),
        batch_size=int(e["batch_size"]),
    )
    ctx.spent_gpu_h += res.wall_s / 3600.0
    return jd, res, meta


def _distinct2(jd: Path) -> tuple[float, float]:
    grams: set[tuple[str, str]] = set()
    n = 0
    lens: list[int] = []
    for line in (jd / "out" / "completions.jsonl").read_text().splitlines():
        toks = json.loads(line)["completion"].split()
        lens.append(len(toks))
        for a, b in zip(toks, toks[1:], strict=False):
            grams.add((a, b))
            n += 1
    return (len(grams) / max(1, n), sum(lens) / max(1, len(lens)))


def _anchor_nll(ctx: NightContext, w: LedgerWriter, adapter: Path | None, cid: str) -> float | None:
    a = ctx.cfg["anchors"]
    rows = ctx.train_rows[int(a["train_offset"]) : int(a["train_offset"]) + int(a["n"])]
    jd = ctx.job_dir(f"anchor-{cid}")
    (jd / "in" / "anchors.jsonl").write_text(
        "".join(json.dumps({"text": r["question"] + "\n" + r["answer"]}) + "\n" for r in rows)
    )
    args = ["--adapter-dir", "/adapter"] if adapter else []
    res, meta = ctx.run("anchor_nll", args, jd, {str(adapter): "/adapter"} if adapter else None, timeout_s=1200)
    w.append(
        "spend",
        "executor",
        {"gpu_h": res.wall_s / 3600, "wall_s": res.wall_s, "run_id": jd.name, "phase": "canary"},
        epoch=0,
        night=ctx.night,
        candidate_id=cid,
        surface="W3.adapter",
    )
    return float(meta["nll_mean"]) if meta and "nll_mean" in meta else None


def evaluate_and_dispose(ctx: NightContext, w: LedgerWriter, cid: str, recipe: LoraRecipe, adapter: Path) -> str:
    st = replay(ctx.root / "research" / "ledger.jsonl")
    n_prev = st.candidates[cid].n_obs if cid in st.candidates else 0
    xs_prev = list(st.candidates[cid].xs) if cid in st.candidates else []
    seed = n_prev
    secret = read_secret(ctx.state)
    e = ctx.cfg["evaluation"]
    rot = draw_rotation(
        ctx.pool_dir,
        ctx.night,
        f"{cid}-s{seed}",
        secret,
        k=int(e["k_items"]),
        exposure_cap=int(e["exposure_cap"]),
    )
    record_exposure(ctx.pool_dir, rot)
    template = recipe.eval_template if (ctx.templates / f"{recipe.eval_template}.md").exists() else ctx.eval_template
    inc_dir, inc_res, inc_meta = _eval_arm(ctx, rot, seed, ctx.incumbent_adapter, template, f"inc-{cid}")
    can_dir, can_res, can_meta = _eval_arm(ctx, rot, seed, adapter, template, f"cand-{cid}")
    if inc_res.exit_code != 0 or can_res.exit_code != 0 or inc_meta is None or can_meta is None:
        w.append(
            "audit",
            "kernel",
            {
                "kind": "job_failed",
                "severity": "high",
                "detail": "paired eval failed",
                "inc_exit": inc_res.exit_code,
                "cand_exit": can_res.exit_code,
            },
            epoch=0,
            night=ctx.night,
            candidate_id=cid,
            surface="W3.adapter",
        )
        return "failed"
    inc_scores, inc_ref = score_job(inc_dir, ctx.pool_dir, rot)
    can_scores, can_ref = score_job(can_dir, ctx.pool_dir, rot)
    harness_dir = ctx.root / "harness"
    bucket = {
        "task_family": ctx.variance.bench,
        "target_model": str(ctx.cfg["model"]),
        "corpus": f"{ctx.variance.bench}-train",
    }
    inc_exp_raw = expected_hashes(inc_dir, ctx.pool_dir, harness_dir, ctx.snapshot)
    can_exp_raw = expected_hashes(can_dir, ctx.pool_dir, harness_dir, ctx.snapshot).model_copy(
        update={"harness": model_dir_hash(adapter)}
    )
    # When an incumbent adapter is used, record its hash so replay's rebase detection can fire
    # (ADR-0013, H5 in ADVERSARIAL-2026-09-11.md)
    incumbent_adapter_hash: str | None = None
    if ctx.incumbent_adapter is not None:
        incumbent_adapter_hash = model_dir_hash(ctx.incumbent_adapter)
        # Update incumbent observation to use incumbent adapter's hash, not base snapshot hash
        inc_exp_raw = inc_exp_raw.model_copy(update={"harness": incumbent_adapter_hash})
        # Wrap both to inject harness_parent for rebase detection
        inc_exp: KernelHashes | _HashesWithParent = _HashesWithParent(inc_exp_raw, incumbent_adapter_hash)
        can_exp: KernelHashes | _HashesWithParent = _HashesWithParent(can_exp_raw, incumbent_adapter_hash)
    else:
        # No incumbent adapter: no harness_parent needed
        inc_exp = _HashesWithParent(inc_exp_raw, None)
        can_exp = _HashesWithParent(can_exp_raw, None)
    inc_value = sum(inc_scores.values()) / len(inc_scores)
    can_value = sum(can_scores.values()) / len(can_scores)
    delta = can_value - inc_value
    xs = [*xs_prev, delta]
    br = sequential_boundary(xs, ctx.variance)
    sealed = ctx.sealed.get(cid)
    predicted = None
    brier = None
    if sealed:
        predicted = {
            "delta_in": sealed["delta_in"],
            "delta_out": None,
            "conf": sealed["conf"],
            "hash": sealed["hash"],
        }
        y = 1.0 if (sealed["delta_in"] >= 0) == (delta >= 0) else 0.0
        brier = (sealed["conf"] - y) ** 2
    admit_observation(
        w,
        expected=cast(KernelHashes, inc_exp),
        job_meta=inc_meta,
        per_item_scores=inc_scores,
        per_item_ref=str(inc_ref),
        run_id=inc_dir.name,
        candidate_id=ctx.incumbent_id,
        surface="W3.adapter",
        bucket=bucket,
        epoch=0,
        night=ctx.night,
        cycle=None,
        seed=seed,
        rotation_id=rot.rotation_id,
        value_ref=None,
        cost_gpu_h=inc_res.wall_s / 3600,
        wall_s=inc_res.wall_s,
        peak_gib=inc_res.peak_gib_smi,
        isolation=ctx.state.isolation,
        stage="screen",
        extra={"arm": "incumbent", "paired_with": cid},
    )
    d2_inc, len_inc = _distinct2(inc_dir)
    d2_can, len_can = _distinct2(can_dir)
    canary: dict[str, Any] = {
        "distinct2_ratio": d2_can / max(1e-9, d2_inc),
        "length_ratio": len_can / max(1e-9, len_inc),
        "per_canary": {},
    }
    _, obs = admit_observation(
        w,
        expected=cast(KernelHashes, can_exp),
        job_meta=can_meta,
        per_item_scores=can_scores,
        per_item_ref=str(can_ref),
        run_id=can_dir.name,
        candidate_id=cid,
        surface="W3.adapter",
        bucket=bucket,
        epoch=0,
        night=ctx.night,
        cycle=None,
        seed=seed,
        rotation_id=rot.rotation_id,
        value_ref=inc_value,
        cost_gpu_h=can_res.wall_s / 3600,
        wall_s=can_res.wall_s,
        peak_gib=can_res.peak_gib_smi,
        isolation=ctx.state.isolation,
        stage="screen",
        extra={
            "arm": "candidate",
            "incumbent_run_id": inc_dir.name,
            "predicted": predicted,
            "brier": brier,
            # Guarded on the pool's declared kind, like the harness track: `discordance` refuses a fractional
            # score rather than computing a McNemar test that does not apply (ADR-0038). This call site was
            # unconditional, so a model-track night on a `set` pool raised here on its first candidate.
            **discordance_fields(ctx.answer_kind, inc_scores, can_scores),
            "stats": {
                "boundary": br.decision,
                "e_value": br.e_value,
                "xbar": br.xbar,
                "halfwidth": br.halfwidth,
                "sigma_used": br.sigma_used,
                "n": br.n,
            },
            "canary": canary,
            "recipe_strategy": recipe.strategy,
        },
    )
    ctx.log(
        f"{cid}: seed {seed} incumbent={inc_value:.3f} candidate={can_value:.3f} "
        f"delta={delta:+.3f} boundary={br.decision} (n={br.n}, E={br.e_value:.2f})"
    )
    if br.decision == "prune":
        w.append(
            "prune",
            "kernel",
            {
                "hetvabhasa": br.hetvabhasa or "asiddha",
                "reason": f"sequential boundary at n={br.n}: xbar={br.xbar:+.4f} halfwidth={br.halfwidth:.4f}",
                "stage": "screen",
                "boundary": "prune",
                "by": "sequential",
                "status": "pruned",
            },
            epoch=0,
            night=ctx.night,
            candidate_id=cid,
            surface="W3.adapter",
        )
        return "pruned"
    if br.decision == "confirm":
        return _canaries_and_promote(ctx, w, cid, recipe, adapter, canary, obs.seq, delta)
    return "continue"


def _canaries_and_promote(
    ctx: NightContext,
    w: LedgerWriter,
    cid: str,
    recipe: LoraRecipe,
    adapter: Path,
    canary: dict[str, Any],
    obs_seq: int,
    delta: float,
) -> str:
    if ctx.anchor_nll_incumbent is None:
        ctx.anchor_nll_incumbent = _anchor_nll(ctx, w, ctx.incumbent_adapter, ctx.incumbent_id)
    nll_c = _anchor_nll(ctx, w, adapter, cid)
    results = {}
    rel = (
        (nll_c - ctx.anchor_nll_incumbent) / ctx.anchor_nll_incumbent
        if (nll_c is not None and ctx.anchor_nll_incumbent)
        else None
    )
    results["anchor_nll"] = {
        "incumbent": ctx.anchor_nll_incumbent,
        "candidate": nll_c,
        "relative_increase": rel,
        "pass": rel is not None and rel <= 0.03,
    }
    results["distinct2"] = {"ratio": canary["distinct2_ratio"], "pass": canary["distinct2_ratio"] >= 0.90}
    results["entropy_proxy"] = {
        "length_ratio": canary["length_ratio"],
        "pass": 0.5 <= canary["length_ratio"] <= 2.0,
    }
    all_pass = all(v["pass"] for v in results.values())
    w.append(
        "audit",
        "kernel",
        {
            "kind": "canary",
            "severity": "info" if all_pass else "high",
            "detail": results,
            "observe_seq": obs_seq,
        },
        epoch=0,
        night=ctx.night,
        candidate_id=cid,
        surface="W3.adapter",
    )
    if not all_pass:
        w.append(
            "prune",
            "kernel",
            {
                "hetvabhasa": "badhita",
                "reason": "canary failure: " + ", ".join(k for k, v in results.items() if not v["pass"]),
                "stage": "confirm",
                "boundary": "confirm",
                "by": "canary",
                "status": "pruned",
            },
            epoch=0,
            night=ctx.night,
            candidate_id=cid,
            surface="W3.adapter",
        )
        ctx.log(f"{cid}: canary FAIL -> pruned (badhita)")
        return "pruned"
    pack = ctx.root / "research" / "inbox" / f"night{ctx.night}" / cid
    pack.mkdir(parents=True, exist_ok=True)
    (pack / "README.md").write_text(
        f"# Promotion pack {cid} (night {ctx.night})\n\nrecipe: `{json.dumps(recipe.model_dump())}`\n\n"
        f"adapter: `{adapter}`\nlast paired delta: {delta:+.4f}\ncanaries: `{json.dumps(results)}`\n\n"
        "Promotion to T2 (incumbent for subsequent pairings) is automatic; merging into base weights is a human act "
        "(`pravrudhi gate sign` / inbox --sign in L5).\n"
    )
    w.append(
        "promote",
        "broker",
        {
            "tier": "T2",
            "from_worktree": str(adapter),
            "merge_commit": model_dir_hash(adapter),
            "tag": f"night{ctx.night}-{cid}",
            "lineage": [ctx.incumbent_id],
            "tau_before": 0.5,
            "tau_after": 0.6,
            "inbox_pack": str(pack),
            "regression": {"pass3": None, "wilson_lo": None, "flips": 0},
            "holm": {"m": 1, "rank": 1, "p_adj": None},
        },
        epoch=0,
        night=ctx.night,
        candidate_id=cid,
        surface="W3.adapter",
    )
    ctx.incumbent_id, ctx.incumbent_adapter, ctx.anchor_nll_incumbent = cid, adapter, nll_c
    ctx.log(f"{cid}: PROMOTED to incumbent (T2); pack {pack}")
    return "promoted"
