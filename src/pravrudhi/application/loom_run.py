"""Pure job planning and durable dispatch through NightContext.run_raw.

Costs and evidence are opaque host/kernel records, never estimates made by Loom.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from pravrudhi.application.loom_pipeline import Job, Pipeline, PipelineError, Stage, lora_recipe, lower


@dataclass(frozen=True)
class PlannedJob:
    job: Job
    files: tuple[tuple[str, str], ...] = ()
    # A reservation/quote supplied by the engine, not measured expenditure.
    spend: str = "unknown until kernel execution"


Planner = Callable[[Stage, Mapping[str, str]], PlannedJob]


def _sft(stage: Stage, inputs: Mapping[str, str]) -> PlannedJob:
    recipe = lora_recipe(stage)
    if recipe.strategy != "sft_rejection" or recipe.sft.init != "base":
        raise PipelineError("prepared SFT requires sft_rejection and init=base")
    return PlannedJob(
        Job("train_sft", ("--model-dir", "/model", "--seed", "0"),
            ((inputs["model"], "/model"), (inputs["corpus"], "/in/train.jsonl"))),
        (("recipe.json", json.dumps(recipe.model_dump(), sort_keys=True)),),
    )


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, allow_nan=False)


def dry_run(pipeline: Pipeline, *, planners: Mapping[str, Planner] | None = None) -> dict[str, Any]:
    """Return the complete launch manifest without filesystem access or preparation.

    Custom planners must be pure and produce existing engine job contracts. Output
    references use @stage syntax and are resolved only to recorded artifact paths.
    No default promotion planner exists: raw job success cannot authorize promotion.
    """
    canonical = lower(pipeline.program)
    if pipeline.resources != canonical.resources or sorted(pipeline.stages, key=lambda s: s.name) != sorted(
        canonical.stages, key=lambda s: s.name
    ):
        raise PipelineError("pipeline does not match its source tree")
    bindings = {"sft": _sft} if planners is None else dict(planners)
    values = {r.name: r.location for r in pipeline.resources}
    if any(v.startswith("@") for v in values.values()):
        raise PipelineError("resource locations may not start with reserved @")
    pending = list(pipeline.stages)
    rows = []
    names = {s.name for s in pending}
    while pending:
        ready = next((s for s in pending if all(ref in values for _, ref in s.inputs)), None)
        if ready is None:
            raise PipelineError("unresolved or cyclic stage dependencies")
        if ready.operation == "promote" or ready.operation not in bindings:
            raise PipelineError(f"engine has no job planner for {ready.operation!r}")
        inputs = {role: values[ref] for role, ref in ready.inputs}
        if bindings[ready.operation] is _sft and inputs["model"].startswith("@"):
            raise PipelineError("prepared SFT requires a snapshot; adapter continuation needs an engine planner")
        planned = bindings[ready.operation](ready, inputs)
        job = planned.job
        if not job.command or job.timeout_s <= 0:
            raise PipelineError("job requires a command and positive timeout")
        for path in [job.output, *(name for name, _ in planned.files)]:
            if not path or Path(path).is_absolute() or ".." in Path(path).parts:
                raise PipelineError("job files and output must be relative paths within the job directory")
        rows.append({"name": ready.name, "operation": ready.operation,
                     "dependencies": [ref for _, ref in ready.inputs if ref in names],
                     "inputs": inputs, **asdict(planned)})
        values[ready.name] = "@" + ready.name
        pending.remove(ready)
    manifest = {"version": 1, "resources": [asdict(r) for r in pipeline.resources], "stages": rows}
    # JSON normalization makes disk reload and in-memory comparison identical.
    # Named `normalised` rather than `canonical`, which is a function imported into this module: shadowing it
    # made the local a Pipeline as far as the type checker was concerned and hid a real name collision.
    normalised: dict[str, Any] = json.loads(_json(manifest))
    normalised["id"] = hashlib.sha256(_json(normalised).encode()).hexdigest()
    return normalised


def _save(path: Path, record: dict[str, Any]) -> None:
    import os
    import tempfile

    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name, dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(_json(record))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def run(pipeline: Pipeline, ctx: Any, record_path: Path, *,
        planners: Mapping[str, Planner] | None = None) -> dict[str, Any]:
    """Preflight all jobs, checkpoint each result, and resume completed stages.

    An interrupted in-flight job requires host reconciliation; it is never blindly
    relaunched. The record is orchestration state, not kernel evidence or a ledger.
    """
    # A function-level `import fcntl` survives module import and kills the CALL instead, so it stayed invisible
    # on Windows until somebody ran a pipeline there. Swept with `requests.py`'s module-level one after v0.1.7.
    from pravrudhi.application.portable_lock import LockUnavailable, exclusive_lock

    manifest = dry_run(pipeline, planners=planners)
    record_path = Path(record_path)
    record_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        lock_ctx = exclusive_lock(record_path.with_suffix(record_path.suffix + ".lock"), blocking=False)
        lock_ctx.__enter__()
    except LockUnavailable as exc:
        raise PipelineError("pipeline record is already in use") from exc
    try:
        record = json.loads(record_path.read_text()) if record_path.exists() else {
            "manifest": manifest, "stages": {}}
        if record.get("manifest") != manifest:
            raise PipelineError("resume manifest differs from the recorded program/jobs")
        states = record["stages"]
        values: dict[str, Any] = {r.name: r.location for r in pipeline.resources}
        # Validate all checkpoints before any job can launch.
        for row in manifest["stages"]:
            prior = states.get(row["name"], {})
            if prior.get("status") == "running":
                raise PipelineError(f"{row['name']}: interrupted job requires host reconciliation")
            if prior.get("status") == "complete":
                if not Path(prior["value"]["path"]).exists():
                    raise PipelineError(f"{row['name']}: recorded artifact is missing")
                if any(states.get(dep, {}).get("status") != "complete" for dep in row["dependencies"]):
                    raise PipelineError("checkpoint has incomplete dependencies")
        for row in manifest["stages"]:
            name = row["name"]
            if states.get(name, {}).get("status") == "complete":
                values[name] = states[name]["value"]
                continue
            jd = ctx.job_dir(name)
            job = row["job"]
            for filename, content in row["files"]:
                target = jd / "in" / filename
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content)
            def resolve(source: str) -> str:
                return values[source[1:]]["path"] if source.startswith("@") else source
            mounts = {resolve(source): target for source, target in job["mounts"]}
            states[name] = {"status": "running", "job_dir": str(jd)}
            _save(record_path, record)
            result, metadata = ctx.run_raw(job["command"], job["args"], jd, mounts, job["timeout_s"])
            artifact = jd / "out" / job["output"]
            if result.exit_code != 0 or metadata is None or not artifact.exists():
                states[name]["status"] = "failed"
                _save(record_path, record)
                raise PipelineError(f"{name}: engine job failed or output missing")
            value = {"path": str(artifact), "metadata": metadata}
            states[name].update(status="complete", value=value)
            _save(record_path, record)
            values[name] = value
        return values
    finally:
        # The lock was entered by hand (rather than via `with`) so that a LockUnavailable at ACQUISITION
        # becomes a PipelineError while the body's own exceptions keep their own meaning. This releases it on
        # every path out, `return values` included.
        lock_ctx.__exit__(None, None, None)
