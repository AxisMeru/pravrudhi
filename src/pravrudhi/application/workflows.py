"""A wave used to be a Python script someone wrote by hand for one occasion, then threw away. Nothing let an
operator declare a repeatable, multi-step piece of work, run it by name, watch it, or hand it to someone else.

A workflow is that declaration: YAML naming an ordered set of steps, each one the same `TaskSpec` shape
`application/delegate.py` already dispatches safely, with a `needs` list between steps and an optional `when`
expression that can look at the run's inputs and at what earlier steps did. `plan` turns the declaration into
waves using `application/swarm.py`'s own conflict detection, so two steps that could touch the same file are
never scheduled together even if nothing in `needs` says they must be ordered. `run` walks those waves, skips or
blocks a step for a recorded reason instead of silently, and records what happened.

Workflows are not self-build tasks: `application/selfbuild.py` refuses `pravrudhi_kernel/`, `research/`,
`gates/` and `.pravrudhi/` because a self-build task would otherwise grade itself. A workflow's declared paths
carry no such exemption either -- `delegate.dispatch` refuses those paths for every dispatched task regardless of
what any `TaskSpec` declares -- so this module adds no new door into them.
"""

from __future__ import annotations

import ast
import json
import os
import re
import string
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import yaml

from pravrudhi.application import swarm
from pravrudhi.application.delegate import TaskSpec, Verdict
from pravrudhi.application.sandbox_policy import apply_policy, policy_for
from pravrudhi.application.swarm import SwarmTask

PACKAGED_DIR = Path(__file__).resolve().parents[1] / "assets" / "workflows"

_FORMATTER = string.Formatter()
_FIELD_BASE = re.compile(r"[.\[]")


class WorkflowError(ValueError):
    """A workflow this module refuses outright: a cycle in `needs`, a step referencing an input nothing
    declared, an allowed path that escapes the workspace, or a step with no validate command."""


@dataclass(frozen=True)
class WorkflowInput:
    name: str
    required: bool = False
    default: Any = None
    has_default: bool = False


@dataclass(frozen=True)
class WorkflowStep:
    id: str
    title: str
    brief: str
    allowed_paths: tuple[str, ...] = ()
    validate: str = ""
    tier: str = "standard"
    policy: str | None = None
    needs: tuple[str, ...] = ()
    when: str | None = None
    continue_on_failure: bool = False


@dataclass(frozen=True)
class Workflow:
    id: str
    title: str
    description: str
    inputs: tuple[WorkflowInput, ...]
    steps: tuple[WorkflowStep, ...]
    source: Path | None = None

    def input_names(self) -> tuple[str, ...]:
        return tuple(i.name for i in self.inputs)

    def step_ids(self) -> tuple[str, ...]:
        return tuple(s.id for s in self.steps)


@dataclass(frozen=True)
class PlannedStep:
    """A `WorkflowStep` with every `{input}` placeholder in its text fields filled in for one run."""

    id: str
    title: str
    brief: str
    allowed_paths: tuple[str, ...]
    validate: str
    tier: str
    policy: str | None
    needs: tuple[str, ...]
    when: str | None
    continue_on_failure: bool


@dataclass(frozen=True)
class StepResult:
    id: str
    state: Literal["accepted", "rejected", "skipped", "blocked"]
    reason: str = ""
    agent: str = ""
    files: tuple[str, ...] = ()
    wall_s: float = 0.0


@dataclass(frozen=True)
class WorkflowRun:
    id: str
    workflow_id: str
    inputs: dict[str, Any]
    steps: tuple[StepResult, ...]
    started: str
    ended: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "workflow_id": self.workflow_id,
            "inputs": self.inputs,
            "started": self.started,
            "ended": self.ended,
            "steps": [
                {
                    "id": s.id, "state": s.state, "reason": s.reason, "agent": s.agent,
                    "files": list(s.files), "wall_s": round(s.wall_s, 1),
                }
                for s in self.steps
            ],
        }


def _parse_input(raw: dict[str, Any]) -> WorkflowInput:
    return WorkflowInput(
        name=str(raw["name"]),
        required=bool(raw.get("required", False)),
        default=raw.get("default"),
        has_default="default" in raw,
    )


def _parse_step(raw: dict[str, Any]) -> WorkflowStep:
    step_id = str(raw["id"])
    return WorkflowStep(
        id=step_id,
        title=str(raw.get("title", step_id)),
        brief=str(raw.get("brief", "")),
        allowed_paths=tuple(str(p) for p in raw.get("allowed_paths", ())),
        validate=str(raw.get("validate", "")),
        tier=str(raw.get("tier", "standard")),
        policy=raw.get("policy"),
        needs=tuple(str(n) for n in raw.get("needs", ())),
        when=raw.get("when"),
        continue_on_failure=bool(raw.get("continue_on_failure", False)),
    )


def parse(raw: dict[str, Any], *, source: Path | None = None) -> Workflow:
    return Workflow(
        id=str(raw["id"]),
        title=str(raw.get("title", raw["id"])),
        description=str(raw.get("description", "")),
        inputs=tuple(_parse_input(i) for i in raw.get("inputs", ())),
        steps=tuple(_parse_step(s) for s in raw.get("steps", ())),
        source=source,
    )


def _read_file(path: Path) -> Workflow:
    raw = yaml.safe_load(path.read_text())
    return parse(raw, source=path)


def load(root: Path) -> list[Workflow]:
    """Every workflow this workspace knows: the packaged examples, then `<root>/workflows/*.yaml`, an operator's
    own definition of the same id replacing the packaged one."""
    by_id: dict[str, Workflow] = {}
    for path in sorted(PACKAGED_DIR.glob("*.yaml")):
        wf = _read_file(path)
        by_id[wf.id] = wf
    user_dir = Path(root) / "workflows"
    if user_dir.is_dir():
        for path in sorted(user_dir.glob("*.yaml")):
            wf = _read_file(path)
            by_id[wf.id] = wf
    return sorted(by_id.values(), key=lambda w: w.id)


def get(root: Path, workflow_id: str) -> Workflow | None:
    for wf in load(root):
        if wf.id == workflow_id:
            return wf
    return None


def _cycle(wf: Workflow) -> list[str] | None:
    """The first cycle found in `needs`, as an ordered list of step ids, or `None` if the graph is acyclic."""
    ids = set(wf.step_ids())
    graph = {s.id: [n for n in s.needs if n in ids] for s in wf.steps}
    color: dict[str, int] = {}
    path: list[str] = []

    def visit(node: str) -> list[str] | None:
        color[node] = 1
        path.append(node)
        for nxt in graph.get(node, ()):
            state = color.get(nxt, 0)
            if state == 1:
                i = path.index(nxt)
                return [*path[i:], nxt]
            if state == 0:
                found = visit(nxt)
                if found is not None:
                    return found
        path.pop()
        color[node] = 2
        return None

    for node in graph:
        if color.get(node, 0) == 0:
            found = visit(node)
            if found is not None:
                return found
    return None


def _escapes_workspace(path: str) -> bool:
    """Whether a declared path could resolve outside the workspace root, mirroring `dispatchboard._escapes`."""
    if not path or path.startswith("/") or path.startswith("~"):
        return True
    normalised = os.path.normpath(path)
    return normalised == ".." or normalised.startswith(f"..{os.sep}")


def _placeholders(text: str) -> set[str]:
    names: set[str] = set()
    for _, field_name, _, _ in _FORMATTER.parse(text):
        if field_name:
            base = _FIELD_BASE.split(field_name, 1)[0]
            if base:
                names.add(base)
    return names


# `when` is evaluated by walking this whitelist, never by `eval`: a workflow's `when` is YAML an operator wrote,
# but there is no reason a boolean condition over inputs and prior step outcomes needs arbitrary code execution.
_ALLOWED_WHEN_NODES: tuple[type[ast.AST], ...] = (
    ast.Expression, ast.BoolOp, ast.UnaryOp, ast.Compare, ast.Name, ast.Load, ast.Attribute, ast.Subscript,
    ast.Constant, ast.And, ast.Or, ast.Not, ast.Eq, ast.NotEq, ast.Is, ast.IsNot, ast.In, ast.NotIn, ast.Lt,
    ast.LtE, ast.Gt, ast.GtE,
)

_COMPARATORS: dict[type, Callable[[Any, Any], bool]] = {
    ast.Eq: lambda a, b: bool(a == b),
    ast.NotEq: lambda a, b: bool(a != b),
    ast.Is: lambda a, b: a is b,
    ast.IsNot: lambda a, b: a is not b,
    ast.In: lambda a, b: a in b,
    ast.NotIn: lambda a, b: a not in b,
    ast.Lt: lambda a, b: bool(a < b),
    ast.LtE: lambda a, b: bool(a <= b),
    ast.Gt: lambda a, b: bool(a > b),
    ast.GtE: lambda a, b: bool(a >= b),
}


def _parse_when(expr: str, step_id: str) -> ast.Expression:
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as e:
        raise WorkflowError(f"step {step_id!r}: when expression {expr!r} is not valid: {e}") from e
    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED_WHEN_NODES):
            raise WorkflowError(
                f"step {step_id!r}: when expression {expr!r} uses {type(node).__name__}, which is not permitted"
            )
    return tree


def _when_identifiers(expr: str, step_id: str) -> set[str]:
    tree = _parse_when(expr, step_id)
    return {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}


def _eval_when_node(node: ast.expr, namespace: dict[str, Any]) -> Any:
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        if node.id not in namespace:
            raise WorkflowError(f"when expression uses undefined name {node.id!r}")
        return namespace[node.id]
    if isinstance(node, ast.Attribute):
        return getattr(_eval_when_node(node.value, namespace), node.attr)
    if isinstance(node, ast.Subscript):
        return _eval_when_node(node.value, namespace)[_eval_when_node(node.slice, namespace)]
    if isinstance(node, ast.UnaryOp):  # only ast.Not survives the whitelist above
        return not _eval_when_node(node.operand, namespace)
    if isinstance(node, ast.BoolOp):
        values = [_eval_when_node(v, namespace) for v in node.values]
        return all(values) if isinstance(node.op, ast.And) else any(values)
    if isinstance(node, ast.Compare):
        left = _eval_when_node(node.left, namespace)
        for op, comparator in zip(node.ops, node.comparators, strict=True):
            right = _eval_when_node(comparator, namespace)
            if not _COMPARATORS[type(op)](left, right):
                return False
            left = right
        return True
    raise WorkflowError(f"when expression uses an unsupported construct: {type(node).__name__}")


def _format_fields(step: WorkflowStep) -> list[str]:
    return [step.title, step.brief, step.validate, *step.allowed_paths]


def validate(wf: Workflow) -> None:
    """Refuse a workflow that could never run safely: a duplicate or unknown step id, a cycle in `needs`, a step
    referencing an input nothing declared, an allowed path that escapes the workspace, or a step with no
    validate command. Each refusal names the offending step(s) so the operator can fix the YAML directly."""
    ids = wf.step_ids()
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        raise WorkflowError(f"workflow {wf.id!r} declares duplicate step id(s): {', '.join(dupes)}")

    id_set = set(ids)
    for step in wf.steps:
        unknown = sorted(n for n in step.needs if n not in id_set)
        if unknown:
            raise WorkflowError(f"workflow {wf.id!r} step {step.id!r} needs unknown step(s): {', '.join(unknown)}")

    cyc = _cycle(wf)
    if cyc is not None:
        raise WorkflowError(f"workflow {wf.id!r} has a cycle in needs: {' -> '.join(cyc)}")

    declared = set(wf.input_names())
    for step in wf.steps:
        used: set[str] = set()
        for text in _format_fields(step):
            used |= _placeholders(text)
        if step.when:
            used |= _when_identifiers(step.when, step.id) - {"steps"}
        undeclared = sorted(used - declared)
        if undeclared:
            raise WorkflowError(
                f"workflow {wf.id!r} step {step.id!r} references undeclared input(s): {', '.join(undeclared)}"
            )
        if not step.validate.strip():
            raise WorkflowError(f"workflow {wf.id!r} step {step.id!r} has no validate command")
        escaping = sorted(p for p in step.allowed_paths if _escapes_workspace(p))
        if escaping:
            raise WorkflowError(
                f"workflow {wf.id!r} step {step.id!r} allowed_paths escape the workspace: {', '.join(escaping)}"
            )


def resolve_inputs(wf: Workflow, provided: dict[str, Any]) -> dict[str, Any]:
    """`provided`, filled out with declared defaults, refusing a missing required input or an undeclared one."""
    extra = sorted(set(provided) - set(wf.input_names()))
    if extra:
        raise WorkflowError(f"workflow {wf.id!r} was given undeclared input(s): {', '.join(extra)}")
    out: dict[str, Any] = {}
    missing: list[str] = []
    for inp in wf.inputs:
        if inp.name in provided:
            out[inp.name] = provided[inp.name]
        elif inp.has_default:
            out[inp.name] = inp.default
        elif inp.required:
            missing.append(inp.name)
        else:
            out[inp.name] = None
    if missing:
        raise WorkflowError(f"workflow {wf.id!r} is missing required input(s): {', '.join(missing)}")
    return out


def _render(text: str, inputs: dict[str, Any]) -> str:
    return text.format(**inputs)


def _render_step(step: WorkflowStep, inputs: dict[str, Any]) -> PlannedStep:
    allowed = tuple(_render(p, inputs) for p in step.allowed_paths)
    escaping = sorted(p for p in allowed if _escapes_workspace(p))
    if escaping:
        raise WorkflowError(
            f"step {step.id!r} allowed_paths escape the workspace once inputs are applied: {', '.join(escaping)}"
        )
    return PlannedStep(
        id=step.id,
        title=_render(step.title, inputs),
        brief=_render(step.brief, inputs),
        allowed_paths=allowed,
        validate=_render(step.validate, inputs),
        tier=step.tier,
        policy=step.policy,
        needs=step.needs,
        when=step.when,
        continue_on_failure=step.continue_on_failure,
    )


def _bare_task(step: PlannedStep) -> SwarmTask:
    """The step as a `SwarmTask`, for conflict grouping only; no sandbox policy narrowing applied here."""
    spec = TaskSpec(task_id=step.id, prompt=step.brief, allowed_paths=step.allowed_paths, validate=step.validate)
    return SwarmTask(spec, tier=step.tier, why=step.title)


def _task_for_dispatch(step: PlannedStep) -> SwarmTask:
    task = _bare_task(step)
    if step.policy:
        spec = apply_policy(task.spec, policy_for(step.policy))
        return SwarmTask(spec, tier=step.tier, why=step.title)
    return task


def plan(wf: Workflow, inputs: dict[str, Any]) -> list[list[PlannedStep]]:
    """The workflow's steps as ordered waves: a step never appears in the same or an earlier wave than something
    it `needs`, and `swarm.plan`'s conflict detection further splits any wave whose steps could touch the same
    file, so two such steps never run together even without a declared `needs` between them."""
    validate(wf)
    resolved = resolve_inputs(wf, inputs)
    rendered = {s.id: _render_step(s, resolved) for s in wf.steps}

    remaining = set(rendered)
    done: set[str] = set()
    layers: list[list[str]] = []
    while remaining:
        ready = [sid for sid in remaining if all(n in done for n in rendered[sid].needs)]
        if not ready:  # unreachable once validate() has passed: it already refuses any real cycle
            raise WorkflowError(f"workflow {wf.id!r}: no progress can be made on {sorted(remaining)}")
        layers.append(ready)
        done |= set(ready)
        remaining -= set(ready)

    waves: list[list[PlannedStep]] = []
    for layer in layers:
        layer_steps = {sid: rendered[sid] for sid in layer}
        tasks = [_bare_task(step) for step in layer_steps.values()]
        sub_waves, _conflicts = swarm.plan(tasks)
        for sub in sub_waves:
            waves.append([layer_steps[t.spec.task_id] for t in sub])
    return waves


class _StepView:
    """What a `when` expression sees of an earlier step through `steps['id']`."""

    def __init__(self, result: StepResult) -> None:
        self.state = result.state
        self.accepted = result.state == "accepted"
        self.rejected = result.state == "rejected"
        self.skipped = result.state == "skipped"
        self.blocked = result.state == "blocked"
        self.reason = result.reason


def _eval_when(expr: str, inputs: dict[str, Any], results: dict[str, StepResult], step_id: str) -> bool:
    tree = _parse_when(expr, step_id)
    namespace: dict[str, Any] = dict(inputs)
    namespace["steps"] = {sid: _StepView(r) for sid, r in results.items()}
    return bool(_eval_when_node(tree.body, namespace))


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def runs_dir(root: Path) -> Path:
    return Path(root) / ".pravrudhi" / "workflows" / "runs"


def _record(root: Path, run: WorkflowRun) -> Path:
    d = runs_dir(root)
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{run.id}.json"
    p.write_text(json.dumps(run.to_dict(), indent=2, sort_keys=True))
    return p


def _need_failed(need: str, results: dict[str, StepResult]) -> bool:
    r = results.get(need)
    return r is not None and r.state in ("rejected", "blocked")


def run(
    root: Path,
    wf: Workflow,
    inputs: dict[str, Any],
    dispatch: Callable[[list[SwarmTask]], list[Verdict]],
) -> WorkflowRun:
    """Run every wave in order, evaluating each step's `needs` and `when` against the inputs and what earlier
    steps did. A step whose predecessor failed is blocked, not run, unless it declares `continue_on_failure`; a
    step whose `when` is false is skipped. Both are recorded with a reason rather than silently. Every run is
    written to `.pravrudhi/workflows/runs/<run id>.json`."""
    waves = plan(wf, inputs)
    resolved = resolve_inputs(wf, inputs)
    results: dict[str, StepResult] = {}
    started = _now()

    for wave in waves:
        runnable: list[PlannedStep] = []
        for step in wave:
            blocking = next((n for n in step.needs if _need_failed(n, results)), None)
            if blocking is not None and not step.continue_on_failure:
                results[step.id] = StepResult(step.id, "blocked", reason=f"predecessor {blocking!r} did not succeed")
                continue
            if step.when:
                try:
                    ok = _eval_when(step.when, resolved, results, step.id)
                except Exception as e:  # a malformed `when` must not silently run the step or crash the wave
                    results[step.id] = StepResult(step.id, "blocked", reason=f"when expression failed: {e}")
                    continue
                if not ok:
                    results[step.id] = StepResult(step.id, "skipped", reason=f"when {step.when!r} was false")
                    continue
            runnable.append(step)
        if not runnable:
            continue
        verdicts = {v.task_id: v for v in dispatch([_task_for_dispatch(s) for s in runnable])}
        for step in runnable:
            v = verdicts.get(step.id)
            if v is None:
                results[step.id] = StepResult(step.id, "rejected", reason="the dispatcher returned no result")
                continue
            results[step.id] = StepResult(
                step.id, "accepted" if v.accepted else "rejected", reason=" ".join(v.reasons),
                agent=v.agent, files=tuple(v.files), wall_s=v.wall_s,
            )

    record = WorkflowRun(
        id=uuid.uuid4().hex[:12],
        workflow_id=wf.id,
        inputs=resolved,
        steps=tuple(results[s.id] for s in wf.steps if s.id in results),
        started=started,
        ended=_now(),
    )
    _record(root, record)
    return record


__all__ = [
    "PACKAGED_DIR",
    "PlannedStep",
    "StepResult",
    "Workflow",
    "WorkflowError",
    "WorkflowInput",
    "WorkflowRun",
    "WorkflowStep",
    "get",
    "load",
    "parse",
    "plan",
    "resolve_inputs",
    "run",
    "runs_dir",
    "validate",
]
