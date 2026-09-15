"""Pure pipeline compilation and binding to the existing NightContext runner.

Resources and policy are supplied by the host. No scores, observations or promotion
rows are manufactured here. Unsupported engine capabilities fail before any job runs.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from pravrudhi.application.loom import Assign, Call, Decl, Ident, LoomProgram, NumberLit, StringLit
from pravrudhi.application.loom import lift as parse
from pravrudhi.targets.harness_grammar import HarnessRecipe
from pravrudhi.targets.lora_grammar import LoraRecipe


class PipelineError(ValueError):
    """A pipeline cannot be resolved or executed by the selected engine."""


@dataclass(frozen=True)
class Resource:
    name: str
    kind: str
    location: str


@dataclass(frozen=True)
class Stage:
    name: str
    operation: str
    inputs: tuple[tuple[str, str], ...]
    options: tuple[tuple[str, str | float | bool], ...]


@dataclass(frozen=True)
class Pipeline:
    program: LoomProgram
    resources: tuple[Resource, ...]
    stages: tuple[Stage, ...]
    source: str | None = None


# Roles are typed references, never implicit global model/corpus/benchmark names.
_ROLES = {
    "pretrain": {"model": "model", "corpus": "corpus"},
    "continue_pretrain": {"model": "model", "corpus": "corpus"},
    "sft": {"model": "model", "corpus": "corpus"},
    "distill": {"teacher": "model", "student": "model", "corpus": "corpus"},
    "evaluate": {"model": "model", "benchmark": "evalset"},
    "promote": {"model": "model", "evaluation": "evaluation"},
}

STAGE_EXECUTABLE = "executable"
"""A concrete engine binding ships in this module; `executable_bindings()` supplies it."""

STAGE_PENDING = "pending-engine-binding"
"""No concrete engine binding ships here; `execute` refuses this stage until a host supplies one."""

# The single source of truth for docs/LOOM.md's "Execution boundary": every grammar
# stage, including `promote` (a policy callback rather than an engine job), must be
# named here exactly once. `test_stage_executability_declares_every_loom_stage` pins
# this against the grammar's own stage names so the table cannot silently drift.
STAGE_EXECUTABILITY: Mapping[str, str] = {
    "pretrain": STAGE_PENDING,
    "continue_pretrain": STAGE_PENDING,
    "sft": STAGE_EXECUTABLE,
    # 2026-09-15, Track B T6 priority shift: the data-feasibility spike found 0/25k sources convert
    # deterministically to full IR, so supervision must be teacher-authored-then-checker-filtered before any
    # SFT run consumes it -- distill unblocks P2, ahead of sft in practice even though sft bound first.
    "distill": STAGE_EXECUTABLE,
    "evaluate": STAGE_EXECUTABLE,
    "promote": STAGE_PENDING,
}

assert set(STAGE_EXECUTABILITY) == set(_ROLES), "STAGE_EXECUTABILITY must name exactly the grammar's stages"


def lower(program: str | LoomProgram) -> Pipeline:
    """Compile declarations and stages; preserve the original tree and optional source.

    Every reference must name an earlier declaration. Outputs are immutable names,
    making dependencies (including the exact model evaluated for promotion) explicit.
    """
    source = program if isinstance(program, str) else None
    tree = parse(program) if isinstance(program, str) else program
    symbols: dict[str, str] = {}
    resources: list[Resource] = []
    stages: list[Stage] = []
    evaluations: dict[str, str] = {}
    for stmt in tree.stmts:
        if not isinstance(stmt, (Decl, Assign)):
            raise PipelineError(f"unsupported statement at {stmt.line}:{stmt.col}")
        if stmt.name in symbols:
            raise PipelineError(f"duplicate name {stmt.name!r}; use a new name for each output")
        call = stmt.value
        if not isinstance(call, Call) or not isinstance(call.callee, Ident):
            raise PipelineError(f"{stmt.name}: expected a pipeline call")
        op = call.callee.name
        if op == "load":
            if (not isinstance(stmt, Decl) or stmt.type_ not in {"model", "corpus", "evalset"}
                    or len(call.args) != 1 or call.args[0].name is not None
                    or not isinstance(call.args[0].value, StringLit) or call.block is not None):
                raise PipelineError("load requires a model/corpus/evalset declaration and one literal location")
            resources.append(Resource(stmt.name, stmt.type_, call.args[0].value.value))
            symbols[stmt.name] = stmt.type_
            continue
        if op not in _ROLES:
            raise PipelineError(f"unsupported operation {op!r}")
        roles = _ROLES[op]
        inputs: dict[str, str] = {}
        for arg in call.args:
            if arg.name not in roles or arg.name in inputs or not isinstance(arg.value, Ident):
                raise PipelineError(f"{stmt.name}: expected unique named roles {tuple(roles)}")
            ref = arg.value.name
            if symbols.get(ref) != roles[arg.name]:
                raise PipelineError(f"{stmt.name}: {arg.name} requires an earlier {roles[arg.name]}, got {ref!r}")
            inputs[arg.name] = ref
        if inputs.keys() != roles.keys():
            raise PipelineError(f"{stmt.name}: required roles {tuple(roles)}")
        options: dict[str, str | float | bool] = {}
        for option in call.block.assigns if call.block else ():
            if option.name in options:
                raise PipelineError(f"duplicate option {option.name!r}")
            value = option.value
            if isinstance(value, (StringLit, NumberLit)):
                options[option.name] = value.value
            elif isinstance(value, Ident) and value.name in {"true", "false"}:
                options[option.name] = value.name == "true"
            else:
                raise PipelineError(f"{stmt.name}: options must be literal values")
        kind = "evaluation" if op == "evaluate" else "promotion" if op == "promote" else "model"
        if isinstance(stmt, Decl) and stmt.type_ != kind:
            raise PipelineError(f"{stmt.name}: {op} produces {kind}")
        if op == "evaluate":
            evaluations[stmt.name] = inputs["model"]
        if op == "promote" and evaluations[inputs["evaluation"]] != inputs["model"]:
            raise PipelineError("promotion must refer to an evaluation of the same model")
        symbols[stmt.name] = kind
        stages.append(Stage(stmt.name, op, tuple(inputs.items()), tuple(options.items())))
    return Pipeline(tree, tuple(resources), tuple(stages), source)


def lift(pipeline: Pipeline) -> LoomProgram:
    """Return the exact original AST, including positions and numeric spellings."""
    return pipeline.program


@dataclass(frozen=True)
class Job:
    """Arguments for NightContext.run_raw, whose implementation creates JobSpec.

    Input materialization belongs to the engine binding, as in execute.train.
    Output paths are supplied by the binding, not inferred from job metadata.
    """
    command: str
    args: tuple[str, ...] = ()
    mounts: tuple[tuple[str, str], ...] = ()
    timeout_s: int = 5400
    output: str = "adapter"


@dataclass(frozen=True)
class Binding:
    """A host capability: validate without I/O, then prepare existing engine jobs.

    Each binding prepares one job. Distillation preparation must use the engine
    sample verification path; Loom never filters or scores teacher output.
    """
    validate: Callable[[Stage], None]
    prepare: Callable[[Stage, Mapping[str, Any], Any], Job]


def lora_recipe(stage: Stage) -> LoraRecipe:
    """Validate explicitly prefixed recipe options using the existing grammar.

    Model roles stay outside the recipe: the bound engine selects their snapshots.
    Unsupported teachers/templates still fail the existing grammar validation.
    """
    obj: dict[str, Any] = {"strategy": "sft_rejection", "execution_family": "adapter"}
    for name, value in stage.options:
        if name.startswith(("sft_", "lora_", "grpo_")):
            group, field = name.split("_", 1)
            obj.setdefault(group, {})[field] = value
        else:
            obj[name] = value
    return LoraRecipe.model_validate(obj)


def harness_recipe(stage: Stage) -> HarnessRecipe:
    return HarnessRecipe.model_validate(dict(stage.options))


def execute(pipeline: Pipeline, ctx: Any, bindings: Mapping[str, Binding], *,
            promote: Callable[[Stage, Mapping[str, Any]], Any] | None = None) -> dict[str, Any]:
    """Preflight every stage, then dispatch through the engine's existing runner.

    The host promotion callback must use kernel-admitted evidence and its normal
    policy. Job metadata is returned opaquely; successful exit is not promotion.
    No jobs are launched by compilation or lifting.
    """
    # Reject a manually modified IR before allowing side effects.
    if lower(pipeline.program) != Pipeline(pipeline.program, pipeline.resources, pipeline.stages):
        raise PipelineError("pipeline does not match its source tree")
    for stage in pipeline.stages:
        if stage.operation == "promote":
            if promote is None:
                raise PipelineError("promotion requires the engine evidence/policy handler")
            if stage.options:
                raise PipelineError("promotion options are not supported by this dispatcher")
        else:
            if stage.operation not in bindings:
                raise PipelineError(f"engine has no binding for {stage.operation!r}")
            bindings[stage.operation].validate(stage)
    values: dict[str, Any] = {r.name: r.location for r in pipeline.resources}
    for stage in pipeline.stages:
        inputs = {role: values[name] for role, name in stage.inputs}
        if stage.operation == "promote":
            assert promote is not None
            values[stage.name] = promote(stage, inputs)
            continue
        jd = ctx.job_dir(stage.name)
        job = bindings[stage.operation].prepare(stage, inputs, jd)
        result, meta = ctx.run_raw(job.command, list(job.args), jd,
                                   dict(job.mounts), job.timeout_s)
        if result.exit_code != 0 or meta is None:
            raise PipelineError(f"{stage.name}: engine job failed")
        values[stage.name] = {"path": jd / "out" / job.output, "metadata": meta}
    return values


def sft_binding() -> Binding:
    """Bind prepared supervised JSONL to execute.py's train_sft job contract.

    Unlike execute.train's rejection-sampling convenience function, this accepts
    an already prepared corpus. Teacher generation/verification needs a separate
    engine binding; distill is deliberately not silently treated as ordinary SFT.
    """
    import json
    from pathlib import Path

    def validate(stage: Stage) -> None:
        if stage.operation != "sft":
            raise PipelineError("the train_sft binding supports sft only")
        recipe = lora_recipe(stage)
        if recipe.strategy != "sft_rejection" or recipe.sft.init != "base":
            raise PipelineError("prepared SFT requires sft_rejection with init=base; lineage supplies adapters")

    def prepare(stage: Stage, inputs: Mapping[str, Any], jd: Any) -> Job:
        recipe = lora_recipe(stage)
        (jd / "in" / "recipe.json").write_text(json.dumps(recipe.model_dump(), sort_keys=True))
        model = inputs["model"]
        if not isinstance(model, str):
            raise PipelineError("prepared SFT requires a model snapshot; bind adapter continuation explicitly")
        return Job("train_sft", ("--model-dir", "/model", "--seed", "0"),
                   ((str(Path(model)), "/model"),
                    (str(Path(inputs["corpus"])), "/in/train.jsonl")))

    return Binding(validate, prepare)


def distill_binding() -> Binding:
    """Bind distill to the engine's teacher-sampling-and-verify job command.

    2026-09-15, Track B T6 priority shift (data-feasibility spike: 0/25k sources convert deterministically
    to full IR): P2 needs teacher-authored traces, filtered through Track A's checker (Lean/Z3), before any
    SFT run can consume them. This binding is at the same abstraction level as `sft_binding`'s own binding to
    `train_sft`: it compiles and validates the stage and prepares the job's contract (which snapshots, which
    Track A contract to filter through, how many accepted traces are wanted); the "distill" job command
    itself performs the sampling and the checker-filtering, inside the sandbox, exactly as `train_sft`
    performs training -- Loom never filters or scores teacher output itself (see `sft_binding`'s own
    docstring), and this binding does not change that.
    """
    from pathlib import Path

    def validate(stage: Stage) -> None:
        if stage.operation != "distill":
            raise PipelineError("the distill binding supports distill only")
        options = dict(stage.options)
        contract_id = options.get("checker_contract_id")
        if not isinstance(contract_id, str) or not contract_id.strip():
            raise PipelineError(
                "distill requires a checker_contract_id option: the Track A contract "
                "(nyaya_lean.check) every teacher trace is filtered through before acceptance"
            )
        min_accepted = options.get("min_accepted_traces")
        if min_accepted is not None and (
            not isinstance(min_accepted, (int, float)) or isinstance(min_accepted, bool) or min_accepted <= 0
        ):
            raise PipelineError("distill's min_accepted_traces, if given, must be a positive number")

    def prepare(stage: Stage, inputs: Mapping[str, Any], jd: Any) -> Job:
        options = dict(stage.options)
        teacher, student = inputs["teacher"], inputs["student"]
        if not isinstance(teacher, str) or not isinstance(student, str):
            raise PipelineError(
                "distill requires resolved teacher/student model snapshots (plain paths); a prior stage's "
                "unresolved job-result dict cannot be mounted directly -- bind adapter continuation explicitly"
            )
        args: list[str] = ["--checker-contract-id", str(options["checker_contract_id"])]
        if "min_accepted_traces" in options:
            n = options["min_accepted_traces"]
            args += ["--min-accepted-traces", str(int(n) if isinstance(n, float) and n.is_integer() else n)]
        return Job(
            "distill", tuple(args),
            (
                (str(Path(teacher)), "/teacher"),
                (str(Path(student)), "/student"),
                (str(Path(inputs["corpus"])), "/in/prompts.jsonl"),
            ),
            output="accepted.jsonl",
        )

    return Binding(validate, prepare)


def evaluate_binding() -> Binding:
    """Bind evaluate to `ext_eval.sh`/lm-eval on an HF snapshot (T6,
    docs/reviews/track-b-directive-2026-09-15.md §10). `harness_recipe` already validates an evaluate
    stage's options against the existing grammar (sampling/retry/system-prompt policy) -- this binding
    reuses that validation rather than duplicating it, exactly as `lora_recipe` is reused for `sft`."""
    import json
    from pathlib import Path

    from pydantic import ValidationError

    def validate(stage: Stage) -> None:
        if stage.operation != "evaluate":
            raise PipelineError("the evaluate binding supports evaluate only")
        try:
            harness_recipe(stage)
        except ValidationError as e:
            raise PipelineError(f"evaluate: invalid harness recipe: {e}") from e

    def prepare(stage: Stage, inputs: Mapping[str, Any], jd: Any) -> Job:
        recipe = harness_recipe(stage)
        (jd / "in" / "recipe.json").write_text(json.dumps(recipe.model_dump(), sort_keys=True))
        model, benchmark = inputs["model"], inputs["benchmark"]
        if not isinstance(model, str):
            raise PipelineError("evaluate requires a resolved model snapshot; bind adapter continuation explicitly")
        if not isinstance(benchmark, str):
            raise PipelineError("evaluate requires a resolved benchmark/evalset path")
        return Job(
            "evaluate", ("--model-dir", "/model", "--benchmark-dir", "/benchmark"),
            ((str(Path(model)), "/model"), (str(Path(benchmark)), "/benchmark")),
            output="results.json",
        )

    return Binding(validate, prepare)


def executable_bindings() -> dict[str, Binding]:
    """The complete registry of concrete engine bindings this module ships.

    Its keys are exactly the stages marked `STAGE_EXECUTABLE` in `STAGE_EXECUTABILITY`, with one deliberate
    exception: `promote` is never a key here even if marked executable, because `execute()` never looks it
    up in `bindings` at all -- promotion is a policy callback (`execute`'s own `promote=` parameter), not a
    prepared `Job`, so there is no `Binding` shape for it to take.
    Stages marked `STAGE_PENDING` are deliberately absent: `execute` must still refuse
    them at preflight rather than have a host binding silently stand in for one.
    """
    return {"sft": sft_binding(), "distill": distill_binding(), "evaluate": evaluate_binding()}
