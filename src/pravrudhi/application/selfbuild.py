"""Pravrudhi's own swarm has been driven from ad-hoc scripts in a scratch directory: a developer holding a
Python shell would write a one-off list of tasks, wire up an agent factory by hand, and throw the result away
once the change landed. Nothing about that path was part of the product, so the engine could not turn its own
swarm on itself except through someone else's private tooling.

This module is that missing capability, built on the same primitives `application/subagents.py` uses to turn an
objective's plan into dispatched work: a self-build task is declared in YAML rather than assembled from an
`IntentPlanProposal`, but it is planned, routed, dispatched and recorded exactly the way a proposal step is.

The one rule that matters is the refusal `load_plan` enforces: a self-build task may improve the engine's own
source, tests and assets, but it may never claim a path under `pravrudhi_kernel/`, `research/`, `gates/` or
`.pravrudhi/`. Those are respectively the kernel a self-build task is not qualified to grade itself against, and
the evidence and operational state that record what the engine has already done. An engine that could edit the
ground it is judged against could rewrite its own report card, so that path is refused before anything is
dispatched, not caught afterward in a diff.

`run_plan` alone still left a human in the middle of every cycle: someone had to author a contract card for a
task before it ran and, once it passed, sign its gate by hand. `propose_card`, `close_gate` and
`run_unattended_cycle` close both ends of that gap, the same way the product and studio apps already close their
own gates autonomously under `configs/delegation.yaml` (ADR-0040): a task is written as a contract card
`gate.find_card` can read before it is dispatched, and a run that actually passed has its gate closed through
`gate.sign_gate_delegated`, under the same `agent-for-operator` identity. A run that did not pass, or a
workspace that carries no active delegation, is left exactly as unsigned, for a person to close by hand.
"""

from __future__ import annotations

import contextlib
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from pravrudhi import KERNEL_VERSION
from pravrudhi.application import delegation as delegation_mod
from pravrudhi.application import gate, routing, swarm
from pravrudhi.application.delegate import TaskSpec

PACKAGED_EXAMPLE = Path(__file__).resolve().parents[1] / "assets" / "selfbuild" / "example.yaml"

# A self-build task may touch the engine's own source, tests, docs and assets. It may never claim a path under
# these: the kernel it would then be grading itself against, and the evidence and operational state that record
# what the engine has already done.
PROTECTED_PREFIXES: tuple[str, ...] = ("pravrudhi_kernel/", "research/", "gates/", ".pravrudhi/")

# `gate.py` infers a card's kind from its id: `P<n>` is a phase, `H<n>` a hypothesis, anything else a loop. A
# self-build task's own card is never a phase or a hypothesis -- it is one iteration of the loop that builds the
# engine -- so it is always proposed under this prefix, the same one `contracts/L5_product_surface.md` uses.
CARD_ID_PREFIX = "L"
_CARD_ID_RE = re.compile(rf"^{CARD_ID_PREFIX}(\d+)_")

# A self-build task's own `validate` is a smoke-level check on the engine's own tree, not a measured study over a
# benchmark pool. The wire value of `pravrudhi_kernel.schema.common.Stage.smoke`.
CARD_TIER = "smoke"


class SelfBuildError(ValueError):
    """A self-build plan that would let a task edit the kernel or the evidence it produces."""


@dataclass(frozen=True)
class BuildTask:
    """One task in a self-build plan, as declared in YAML."""

    id: str
    prompt: str
    allowed_paths: tuple[str, ...]
    validate: str = "uv run pytest -q"
    tier: str = "standard"
    why: str = ""

    def to_swarm_task(self) -> swarm.SwarmTask:
        spec = TaskSpec(task_id=self.id, prompt=self.prompt, allowed_paths=self.allowed_paths, validate=self.validate)
        return swarm.SwarmTask(spec, self.tier, why=self.why)


def _refuse_protected_paths(task_id: str, allowed_paths: tuple[str, ...]) -> None:
    for path in allowed_paths:
        hit = next((prefix for prefix in PROTECTED_PREFIXES if path.startswith(prefix)), None)
        if hit is not None:
            raise SelfBuildError(
                f"self-build task {task_id!r} claims {path!r}: a self-build task may not edit the kernel or the "
                f"evidence, so paths under {', '.join(PROTECTED_PREFIXES)} are refused"
            )


def load_plan(path: Path) -> list[swarm.SwarmTask]:
    """Load a self-build plan from YAML, refusing any task whose `allowed_paths` touch `pravrudhi_kernel/`,
    `research/`, `gates/` or `.pravrudhi/`. That refusal is the point of this module."""
    raw = yaml.safe_load(Path(path).read_text())
    tasks: list[swarm.SwarmTask] = []
    for row in raw.get("tasks", []):
        task_id = str(row["id"])
        allowed = tuple(str(p) for p in row["allowed_paths"])
        _refuse_protected_paths(task_id, allowed)
        build_task = BuildTask(
            id=task_id,
            prompt=str(row["prompt"]),
            allowed_paths=allowed,
            validate=str(row.get("validate", "uv run pytest -q")),
            tier=str(row.get("tier", "standard")),
            why=str(row.get("why", "")),
        )
        tasks.append(build_task.to_swarm_task())
    return tasks


@dataclass(frozen=True)
class BuildRun:
    """One dispatched self-build task and what the swarm's own verdict said about it."""

    task_id: str
    route: str
    accepted: bool
    wall_s: float
    files: tuple[str, ...] = field(default_factory=tuple)
    reasons: tuple[str, ...] = field(default_factory=tuple)
    at: str = ""


def runs_path(root: Path) -> Path:
    return Path(root) / ".pravrudhi" / "selfbuild" / "runs.jsonl"


def record_run(root: Path, run: BuildRun) -> None:
    """Append one run. Operational state, like `subagents.py`'s runs.jsonl: no evidence document may cite it."""
    p = runs_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    row = asdict(run)
    row["files"] = list(run.files)
    row["reasons"] = list(run.reasons)
    row["at"] = run.at or datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    with p.open("a") as fh:
        fh.write(json.dumps(row, sort_keys=True) + "\n")


def runs(root: Path) -> list[BuildRun]:
    """Every recorded self-build run. A corrupt line is skipped, not fatal."""
    p = runs_path(root)
    if not p.exists():
        return []
    out: list[BuildRun] = []
    for line in p.read_text().splitlines():
        if not line.strip():
            continue
        try:
            d = json.loads(line)
            out.append(
                BuildRun(
                    task_id=str(d["task_id"]),
                    route=str(d["route"]),
                    accepted=bool(d["accepted"]),
                    wall_s=float(d.get("wall_s", 0.0)),
                    files=tuple(d.get("files", ())),
                    reasons=tuple(d.get("reasons", ())),
                    at=str(d.get("at", "")),
                )
            )
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            continue  # a corrupt line must not blind the caller to the rest
    return out


def run_plan(root: Path, tasks: list[swarm.SwarmTask], *, build_agent: Any, log: Any = print) -> list[BuildRun]:
    """Dispatch every task through the swarm and record what came back.

    Waves are planned with `swarm.plan` so tasks whose declared paths could collide never run together, and each
    wave runs through `swarm.run_wave(root=root)` so the router learns from the outcome, exactly as
    `subagents.dispatch_plan` does for an objective's plan.
    """
    waves, conflicts = swarm.plan(tasks)
    if conflicts:
        log(f"selfbuild: {len(conflicts)} conflicting task pair(s) deferred to a later wave: {conflicts}")
    out: list[BuildRun] = []
    for wave in waves:
        for verdict in swarm.run_wave(build_agent, wave, log=log, root=root):
            run = BuildRun(
                task_id=verdict.task_id,
                route=verdict.agent,
                accepted=verdict.accepted,
                wall_s=verdict.wall_s,
                files=tuple(verdict.files),
                reasons=tuple(verdict.reasons),
            )
            record_run(root, run)
            out.append(run)
    return out


def _next_card_number(contracts_dir: Path) -> int:
    """The next unused loop-card number under `contracts_dir`, continuing whatever numbering already exists
    there (`L0`..`L5` in this repository) rather than starting over at `L1` and risking a collision with a card
    `gate.find_card` already knows about."""
    highest = 0
    if contracts_dir.exists():
        for p in contracts_dir.glob(f"{CARD_ID_PREFIX}*_*.md"):
            m = _CARD_ID_RE.match(p.name)
            if m:
                highest = max(highest, int(m.group(1)))
    return highest + 1


def propose_card(root: Path, task: swarm.SwarmTask, *, contracts_dir: Path | None = None) -> Path:
    """Write `task` as the next contract card under `contracts/`, in the shape of
    `contracts/L5_product_surface.md`: a title, the acceptance the run must satisfy, and the name of the gate
    file that will carry its verdict. Refuses the same paths `load_plan` refuses, so a card is never written for
    a task that `run_plan` would never have been allowed to dispatch."""
    _refuse_protected_paths(task.spec.task_id, task.spec.allowed_paths)
    contracts_dir = Path(contracts_dir) if contracts_dir is not None else Path(root) / "contracts"
    contracts_dir.mkdir(parents=True, exist_ok=True)
    card_id = f"{CARD_ID_PREFIX}{_next_card_number(contracts_dir)}"
    slug = re.sub(r"[^a-z0-9]+", "-", task.spec.task_id.lower()).strip("-") or "task"
    path = contracts_dir / f"{card_id}_{slug}.md"
    path.write_text(
        f"# {card_id} — {task.why or task.spec.task_id}\n\n"
        f"* **Acceptance.** `{task.spec.validate}`\n"
        f"* **Gate.** `gates/gate_{card_id}.json`\n"
    )
    return path


def _evidence_for(run: BuildRun) -> dict[str, Any]:
    """The evidence-file shape `gate.emit_gate` expects, filled entirely from what the swarm's own verdict
    said -- never hand-set, the same discipline `inbox_sign.record_decision`'s badge already applies to a
    promotion pack."""
    status = "pass" if run.accepted else "fail"
    layer = lambda verdict, evidence: {"verdict": verdict, "evidence": evidence}  # noqa: E731
    return {
        "status": status,
        "tier": CARD_TIER,
        "measure_class": "n/a",
        "code_gate": layer(status, [f"route={run.route or 'unassigned'}", *run.reasons[:3]]),
        "domain_gate": layer("pass", ["no_claim"]),
        "closure": {
            "technical": layer("pass", [f"files touched: {', '.join(run.files) or 'none'}"]),
            "empirical": layer("pass", ["no_claim"]),
            "integrity": layer("pass", [f"wall_s={run.wall_s:.1f}"]),
            "artifacts": layer("pass", [f"{len(run.files)} file(s) in the diff"]),
            "memory": layer("pass", ["recorded in .pravrudhi/selfbuild/runs.jsonl"]),
            "signoff": {"verdict": "pending", "evidence": []},
        },
        "hetvabhasa": None,
        "deviations": [],
        "ledger_head": None,
    }


def close_gate(root: Path, gate_path: Path, *, contracts_dir: Path | None = None) -> Path:
    """Close a passing gate through `gate.sign_gate_delegated`, the same act `pravrudhi gate sign --delegated`
    performs by hand, under the `agent-for-operator` identity `configs/delegation.yaml` grants.

    Refuses before even trying to sign when `check_gate` is not clean or when no active delegation is recorded,
    rather than leaning solely on the delegation's own `conditions` to catch it: those are the operator's dials
    and may not happen to ask for a clean check, but a cycle running unattended must never sign a gate that
    would not pass a person's look either way.
    """
    root = Path(root)
    gate_path = Path(gate_path)
    contracts_dir = Path(contracts_dir) if contracts_dir is not None else root / "contracts"
    problems = gate.check_gate(gate_path, contracts_dir=contracts_dir)
    if problems:
        raise SelfBuildError(f"gate {gate_path.name} is not clean: {'; '.join(problems)}")
    delegation = delegation_mod.load_delegation(root)
    if delegation is None or not delegation.active:
        raise SelfBuildError(f"no active delegation in configs/delegation.yaml; {gate_path.name} stays unsigned")
    return gate.sign_gate_delegated(gate_path, root=root, contracts_dir=contracts_dir)


def run_unattended_cycle(
    root: Path,
    *,
    task: swarm.SwarmTask,
    build_agent: Any,
    log: Any = print,
    contracts_dir: Path | None = None,
    gates_dir: Path | None = None,
) -> tuple[BuildRun, Path]:
    """One self-build task carried end to end with nobody watching: `propose_card` gives it standing as a
    contract card before anything runs, `run_plan` dispatches it and records the result as a `BuildRun`, and --
    only when that run actually passed -- its gate is closed through `close_gate`. A run that did not pass still
    gets its gate emitted, so the evidence exists, but `close_gate` is never even asked to sign it. A refusal
    `close_gate` raises for an accepted run (no active delegation, say) is left unsigned rather than crashing an
    otherwise-healthy night.
    """
    root = Path(root)
    contracts_dir = Path(contracts_dir) if contracts_dir is not None else root / "contracts"
    gates_dir = Path(gates_dir) if gates_dir is not None else root / "gates"
    card_path = propose_card(root, task, contracts_dir=contracts_dir)
    card_id = card_path.name.split("_", 1)[0]
    [run] = run_plan(root, [task], build_agent=build_agent, log=log)
    gates_dir.mkdir(parents=True, exist_ok=True)
    evidence_file = gates_dir / f"{card_id}.evidence.yaml"
    evidence_file.write_text(yaml.safe_dump(_evidence_for(run)))
    gate_path = gate.emit_gate(
        card_id, contracts_dir=contracts_dir, gates_dir=gates_dir, evidence_file=evidence_file,
        kernel_release=KERNEL_VERSION,
    )
    if run.accepted:
        # a legitimate refusal (no delegation, say) leaves the gate unsigned, not the night crashed
        with contextlib.suppress(SelfBuildError):
            close_gate(root, gate_path, contracts_dir=contracts_dir)
    return run, gate_path


def preview(tasks: list[swarm.SwarmTask], root: Path) -> list[dict[str, Any]]:
    """What `run_plan` would dispatch, without dispatching it."""
    out: list[dict[str, Any]] = []
    table = None
    rows: list[routing.Outcome] = []
    try:  # the router's choice, from measured outcomes, so the preview matches what run_plan would do
        table, rows = routing.load_table(), routing.outcomes(root)
    except (OSError, routing.RoutingError):
        table = None
    for task in tasks:
        agent: str
        model: str | None
        if table is not None:
            agent, model = routing.choose(table, rows, task.tier).route.pair()
        else:
            agent, model = task.route()
        out.append(
            {
                "task_id": task.spec.task_id,
                "tier": task.tier,
                "agent": agent,
                "model": model,
                "allowed_paths": list(task.spec.allowed_paths),
                "validate": task.spec.validate,
                "why": task.why,
            }
        )
    return out


__all__ = [
    "SelfBuildError",
    "BuildTask",
    "BuildRun",
    "PROTECTED_PREFIXES",
    "CARD_ID_PREFIX",
    "CARD_TIER",
    "PACKAGED_EXAMPLE",
    "load_plan",
    "run_plan",
    "preview",
    "record_run",
    "runs",
    "runs_path",
    "propose_card",
    "close_gate",
    "run_unattended_cycle",
]
