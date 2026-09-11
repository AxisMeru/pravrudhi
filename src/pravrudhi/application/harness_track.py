"""Track H: a fixed model, a mutable harness, MBPP+ hidden tests scored in the sandbox. Same ledger, same boundary."""

from __future__ import annotations

import hashlib
import json
import os
import time
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any

import yaml

from pravrudhi.application.deliberate import DecorativeAbort, deliberate
from pravrudhi.application.discordance import discordance_fields
from pravrudhi.application.propose import next_candidate_id, propose_generic, strategy_switch_rate
from pravrudhi.application.spine import IMAGE, resolve_model_snapshot
from pravrudhi.models.proposer import proposer_client
from pravrudhi.targets.harness_grammar import BASELINE, H_GRAMMAR_DOC, HarnessRecipe, harness_array_schema, parse_harness
from pravrudhi_kernel.ledger import LedgerWriter, replay
from pravrudhi_kernel.metrics import (
    PoolExhausted,
    Rotation,
    answer_kind,
    draw_rotation,
    record_exposure,
    scorer_for_pool,
    scorer_source_for_pool,
    unparsed,
)
from pravrudhi_kernel.metrics.pool import load_manifest, read_item
from pravrudhi_kernel.sandbox import (
    JobResult,
    JobSpec,
    KernelState,
    admit_observation,
    ensure_kernel_state,
    run_job,
)
from pravrudhi_kernel.sandbox.observe import KernelHashes, kernel_hashes, sha256_file
from pravrudhi_kernel.sandbox.runner import docker_available
from pravrudhi_kernel.sandbox.state import read_secret
from pravrudhi_kernel.stats import Variance, sequential_boundary

EXT_IMAGE = "pravrudhi/ext-scorers:latest"
JOBS_DIR = Path(__file__).resolve().parents[3] / "docker" / "jobs"
# pool manifest bench name -> (scorer job, whether that scorer reads the sealed pool's hidden tests)
SCORERS: dict[str, tuple[str, bool]] = {"mbppplus": ("score_code.py", False), "apps": ("score_apps.py", True)}
Log = Callable[[str], None]
BASELINE_ID = "c-0000"


def kernel_scored(pool_dir: Path) -> bool:
    """Whether this pool is scored in process by the kernel rather than by a container job.

    Scoring a code pool means EXECUTING the candidate's solution against hidden tests, which needs a sandbox.
    Scoring a multiple-choice pool means comparing a letter, which the kernel already does -- and which must
    stay in the kernel: ADR-0035 put the choice scorer in T0 so the engine would not compute the numbers, and a
    `score_choice.py` beside `score_code.py` would put it straight back.

    The rule has to survive a code pool nobody remembered to register. `mbppplus` and `apps` were sealed before
    `answer_kind` existed, so their manifests declare none and `pool.answer_kind` defaults to numeric; routing
    on that alone would hand a JSON answer blob to the numeric scorer and score every item zero, which reads as
    a harness that got worse rather than a scorer that was never right. So a registered bench keeps its
    container job, an unregistered one MUST declare its kind, and a pool that does neither is refused.
    """
    manifest = load_manifest(pool_dir)
    bench = str(manifest["bench"])
    if bench in SCORERS:
        return False
    if "answer_kind" not in manifest:
        raise KeyError(
            f"bench {bench!r} has no harness scorer and its manifest declares no answer_kind, so nothing can "
            f"say how to score it; either register a scorer job in SCORERS or seal the pool with an "
            f"answer_kind. Known container benches: {sorted(SCORERS)}"
        )
    return True


def _scorer(pool_dir: Path) -> tuple[Path, bool]:
    """Which scorer runs a pool's hidden tests, chosen by the bench name the pool was sealed under.

    With one internal code pool the scorer could be a module constant. With two it cannot: the EvalPlus scorer
    asked for an APPS task id would raise on a task EvalPlus has never heard of, every item would score zero,
    and the ledger would record a harness that got worse rather than a scorer that was never run. The same
    lookup names the scorer hashed into the observation, so a row cannot claim a scorer that did not produce
    its numbers."""
    bench = str(load_manifest(pool_dir)["bench"])
    if bench not in SCORERS:
        raise KeyError(f"no harness scorer for bench {bench!r}; known benches: {sorted(SCORERS)}")
    job, needs_pool = SCORERS[bench]
    return JOBS_DIR / job, needs_pool


class HarnessContext:
    def __init__(  # noqa: D107
        self, root: Path, cfg: dict[str, Any], night: int, log: Log, *, measuring: bool = False
    ) -> None:
        # `measuring=True` for the study that WRITES the floor. The pairing checks below refuse a floor that
        # does not match this config's bench and baseline -- correct for a night, and a deadlock for the study
        # that would replace it: the floor study builds this same context, so it was refused by the staleness
        # it exists to fix, and the error told the reader to run the command that had just failed. Skipping
        # them here is safe because `floor_dest` still refuses to overwrite another BENCH's floor on the way
        # out, which is the loss that check protects against.
        self.measuring = measuring
        self.root, self.cfg, self.night, self.log = root, cfg, night, log
        self.state: KernelState = ensure_kernel_state(root, docker_available=docker_available())
        self.snapshot = resolve_model_snapshot(str(cfg["model"]))
        self.hf_home = self.snapshot.parents[3]
        self.bench = str(cfg["bench"])
        self.pool_dir = root / ".pravrudhi" / "kernel" / "pools" / self.bench
        # How this pool's answers are read, from the POOL's manifest rather than the config, because the
        # manifest is what the scorer dispatches on. A config that declares a different kind is refused: that
        # is the same disagreement-between-frozen-inputs defect as a floor measured on another bench.
        self.answer_kind = answer_kind(self.pool_dir) if self.pool_dir.exists() else str(cfg.get("answer_kind") or "numeric")
        declared_kind = str(cfg.get("answer_kind") or "")
        if declared_kind and self.pool_dir.exists() and declared_kind != self.answer_kind:
            raise ValueError(
                f"{self.bench} was sealed with answer_kind {self.answer_kind!r} but this config declares "
                f"{declared_kind!r}; the scorer dispatches on the manifest, so the config would describe a "
                f"night that is not being run"
            )
        self.incumbent_id = BASELINE_ID
        # The recipe every candidate this night is paired against. `harness_grammar.BASELINE` is a CODE
        # recipe -- "You are an expert Python programmer ... return only the code in one ```python block" --
        # and it was the starting incumbent on a law multiple-choice bench, which is most of why the harness
        # baseline scored 0.1042 there against the model track's 0.3993. Fixing the proposer prompt (night
        # 19) was half the defect; the other half is that the search started from a recipe for another task.
        # Config-driven so the two nights cannot disagree, and absent means the grammar default, which keeps
        # the code track exactly as it was.
        self.baseline: HarnessRecipe = baseline_recipe(root, cfg)
        self.incumbent: HarnessRecipe = self.baseline
        self.spent_gpu_h = 0.0
        self.sealed = _load_sealed(root / ".pravrudhi" / "kernel" / "sealed" / "predictions")
        # The floor this config names, or the track's default. A config whose bench does not match the floor's
        # is refused below: `harness_night.yaml` ran bench `apps` (ADR-0029) against a floor measured on
        # `mbppplus`, so this track had the same silent defect the model track did.
        vf = floor_path(root, cfg)
        self.variance_file = vf
        self.variance: Variance | None = None
        if vf.exists() and not measuring:
            v = json.loads(vf.read_text())
            measured, bench = str(v.get("bench") or ""), self.bench
            if measured and measured != bench:
                raise ValueError(
                    f"{vf.name} was measured on bench {measured!r} but this night runs {bench!r}; the boundary "
                    f"would be set from another pool's sigma. Measure it with `pravrudhi study "
                    f"harness-noise-floor` on {bench!r} first."
                )
            # The bench check above is half the pairing. The other half is the BASELINE: a floor is the
            # variance of repeated runs of one recipe, and pairing candidates against a different recipe
            # sets the boundary from another arm's sigma -- the same defect as a floor from another pool,
            # which ADR-0037 found in four places. Changing `baseline_recipe:` therefore invalidates the
            # floor, and this refuses rather than trusting anyone to remember that.
            want = baseline_sha256(self.baseline)
            held = str(v.get("baseline_sha256") or "")
            if held != want:
                raise ValueError(
                    f"{vf.name} was measured at baseline "
                    f"{held[:12] + '...' if held else '(unrecorded, before ADR-0037)'} and this night pairs "
                    f"against {want[:12]}...; the boundary would come from another recipe's variance. "
                    f"Re-measure the floor before running a night on it -- `pravrudhi study "
                    f"harness-noise-floor` writes it and is not subject to this check, since it is the thing "
                    f"that fixes it."
                )
            b = cfg["boundary"]
            dm = max(2 * float(v["sigma_seed"]), float(b["delta_min_floor"]))
            self.variance = Variance.model_validate(
                {
                    "bench": str(v["bench"]),
                    "sigma_seed": max(float(v["sigma_seed"]), 1e-4),
                    "tau": dm,
                    "delta_min": dm,
                    "alpha_eff": float(b["alpha_eff"]),
                    "alpha_fut": float(b["alpha_fut"]),
                    "k_max": int(b["k_max"]),
                    "sigma_mode": str(b["sigma_mode"]),
                    "n0": int(b["n0"]),
                    "min_n_confirm": int(b.get("min_n_confirm", 1)),
                }
            )
        # `corpus` was the literal string "mbppplus" for every bench this track has ever run, so the ledger's
        # casehold-val and mmlu-law-val observations all claimed a CODE corpus that was never involved. Found
        # by reading an observation envelope, not by any check. `Bucket.corpus` is a required `str`, so the
        # honest value is not None but a statement of the fact: this track trains nothing, and what varies is
        # the harness. The bench is already in `task_family`; repeating it here as a corpus would be a
        # different untruth.
        self.bucket = {
            "task_family": self.bench,
            "target_model": str(cfg["model"]),
            "corpus": "none:harness-track-trains-nothing",
        }

    def job_dir(self, tag: str) -> Path:
        d = Path(self.state.jobs_dir) / f"h{self.night}-{tag}-{int(time.time() * 1000) % 10**8}"
        (d / "in").mkdir(parents=True, exist_ok=True)
        (d / "out").mkdir(parents=True, exist_ok=True)
        return d


def _load_sealed(d: Path) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    if d.exists():
        for p in sorted(d.glob("*.jsonl")):
            for line in p.read_text().splitlines():
                if line.strip():
                    r = json.loads(line)
                    out[r["candidate_id"]] = r
    return out


def run_agent(
    ctx: HarnessContext, harness: HarnessRecipe, rot: Rotation, seed: int, tag: str
) -> tuple[Path, Any, dict[str, Any] | None]:
    jd = ctx.job_dir(f"agent-{tag}")
    with (jd / "in" / "items.jsonl").open("w") as fh:
        for i in rot.item_ids:
            it = read_item(ctx.pool_dir, i)
            fh.write(json.dumps({"id": it["id"], "question": it["question"]}) + "\n")
    (jd / "in" / "harness.json").write_text(json.dumps(harness.harness_json(), sort_keys=True))
    cont_model = "/models/" + str(ctx.snapshot.relative_to(ctx.hf_home))
    # `agent_code` extracts a fenced Python block and selects among candidates by RUNNING the visible tests a
    # code prompt carries. On a choice pool there are no visible tests and the answer is a letter, so retries,
    # n_samples and use_visible_tests would every one be inert while the track reported it had explored them.
    # `agent_choice` gives those knobs gold-free meanings instead: majority vote across samples, and retry on
    # a completion that committed to no letter.
    job = "agent_choice" if kernel_scored(ctx.pool_dir) else "agent_code"
    spec = JobSpec(
        image=IMAGE,
        command=[job, "--model-dir", cont_model, "--seed", str(seed), "--batch-size", "16"],
        mounts_ro={str(jd / "in"): "/in", str(ctx.hf_home): "/models"},
        output_dir=str(jd / "out"),
        gpu=True,
        network=False,
        timeout_s=5400,
        user=f"{os.getuid()}:{os.getgid()}",
        env={"HF_HOME": "/models", "HF_HUB_OFFLINE": "1"},
    )
    res = run_job(spec)
    ctx.spent_gpu_h += res.wall_s / 3600.0
    meta_p = jd / "out" / "job_meta.json"
    return jd, res, (json.loads(meta_p.read_text()) if meta_p.exists() else None)


def _score_in_process(ctx: HarnessContext, jd: Path, rot: Rotation) -> tuple[Mapping[str, float], Path, JobResult]:
    """Score a non-code pool with the scorer its own manifest declares. No container: nothing to execute.

    Mirrors `spine.score_job`, including `unparsed.json`: a completion that commits to no answer scores 0
    exactly like a wrong one, and on this bench a token cap once depressed a pass rate by 0.21.
    """
    scorer = scorer_for_pool(ctx.pool_dir)
    sd = ctx.job_dir(f"score-{jd.name[-8:]}")
    samples: dict[str, str] = {}
    for line in (jd / "out" / "samples.jsonl").read_text().splitlines():
        if line.strip():
            r = json.loads(line)
            # The agent job writes `solution` for code benches; a text answer may arrive under either name.
            samples[str(r["id"])] = str(r.get("solution") if r.get("solution") is not None else r.get("completion", ""))
    golds = {i: scorer.gold_answer(read_item(ctx.pool_dir, i)["answer"]) for i in rot.item_ids}
    scores = scorer.score_completions(samples, golds)
    ref = sd / "out" / "per_item_scores.jsonl"
    ref.write_text("".join(json.dumps({"id": i, "score": s}) + "\n" for i, s in sorted(scores.items())))
    misses = unparsed(scorer, samples)
    if misses:
        (sd / "out" / "unparsed.json").write_text(json.dumps({"n": len(misses), "ids": misses}, indent=2) + "\n")
    return (
        scores,
        ref,
        # A real JobResult so every caller keeps working, and honest about what happened: no container ran, so
        # there is no wall clock to charge and no exit code to report but the absence of a failure.
        JobResult(
            exit_code=0,
            wall_s=0.0,
            peak_gib_smi=None,
            stdout_tail=f"scored in process by {scorer_source_for_pool(ctx.pool_dir).name}; no container job",
            stderr_tail="",
            timed_out=False,
        ),
    )


def score_agent(ctx: HarnessContext, jd: Path, rot: Rotation) -> tuple[Mapping[str, float], Path, Any]:
    """Kernel-launched hidden-test execution in the scorers image: no network, no GPU, disposable.

    A pool with no container scorer is scored in process instead; see `kernel_scored`."""
    if kernel_scored(ctx.pool_dir):
        return _score_in_process(ctx, jd, rot)
    sd = ctx.job_dir(f"score-{jd.name[-8:]}")
    (sd / "in" / "samples.jsonl").write_text((jd / "out" / "samples.jsonl").read_text())
    with (sd / "in" / "answers.jsonl").open("w") as fh:
        for i in rot.item_ids:
            fh.write(json.dumps({"id": i, "task_id": json.loads(read_item(ctx.pool_dir, i)["answer"])["task_id"]}) + "\n")
    scorer, needs_pool = _scorer(ctx.pool_dir)
    mounts = {str(sd / "in"): "/in", str(ctx.root / ".pravrudhi" / "ext_cache"): "/cache"}
    if needs_pool:
        (sd / "in" / "pool").mkdir(exist_ok=True)  # the mountpoint must exist inside the read-only /in bind
        mounts[str(ctx.pool_dir)] = "/in/pool"
    spec = JobSpec(
        image=EXT_IMAGE,
        command=["python", f"/opt/pravrudhi/jobs/{scorer.name}"],
        mounts_ro=mounts,
        output_dir=str(sd / "out"),
        gpu=False,
        network=False,
        timeout_s=1800,
        user=f"{os.getuid()}:{os.getgid()}",
        env={"HOME": "/cache", "HF_HOME": "/cache"},
    )
    res = run_job(spec)
    scores: dict[str, int] = {}
    sp = sd / "out" / "scores.jsonl"
    if sp.exists():
        for line in sp.read_text().splitlines():
            if line.strip():
                r = json.loads(line)
                scores[r["id"]] = int(r["score"])
    # A scoring job that did not run must not become a rotation of zeros.
    #
    # `scores.setdefault(i, 0)` below is right for an item the scorer reported nothing about -- that is a
    # failure of that item. It is wrong when the JOB failed, because then it fabricates a verdict for every
    # item at once. The `ext-scorers` image was built before `score_apps.py` existed, so `docker run` died
    # with "can't open file '/opt/pravrudhi/jobs/score_apps.py'", `scores.jsonl` never appeared, and the
    # `apps` bench measured plus_pass=0.0000 for what would have been every rotation of every night -- a
    # stale image reported as a model that cannot code. This is the same defect `docker/entry.sh` had when an
    # unknown job name fell through to `generate.py`: a failure that produces plausible output for something
    # nobody asked for. Refused loudly, and the container's own words are in the message.
    if res.exit_code != 0 or not sp.exists():
        raise RuntimeError(
            f"{scorer.name} did not score this rotation: exit {res.exit_code}"
            f"{', timed out' if res.timed_out else ''}, scores.jsonl "
            f"{'present but unusable' if sp.exists() else 'never written'}. Every item would otherwise have "
            f"been recorded as a failure. stderr: {res.stderr_tail[-400:].strip() or '(empty)'}"
        )
    for i in rot.item_ids:
        scores.setdefault(i, 0)
    ref = sd / "out" / "per_item_scores.jsonl"
    ref.write_text("".join(json.dumps({"id": i, "score": s}) + "\n" for i, s in sorted(scores.items())))
    return scores, ref, res


def _hashes(ctx: HarnessContext, jd: Path, harness: HarnessRecipe) -> KernelHashes:
    # The file that actually scores, whichever path ran. Naming the container job on a kernel-scored bench
    # would be the defect ADR-0035 found in `spine.SCORER_SOURCE`: a row claiming a scorer that did not
    # produce its numbers.
    source = scorer_source_for_pool(ctx.pool_dir) if kernel_scored(ctx.pool_dir) else _scorer(ctx.pool_dir)[0]
    h = kernel_hashes(
        jd / "in" / "items.jsonl",
        ctx.pool_dir / "manifest.json",
        source,
        ctx.root / "harness",
        ctx.snapshot,
    )
    return h.model_copy(
        update={"harness": hashlib.sha256(json.dumps(harness.harness_json(), sort_keys=True).encode()).hexdigest()}
    )


def paired_eval(
    ctx: HarnessContext, w: LedgerWriter, cid: str, cand: HarnessRecipe, seed: int, k: int, stage: str = "screen"
) -> tuple[float, float, int]:
    rot = draw_rotation(
        ctx.pool_dir,
        ctx.night,
        f"{cid}-s{seed}",
        read_secret(ctx.state),
        k=k,
        exposure_cap=int(ctx.cfg["evaluation"]["exposure_cap"]),
    )
    record_exposure(ctx.pool_dir, rot)
    ijd, ires, imeta = run_agent(ctx, ctx.incumbent, rot, seed, f"inc-{cid}")
    cjd, cres, cmeta = run_agent(ctx, cand, rot, seed, f"cand-{cid}")
    if ires.exit_code != 0 or cres.exit_code != 0 or imeta is None or cmeta is None:
        w.append(
            "audit",
            "kernel",
            {
                "kind": "job_failed",
                "severity": "high",
                "detail": "paired agent run failed",
                "inc_exit": ires.exit_code,
                "cand_exit": cres.exit_code,
                "stderr_tail": (cres.stderr_tail or ires.stderr_tail)[-800:],
            },
            epoch=0,
            night=ctx.night,
            candidate_id=cid,
            surface="H3.prompt",
        )
        raise RuntimeError("agent run failed")
    iscores, iref, isres = score_agent(ctx, ijd, rot)
    cscores, cref, csres = score_agent(ctx, cjd, rot)
    iv = sum(iscores.values()) / len(iscores)
    cv = sum(cscores.values()) / len(cscores)
    ih, ch = _hashes(ctx, ijd, ctx.incumbent), _hashes(ctx, cjd, cand)
    imeta["items_sha256"], cmeta["items_sha256"] = ih.items, ch.items
    admit_observation(
        w,
        expected=ih,
        job_meta=with_unparsed(imeta, iref),
        per_item_scores=iscores,
        per_item_ref=str(iref),
        run_id=ijd.name,
        candidate_id=ctx.incumbent_id,
        surface="H3.prompt",
        bucket=ctx.bucket,
        epoch=0,
        night=ctx.night,
        cycle=None,
        seed=seed,
        rotation_id=rot.rotation_id,
        value_ref=None,
        cost_gpu_h=ires.wall_s / 3600,
        wall_s=ires.wall_s,
        peak_gib=ires.peak_gib_smi,
        isolation=ctx.state.isolation,
        stage=stage,
        extra={"arm": "incumbent", "paired_with": cid, "track": "harness", "score_job": isres.exit_code},
    )
    return iv, cv, len(cscores)  # candidate row is written by the caller once the boundary is known


def admit_candidate(
    ctx: HarnessContext,
    w: LedgerWriter,
    cid: str,
    cand: HarnessRecipe,
    cjd_scores: tuple[Mapping[str, float], Path, Any, dict[str, Any], Path],
    seed: int,
    rot_id: str,
    iv: float,
    br: Any,
    extra: dict[str, Any],
    incumbent_scores: Mapping[str, float],
) -> None:
    cscores, cref, csres, cmeta, cjd = cjd_scores
    ch = _hashes(ctx, cjd, cand)
    cmeta["items_sha256"] = ch.items
    admit_observation(
        w,
        expected=ch,
        job_meta=with_unparsed(cmeta, cref),
        per_item_scores=cscores,
        per_item_ref=str(cref),
        run_id=cjd.name,
        candidate_id=cid,
        surface="H3.prompt",
        bucket=ctx.bucket,
        epoch=0,
        night=ctx.night,
        cycle=None,
        seed=seed,
        rotation_id=rot_id,
        value_ref=iv,
        cost_gpu_h=float(extra.get("wall_s_candidate") or 0.0) / 3600,
        wall_s=float(extra.get("wall_s_candidate") or 0.0),
        peak_gib=None,
        isolation=ctx.state.isolation,
        stage="screen",
        extra={
            "arm": "candidate",
            "track": "harness",
            # McNemar is DEFINED on binary paired outcomes (ADR-0038): there is no "win" to count when an
            # item goes from 0.667 to 0.750, and `discordance` refuses a fractional score rather than
            # computing a statistic that does not apply. Sealing the first `set` pool made that live -- a
            # night on `iltur-lsi-dev` would have crashed here on its first candidate. Asked of the pool's
            # DECLARED kind, not of the observed values, so a fractional pool whose items happened to score
            # 0/1 cannot be mistaken for a binary one.
            **discordance_fields(ctx.answer_kind, incumbent_scores, cscores),
            "stats": {
                "boundary": br.decision,
                "e_value": br.e_value,
                "xbar": br.xbar,
                "halfwidth": br.halfwidth,
                "sigma_used": br.sigma_used,
                "n": br.n,
            },
            **extra,
        },
    )


def baseline_recipe(root: Path, cfg: dict[str, Any]) -> HarnessRecipe:
    """The recipe candidates are paired against on this bench.

    A bench that is not code needs its own: the grammar's default recipe instructs the model to be a Python
    programmer and answer in a ```python block, which on a multiple-choice law pool is not a weak harness but
    a wrong one. Declared with `baseline_recipe:` in the pre-registration; absent means the grammar default,
    so the code track is unchanged.
    """
    declared = str(cfg.get("baseline_recipe") or "")
    if not declared:
        return BASELINE
    path = Path(root) / declared
    if not path.exists():
        raise ValueError(f"{path} does not exist, and it is the recipe every candidate tonight is paired against")
    parsed = parse_harness(json.loads(path.read_text()))
    if isinstance(parsed, str):
        # `parse_harness` returns the grammar's complaint as a string rather than raising, because the
        # proposer's candidates are expected to be invalid sometimes. A baseline is not: an unparseable one
        # would silently fall back to the code recipe and the night would measure the mismatch again.
        raise ValueError(f"{path} is not a valid harness recipe: {parsed}")
    return parsed


def with_unparsed(meta: dict[str, Any], per_item_ref: Path) -> dict[str, Any]:
    """`meta` plus the count of completions that committed to no answer at all.

    Both scoring paths already write `unparsed.json` beside the per-item scores, and neither lifted it into
    the observation -- so on a choice bench the term that DOMINATES the measurement was recoverable from a
    job directory and absent from the ledger. The casehold-val floor is the case in point: its three rotations
    scored 0.441, 0.368 and 0.188, and the spread tracks an unparsed rate of 35%, 40% and 52% rather than
    anything about law. A number that explains a result has to be in the row that reports it, or the row
    invites the wrong reading -- and CHARTER §6 says no number is stated that the ledger does not contain.

    Absence is recorded as absence: a run whose scorer wrote no `unparsed.json` gets 0, and one whose file
    cannot be read gets `None` rather than a comforting zero.
    """
    path = Path(per_item_ref).parent / "unparsed.json"
    if not path.exists():
        return {**meta, "n_unparsed": 0}
    try:
        return {**meta, "n_unparsed": int(json.loads(path.read_text())["n"])}
    except (OSError, ValueError, KeyError):
        return {**meta, "n_unparsed": None}


def baseline_sha256(recipe: HarnessRecipe) -> str:
    """The identity of the recipe a floor was measured at, or a night is paired against.

    Over `harness_json()` rather than the model, so `rationale` -- prose that explains a recipe without
    changing what it runs -- does not invalidate a floor.
    """
    return hashlib.sha256(json.dumps(recipe.harness_json(), sort_keys=True).encode()).hexdigest()


def promoted_path(root: Path, bench: str) -> Path:
    """Where a promotion writes the new canonical recipe: one file per bench.

    There was one file for every bench, and `scripts/ext_humaneval.sh` reads it (README §harness track). So a
    law night's promotion silently became what the external HumanEval+ proof ran, and the next code delta
    would have been attributed to the code harness track. Evidence from one bench governing another is the
    same defect as a noise floor measured on another pool; this is the fourth place it appeared.
    """
    return Path(root) / "harness" / "agent" / bench / "harness.json"


def floor_path(root: Path, cfg: dict[str, Any]) -> Path:
    """Where this config's noise floor lives. One rule, read by the night and the study alike, so the two
    cannot disagree about which file is the pair of which bench."""
    declared = str(cfg.get("noise_floor") or "")
    return (Path(root) / declared) if declared else Path(root) / "research" / "prereg" / "variance_harness.json"


def floor_dest(root: Path, cfg: dict[str, Any]) -> Path:
    """`floor_path`, refusing to overwrite a floor measured on a different bench.

    The read side already refuses a floor whose bench does not match the night's. Without this, the way to
    satisfy that check is to overwrite the other bench's floor -- which is how `variance_harness.json`
    (mbppplus, ADR-0029) would have been destroyed by measuring `apps` against the default path, leaving no
    trace and no red. A floor belongs to one bench; a config that needs its own must name it.
    """
    dest = floor_path(root, cfg)
    if dest.exists():
        held = str(json.loads(dest.read_text()).get("bench") or "")
        if held and held != str(cfg["bench"]):
            raise ValueError(
                f"{dest.name} holds the floor measured on bench {held!r} and this study measured "
                f"{cfg['bench']!r}; writing would destroy it. Give this config its own "
                f"`noise_floor: research/prereg/variance_harness_{cfg['bench']}.json`."
            )
    return dest


def harness_noise_floor(
    root: Path,
    *,
    rotations: int,
    seeds: int,
    k: int,
    night: int,
    log: Log = print,
    config: Path | None = None,
) -> dict[str, Any]:
    """A/A of the baseline harness on the MBPP+ pool: sigma_seed for the harness track's boundary."""
    import math

    # Whichever harness config is running, not `harness_night.yaml` unconditionally: this track can run more
    # than one bench, and its floor and its bench must be the pair `night_plan` checks for the model track.
    cfg = yaml.safe_load((Path(config) if config else root / "research" / "prereg" / "harness_night.yaml").read_text())
    # This study WRITES the floor, so it must not be refused by the staleness of the one already there.
    ctx = HarnessContext(root, cfg, night, log, measuring=True)
    w = LedgerWriter.open(root / "research" / "ledger.jsonl", "0.1.0")
    w.append(
        "audit",
        "kernel",
        {
            "kind": "study_start",
            "severity": "info",
            "study": "harness_noise_floor",
            "design": {"rotations": rotations, "seeds": seeds, "k": k, "model": cfg["model"], "bench": cfg["bench"]},
        },
        epoch=0,
        night=night,
    )
    values: dict[str, list[float]] = {}
    ref: float | None = None
    for r in range(rotations):
        rot = draw_rotation(
            ctx.pool_dir,
            night,
            f"{BASELINE_ID}-h{r}",
            read_secret(ctx.state),
            k=k,
            exposure_cap=int(cfg["evaluation"]["exposure_cap"]),
        )
        record_exposure(ctx.pool_dir, rot)
        for s in range(seeds):
            jd, res, meta = run_agent(ctx, ctx.baseline, rot, s, f"nf-r{r}-s{s}")
            if res.exit_code != 0 or meta is None:
                log(f"rotation {r} seed {s}: agent FAILED {res.stderr_tail[-300:]}")
                continue
            scores, sref, sres = score_agent(ctx, jd, rot)
            h = _hashes(ctx, jd, ctx.baseline)
            meta["items_sha256"] = h.items
            _, obs = admit_observation(
                w,
                expected=h,
                job_meta=with_unparsed(meta, sref),
                per_item_scores=scores,
                per_item_ref=str(sref),
                run_id=jd.name,
                candidate_id=BASELINE_ID,
                surface="H3.prompt",
                bucket=ctx.bucket,
                epoch=0,
                night=night,
                cycle=None,
                seed=s,
                rotation_id=rot.rotation_id,
                value_ref=ref,
                cost_gpu_h=res.wall_s / 3600,
                wall_s=res.wall_s,
                peak_gib=res.peak_gib_smi,
                isolation=ctx.state.isolation,
                stage="screen",
                extra={"study": "harness_noise_floor", "track": "harness", "rotation_index": r},
            )
            v = obs.payload["observed"]["value"]
            ref = v if ref is None else ref
            values.setdefault(rot.rotation_id, []).append(v)
            log(f"rotation {r} seed {s}: plus_pass={v:.4f} n={k} seq={obs.seq} wall={res.wall_s:.0f}s")
    allv = [x for vs in values.values() for x in vs]
    # A floor is the variance of a COMPLETE A/A set, and a partial one is not a conservative floor -- it is a
    # different number. Found by running it: three of nine runs OOM'd on the casehold-val floor because the
    # previous night had not released its GPU memory yet, all three of them rotation 0, and the study wrote a
    # floor from the surviving six without a word. `sigma_rot` is computed ACROSS rotations, so losing a whole
    # rotation left it with one degree of freedom; `n_runs: 6` in the JSON was technically honest and nothing
    # read it. Same shape as a scoring job that did not run becoming a rotation of zeros (ADR-0039).
    wanted = rotations * seeds
    if len(allv) < wanted:
        got = {rid: len(vs) for rid, vs in values.items()}
        raise RuntimeError(
            f"noise floor incomplete: {len(allv)} of {wanted} runs succeeded ({rotations} rotations x "
            f"{seeds} seeds), per rotation {got}. A floor measured on a subset is a different number, not a "
            f"conservative one, and a missing rotation leaves sigma_rot with almost no degrees of freedom. "
            f"The failures are above; fix them and re-measure rather than running a night on this."
        )
    within = [math.sqrt(sum((x - sum(vs) / len(vs)) ** 2 for x in vs) / (len(vs) - 1)) for vs in values.values() if len(vs) >= 2]
    sigma_seed = math.sqrt(sum(x * x for x in within) / len(within)) if within else 0.0
    means = [sum(vs) / len(vs) for vs in values.values()]
    sigma_rot = math.sqrt(sum((m - sum(means) / len(means)) ** 2 for m in means) / (len(means) - 1)) if len(means) > 1 else 0.0
    out = {
        "bench": cfg["bench"],
        "model": cfg["model"],
        "study": "harness_noise_floor",
        "n_runs": len(allv),
        "mean_plus_pass": sum(allv) / max(1, len(allv)),
        "sigma_seed": sigma_seed,
        "sigma_rot": sigma_rot,
        "k_items": k,
        # Which recipe this is the variance OF. A floor without it cannot be checked against the night that
        # reads it, and an unverifiable pairing is what ADR-0037 is about.
        "baseline_sha256": baseline_sha256(ctx.baseline),
        "baseline_recipe": str(cfg.get("baseline_recipe") or "harness_grammar.BASELINE"),
        "labels": "model-measured, screen tier, baseline harness A/A",
    }
    dest = floor_dest(root, cfg)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(out, indent=2, sort_keys=True) + "\n")
    w.append(
        "audit",
        "kernel",
        {"kind": "study_end", "severity": "info", "study": "harness_noise_floor", "summary": out},
        epoch=0,
        night=night,
    )
    return out


def load_seed_recipes(paths: Iterable[Path]) -> tuple[HarnessRecipe, ...]:
    """Operator-supplied harness recipes, parsed through the same grammar gate a proposer candidate must pass."""
    recipes: list[HarnessRecipe] = []
    for path in paths:
        parsed = parse_harness(json.loads(Path(path).read_text()))
        if isinstance(parsed, str):
            raise ValueError(f"{path}: {parsed}")
        recipes.append(parsed)
    return tuple(recipes)


def _admit_seed(
    w: LedgerWriter, night: int, cid: str, rec: HarnessRecipe, incumbent_id: str, bucket: dict[str, str], surface: str
) -> None:
    """The propose row a seed recipe needs to enter paired eval like any proposer candidate (never promoted here)."""
    key = json.dumps(rec.model_dump(exclude={"rationale"}), sort_keys=True)
    w.append(
        "propose",
        "human:operator",
        {
            "op": "harness",
            "source": "operator-seed",
            "rationale": rec.rationale,
            "recipe": rec.model_dump(),
            "strategy": rec.strategy,
            "edit_family": rec.execution_family,
            "vak": {"para": rec.rationale[:400], "pasyanti": key[:600]},
            "diff": {"sha256": hashlib.sha256(key.encode()).hexdigest()},
            "cost_estimate": {"gpu_h": rec.cost_est_gpu_h()},
            "lineage": [incumbent_id],
        },
        epoch=0,
        night=night,
        candidate_id=cid,
        surface=surface,
        bucket=bucket,
        provenance="agama",
    )


def run_harness_night(
    root: Path, *, night: int, k: int | None, budget_gpu_h: float | None, gguf: Path, log: Log = print,
    selection_policy: str | None = None,
    proposer_endpoint: str = "",
    seed_recipes: tuple[HarnessRecipe, ...] = (),
    config: Path | None = None,
) -> dict[str, Any]:
    # Whichever harness config is running, not `harness_night.yaml` unconditionally: this track can run more
    # than one bench, and its floor and its bench must be the pair `night_plan` checks for the model track.
    #
    # The PATH is kept, not just the parsed body. `deliberate` reads a night config of its own to learn which
    # bench to filter live candidates by, and defaults to `lora_night.yaml` when not told -- so night 21
    # proposed eight `apps` candidates, filtered them all against `gsm8k-trainD`, logged "no live candidates"
    # and closed having spent 0.00 of 2.0 GPU-hours. A night that does nothing and reports `status: closed`
    # is the worst shape a failure can take, so the path travels with the config.
    config_path = Path(config) if config else root / "research" / "prereg" / "harness_night.yaml"
    cfg = yaml.safe_load(config_path.read_text())
    ctx = HarnessContext(root, cfg, night, log)
    if ctx.variance is None:
        raise RuntimeError(f"run the harness noise floor first: {ctx.variance_file} does not exist")
    budget = float(budget_gpu_h if budget_gpu_h is not None else cfg["budget"]["night_gpu_h"])
    kk = int(k if k is not None else cfg["proposer"]["k_candidates"])
    policy = str(selection_policy or cfg.get("selection_policy", "efe"))
    ledger = root / "research" / "ledger.jsonl"
    w = LedgerWriter.open(ledger, "0.1.0")
    # incumbent = latest promoted harness on this surface whose promotion was not withdrawn, else baseline
    from pravrudhi_kernel.ledger.replay import withdrawn_observations
    from pravrudhi_kernel.ledger.verify import iter_events

    withdrawn = withdrawn_observations(ledger)
    # Promotions before 2026-09-11 carry a recipe without `strategy`/`execution_family`; the propose row of the
    # same candidate has both, so an old promotion is completed from it rather than silently skipped. A
    # promotion that still cannot be read is written down as such: a night that quietly measured against the
    # baseline after a promotion is the defect the review found in eleven consecutive `night_start` rows.
    families: dict[str, dict[str, str]] = {}
    load_failures: list[dict[str, Any]] = []
    for ev in iter_events(ledger):
        if ev.kind == "propose" and ev.payload.get("op") == "harness" and ev.candidate_id:
            fam = {"strategy": ev.payload.get("strategy"), "execution_family": ev.payload.get("edit_family")}
            if all(fam.values()):
                families[str(ev.candidate_id)] = {k: str(v) for k, v in fam.items()}
        if ev.kind == "promote" and ev.surface == "H3.prompt" and ev.payload.get("harness") and ev.seq not in withdrawn:
            raw = dict(ev.payload["harness"])
            inc_parsed = parse_harness(raw)
            if isinstance(inc_parsed, str) and str(ev.candidate_id) in families:
                inc_parsed = parse_harness({**families[str(ev.candidate_id)], **raw})
            if isinstance(inc_parsed, str):
                load_failures.append({"seq": ev.seq, "candidate_id": str(ev.candidate_id), "reason": inc_parsed})
            else:
                ctx.incumbent, ctx.incumbent_id = inc_parsed, str(ev.candidate_id)
    for failure in load_failures:
        w.append(
            "audit",
            "kernel",
            {"kind": "incumbent_load_failed", "severity": "warn", "track": "harness", **failure},
            epoch=0,
            night=night,
            provenance="pratyaksha",
        )
    w.append(
        "audit",
        "kernel",
        {
            "kind": "night_start",
            "severity": "info",
            "track": "harness",
            "selection_policy": policy,
            "budget_gpu_h": budget,
            "k": kk,
            "incumbent": ctx.incumbent_id,
            "prereg_sha256": {
                "harness_night": sha256_file(root / "research" / "prereg" / "harness_night.yaml"),
                # The floor this night actually read, by name: there is more than one now.
                "variance_harness": sha256_file(ctx.variance_file),
                "variance_harness_file": ctx.variance_file.name,
            },
        },
        epoch=0,
        night=night,
    )
    recipes: dict[str, HarnessRecipe] = {}
    for seed_rec in seed_recipes:
        cid = next_candidate_id(ledger)
        _admit_seed(w, night, cid, seed_rec, ctx.incumbent_id, ctx.bucket, "H3.prompt")
        recipes[cid] = seed_rec
    already = sum(1 for ev in iter_events(ledger) if ev.kind == "propose" and ev.night == night and ev.surface == "H3.prompt")
    remaining_k = kk - len(seed_recipes)
    if remaining_k > 0 and already < kk:
        endpoint = proposer_endpoint or str(cfg["proposer"].get("endpoint", ""))
        with proposer_client(
            gguf, ctx=int(cfg["proposer"]["max_tokens"]) * 2 + 8192, endpoint=endpoint, log=log
        ) as client:
            acc = propose_generic(
                root,
                w,
                client,
                night=night,
                k=remaining_k,
                model=str(cfg["model"]),
                bucket=ctx.bucket,
                prompts_dir=root / "harness" / "prompts",
                sealed_dir=root / ".pravrudhi" / "kernel" / "sealed" / "predictions",
                incumbent_id=ctx.incumbent_id,
                sigma_seed=ctx.variance.sigma_seed,
                temperature=float(cfg["proposer"]["temperature"]),
                max_tokens=int(cfg["proposer"]["max_tokens"]),
                rethink_m=int(cfg["rethink_m"]),
                log=log,
                grammar_doc=H_GRAMMAR_DOC,
                parse_fn=parse_harness,
                # The config's own prompt version, not `v1` unconditionally. `v1` describes MBPP+ to the
                # proposer -- docstrings, visible asserts, a ```python block -- so on a choice bench it
                # produced seven candidates reasoning about "passing visible tests" and "explicit code block
                # requirement", every one of them leaving `max_new_tokens` at 512, which is the single knob
                # measured to be worth 0.2951 here. The night ran green and explored the wrong space.
                prompt_file=f"harness_proposer/{cfg['proposer'].get('prompt_version', 'v1')}.md",
                surface="H3.prompt",
                op="harness",
                json_schema=harness_array_schema(remaining_k),
                extra_context="Incumbent harness (the reference every candidate is paired against):\n"
                + json.dumps(ctx.incumbent.harness_json(), indent=1, sort_keys=True),
            )
            recipes.update(dict(acc))
    outcomes: dict[str, str] = {}
    from pravrudhi.application.citta_view import build_citta

    for rnd in range(int(cfg.get("max_rounds", 4))):
        remaining = budget - ctx.spent_gpu_h
        if remaining <= 0.05:
            break
        try:
            order = deliberate(
                root,
                w,
                night=night,
                budget_gpu_h=remaining,
                sigma_seed=ctx.variance.sigma_seed,
                incumbent_id=ctx.incumbent_id,
                harness_hash=hashlib.sha256(json.dumps(ctx.incumbent.harness_json(), sort_keys=True).encode()).hexdigest(),
                model_hash="0" * 64,
                rng_seed=night * 100 + rnd,
                log=log,
                round_index=rnd,
                selection_policy=policy,
                surface="H3.prompt",
                target_model=str(cfg["model"]),
                night_config=config_path,
            )
        except DecorativeAbort as e:
            w.append(
                "audit",
                "kernel",
                {"kind": "night_end", "severity": "high", "reason": "decorative_controller", "track": "harness"},
                epoch=0,
                night=night,
            )
            return {"night": night, "status": "aborted", "reason": str(e)}
        if not order:
            break
        log(f"round {rnd + 1}: {len(order)} selected, {remaining:.2f} GPU-h remaining")
        _, meta = build_citta(ledger, root / ".pravrudhi" / "kernel" / "sealed" / "predictions", sigma2_eval=1e-4, tau0_2=0.01)
        for cid in order:
            if ctx.spent_gpu_h >= budget:
                outcomes[cid] = "skipped:budget"
                continue
            rec = recipes.get(cid)
            if rec is None:
                parsed = parse_harness(meta[cid]["recipe"] or {})
                if isinstance(parsed, str):
                    outcomes[cid] = "skipped:bad_recipe"
                    continue
                rec = parsed
                recipes[cid] = parsed
            try:
                outcomes[cid] = _execute_one(ctx, w, cid, rec, int(cfg["evaluation"]["k_items"]))
            except PoolExhausted as e:
                w.append(
                    "audit",
                    "kernel",
                    {"kind": "pool_exhausted", "severity": "high", "detail": str(e)},
                    epoch=0,
                    night=night,
                    candidate_id=cid,
                    surface="H3.prompt",
                )
                outcomes[cid] = "skipped:pool_exhausted"
                break
            except RuntimeError as e:
                outcomes[cid] = f"failed:{e}"
    sw, n, ci = strategy_switch_rate(ledger)
    w.append(
        "audit",
        "controller",
        {"kind": "strategy_switch_rate", "severity": "info", "switches": sw, "n": n, "wilson": list(ci)},
        epoch=0,
        night=night,
    )
    w.append(
        "audit",
        "kernel",
        {
            "kind": "night_end",
            "severity": "info",
            "track": "harness",
            "spent_gpu_h": ctx.spent_gpu_h,
            "budget_gpu_h": budget,
            "outcomes": outcomes,
            "incumbent": ctx.incumbent_id,
            "incumbent_harness": ctx.incumbent.harness_json(),
        },
        epoch=0,
        night=night,
    )
    log(f"harness night {night} closed: spent {ctx.spent_gpu_h:.2f}/{budget}; outcomes {outcomes}; incumbent {ctx.incumbent_id}")
    return {
        "night": night,
        "status": "closed",
        "spent_gpu_h": ctx.spent_gpu_h,
        "outcomes": outcomes,
        "incumbent": ctx.incumbent_id,
    }


def _execute_one(ctx: HarnessContext, w: LedgerWriter, cid: str, rec: HarnessRecipe, k: int) -> str:
    st = replay(ctx.root / "research" / "ledger.jsonl")
    xs_prev = list(st.candidates[cid].xs) if cid in st.candidates else []
    seed = len(xs_prev)
    rot = draw_rotation(
        ctx.pool_dir,
        ctx.night,
        f"{cid}-s{seed}",
        read_secret(ctx.state),
        k=k,
        exposure_cap=int(ctx.cfg["evaluation"]["exposure_cap"]),
    )
    record_exposure(ctx.pool_dir, rot)
    ijd, ires, imeta = run_agent(ctx, ctx.incumbent, rot, seed, f"inc-{cid}")
    cjd, cres, cmeta = run_agent(ctx, rec, rot, seed, f"cand-{cid}")
    if ires.exit_code != 0 or cres.exit_code != 0 or imeta is None or cmeta is None:
        w.append(
            "audit",
            "kernel",
            {"kind": "job_failed", "severity": "high", "stderr_tail": (cres.stderr_tail or ires.stderr_tail)[-800:]},
            epoch=0,
            night=ctx.night,
            candidate_id=cid,
            surface="H3.prompt",
        )
        raise RuntimeError("agent run failed")
    iscores, iref, _ = score_agent(ctx, ijd, rot)
    cscores, cref, csres = score_agent(ctx, cjd, rot)
    iv, cv = sum(iscores.values()) / len(iscores), sum(cscores.values()) / len(cscores)
    delta = cv - iv
    assert ctx.variance is not None
    br = sequential_boundary([*xs_prev, delta], ctx.variance)
    ih = _hashes(ctx, ijd, ctx.incumbent)
    imeta["items_sha256"] = ih.items
    admit_observation(
        w,
        expected=ih,
        job_meta=with_unparsed(imeta, iref),
        per_item_scores=iscores,
        per_item_ref=str(iref),
        run_id=ijd.name,
        candidate_id=ctx.incumbent_id,
        surface="H3.prompt",
        bucket=ctx.bucket,
        epoch=0,
        night=ctx.night,
        cycle=None,
        seed=seed,
        rotation_id=rot.rotation_id,
        value_ref=None,
        cost_gpu_h=ires.wall_s / 3600,
        wall_s=ires.wall_s,
        peak_gib=ires.peak_gib_smi,
        isolation=ctx.state.isolation,
        stage="screen",
        extra={"arm": "incumbent", "paired_with": cid, "track": "harness"},
    )
    sealed = ctx.sealed.get(cid)
    brier = None
    predicted = None
    if sealed:
        predicted = {"delta_in": sealed["delta_in"], "delta_out": None, "conf": sealed["conf"], "hash": sealed["hash"]}
        brier = (sealed["conf"] - (1.0 if (sealed["delta_in"] >= 0) == (delta >= 0) else 0.0)) ** 2
    admit_candidate(
        ctx,
        w,
        cid,
        rec,
        (cscores, cref, csres, cmeta, cjd),
        seed,
        rot.rotation_id,
        iv,
        br,
        {"predicted": predicted, "brier": brier, "wall_s_candidate": cres.wall_s, "harness": rec.harness_json()},
        incumbent_scores=iscores,
    )
    ctx.log(
        f"{cid}: seed {seed} incumbent={iv:.3f} candidate={cv:.3f} delta={delta:+.3f} "
        f"boundary={br.decision} (n={br.n}, E={br.e_value:.2f})"
    )
    if br.decision == "prune":
        w.append(
            "prune",
            "kernel",
            {
                "hetvabhasa": br.hetvabhasa or "asiddha",
                "reason": f"sequential boundary at n={br.n}",
                "stage": "screen",
                "boundary": "prune",
                "by": "sequential",
                "status": "pruned",
            },
            epoch=0,
            night=ctx.night,
            candidate_id=cid,
            surface="H3.prompt",
        )
        return "pruned"
    if br.decision == "confirm":
        pack = ctx.root / "research" / "inbox" / f"harness-night{ctx.night}" / cid
        pack.mkdir(parents=True, exist_ok=True)
        (pack / "README.md").write_text(
            f"# Harness promotion {cid}\n\n```json\n{json.dumps(rec.harness_json(), indent=2)}\n```\n"
        )
        w.append(
            "promote",
            "broker",
            {
                "tier": "T2",
                "from_worktree": str(pack),
                "merge_commit": hashlib.sha256(json.dumps(rec.harness_json(), sort_keys=True).encode()).hexdigest(),
                "tag": f"harness-night{ctx.night}-{cid}",
                "lineage": [ctx.incumbent_id],
                "tau_before": 0.5,
                "tau_after": 0.6,
                "inbox_pack": str(pack),
                "harness": rec.harness_json(),
                "regression": {"pass3": None, "wilson_lo": None, "flips": 0},
                "holm": {"m": 1, "rank": 1, "p_adj": None},
            },
            epoch=0,
            night=ctx.night,
            candidate_id=cid,
            surface="H3.prompt",
        )
        ctx.incumbent, ctx.incumbent_id = rec, cid
        dest = promoted_path(ctx.root, ctx.bench)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps(rec.harness_json(), indent=2, sort_keys=True) + "\n")
        ctx.log(f"{cid}: PROMOTED harness (T2); now the incumbent")
        return "promoted"
    return "continue"
