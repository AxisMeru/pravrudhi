"""OpenClaw's heartbeat: an assistant that wakes on a schedule, looks at what it wants, and does the next small
thing about it.

Every other way this engine moves is a command a user typed: `pravrudhi intent`, `pravrudhi subagents dispatch`,
`pravrudhi update apply`. Objectives (`application/objectives.py`) can sit fully planned and fully unstarted
forever, because a plan (`application/intent.py`) is not itself an action, and nothing here previously turned one
into the other without an operator's keystroke. A heartbeat closes that gap: one wake-up measures the engine's six
drives (`application/kshudha.py`), lets them select which one is largest and eligible, and dispatches the one
action that addresses that drive — through the same swarm machinery a human would use (`application/subagents.py`,
`application/swarm.py`), under the same proposal sandbox policy (`application/sandbox_policy.py`), when the
winning drive has a wired action at all.

`kshudha.select` only decides; it never dispatches (its own module docstring says so). This module is what turns
that decision into work: `samarthya` (capability) still runs the original objective-step dispatch; `seva`
(obligations) dispatches the oldest unmet request criterion; `sthiti` (continuity) proposes the cheapest failing
doctor check's remedy; `sadhana` (resources) has no route to dispatch to, so it records its desire once and steps
aside for the next eligible drive rather than dispatching a doomed route; `pramana_navyata` (freshness) and
`unnati_avakasha` (benchmark headroom) have no wired action in this codebase, so they always produce a bounded
diagnostic, never a fabricated one.

A beat that finds nothing to do, or is not allowed to do it, is still a beat: it is recorded with the reason,
because a heartbeat that only logs when it acts cannot be told apart from one that stopped ticking. Nothing here
writes to the ledger, `research/`, `gates/` or `pravrudhi_kernel/` — a dispatched step produces a proposal exactly
as it would under manual dispatch, for a human to review and execute.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from pravrudhi.agents.registry import build_agent as _registry_build_agent
from pravrudhi.application import kshudha, recipes, requests, subagents, swarm
from pravrudhi.application.delegate import TaskSpec
from pravrudhi.application.intent import compile_intent
from pravrudhi.application.objectives import load_all
from pravrudhi.application.sandbox_policy import apply_policy, policy_for
from pravrudhi.application.update_apply import run_in_progress

PACKAGED_CONFIG = Path(__file__).resolve().parents[1] / "assets" / "configs" / "heartbeat.yaml"

# Capabilities whose step trains or intensively exercises a model on the GPU that a live night has already
# claimed. Evaluation, corpus curation, retrieval, safety review and harness (agents) steps only ever produce a
# proposal document for a human to review and run (see application/subagents.py's module docstring), so those may
# proceed unattended; the ones here would actually compete for the hardware.
GPU_CAPABILITIES: frozenset[str] = frozenset({"pretrain", "finetune", "rl", "performance"})

BuildAgentFn = Callable[[str, str | None], Any]

DispatchFn = BuildAgentFn


def _config_path(root: Path) -> Path:
    return Path(root) / ".pravrudhi" / "heartbeat.yaml"


@dataclass(frozen=True)
class HeartbeatConfig:
    interval_min: int = 60
    max_dispatch_per_beat: int = 1
    quiet_hours: tuple[int, ...] = ()
    allow_gpu: bool = False


def load_config(root: Path) -> HeartbeatConfig:
    """The operator's heartbeat policy at `.pravrudhi/heartbeat.yaml`, or the packaged defaults if unset."""
    path = _config_path(root)
    raw: dict[str, Any] = yaml.safe_load(
        path.read_text(encoding="utf-8") if path.is_file() else PACKAGED_CONFIG.read_text(encoding="utf-8")
    ) or {}
    return HeartbeatConfig(
        interval_min=int(raw.get("interval_min", 60)),
        max_dispatch_per_beat=int(raw.get("max_dispatch_per_beat", 1)),
        quiet_hours=tuple(int(h) for h in (raw.get("quiet_hours") or ())),
        allow_gpu=bool(raw.get("allow_gpu", False)),
    )


@dataclass(frozen=True)
class BeatRecord:
    """What one call to `beat` did, or why it did nothing. Appended to `.pravrudhi/heartbeat.jsonl` unconditionally,
    a no-op beat included, so the log can be read as "is the heartbeat still ticking", not only "what did it do".

    `drive` and `drive_deficit` are the wire name (`kshudha.WIRE_NAMES`) and deficit of the drive `kshudha.select`
    chose, independent of what was actually dispatched — the two differ when `resources` wins but yields to the
    next eligible drive. `sentence` is `kshudha.sentence(appetite)`, the engine's own account of why."""

    at: str
    looked_at: tuple[str, ...]
    chose: dict[str, str] | None
    reason: str
    result: dict[str, Any] | None = None
    drive: str | None = None
    drive_deficit: float | None = None
    sentence: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "at": self.at,
            "looked_at": list(self.looked_at),
            "chose": self.chose,
            "reason": self.reason,
            "result": self.result,
            "drive": self.drive,
            "drive_deficit": self.drive_deficit,
            "sentence": self.sentence,
        }


def log_path(root: Path) -> Path:
    return Path(root) / ".pravrudhi" / "heartbeat.jsonl"


def _at(moment: datetime) -> str:
    aware = moment if moment.tzinfo else moment.replace(tzinfo=UTC)
    return aware.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _append(root: Path, record: BeatRecord) -> None:
    path = log_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as fh:
        fh.write(json.dumps(record.to_dict(), sort_keys=True) + "\n")


def _finish(
    root: Path, moment: datetime, looked_at: tuple[str, ...], chose: dict[str, str] | None, reason: str,
    result: dict[str, Any] | None, *, drive: str | None, drive_deficit: float | None, sentence: str,
) -> BeatRecord:
    record = BeatRecord(
        at=_at(moment), looked_at=looked_at, chose=chose, reason=reason, result=result,
        drive=drive, drive_deficit=drive_deficit, sentence=sentence,
    )
    _append(root, record)
    return record


def history(root: Path, n: int = 20) -> list[BeatRecord]:
    """The last `n` beats, oldest first. A corrupt line is skipped, not fatal, like `subagents.runs`."""
    path = log_path(root)
    if not path.exists():
        return []
    out: list[BeatRecord] = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            d = json.loads(line)
            deficit = d.get("drive_deficit")
            out.append(
                BeatRecord(
                    at=str(d["at"]),
                    looked_at=tuple(d.get("looked_at") or ()),
                    chose=d.get("chose"),
                    reason=str(d.get("reason") or ""),
                    result=d.get("result"),
                    drive=d.get("drive"),
                    drive_deficit=float(deficit) if deficit is not None else None,
                    sentence=str(d.get("sentence") or ""),
                )
            )
        except (json.JSONDecodeError, KeyError, TypeError):
            continue
    return out[-n:] if n > 0 else out


def _default_build_agent(root: Path) -> BuildAgentFn:
    def build(name: str, model: str | None) -> Any:
        return _registry_build_agent(root, name, model)

    return build


ActionResult = tuple[dict[str, str] | None, str, dict[str, Any] | None]


def _beat_capability(
    root: Path, config: HeartbeatConfig, dispatch: DispatchFn | None,
) -> tuple[tuple[str, ...], dict[str, str] | None, str, dict[str, Any] | None]:
    """`samarthya` (capability): the original behaviour — the most-neglected undone step of any declared
    objective, dispatched through the swarm under the proposal sandbox policy."""
    objectives = load_all(root)
    looked_at = tuple(o.id for o in objectives)
    if not objectives:
        return looked_at, None, "no objectives declared in this workspace", None

    catalogue = tuple(recipes.library())
    installed = frozenset(recipes.installed())

    candidates: list[tuple[str, str, str, Any]] = []  # (last_dispatch_at, objective_id, step_id, task)
    steps_by_key: dict[tuple[str, str], Any] = {}
    for objective in objectives:
        plan = compile_intent(objective, catalogue, installed_skills=installed)
        tasks = subagents.tasks_from_plan(objective, plan, root=root)
        steps_by_id = {step.id: step for step in plan.steps}
        obj_runs = subagents.runs(root, objective.id)
        accepted_steps = {r.step for r in obj_runs if r.accepted}
        next_task = next(
            (t for t in tasks if t.spec.task_id.split(":", 1)[1] not in accepted_steps), None
        )
        if next_task is None:
            continue
        step_id = next_task.spec.task_id.split(":", 1)[1]
        steps_by_key[(objective.id, step_id)] = steps_by_id[step_id]
        last_at = max((r.at for r in obj_runs), default="")
        candidates.append((last_at, objective.id, step_id, next_task))

    if not candidates:
        return looked_at, None, "every objective's plan is fully dispatched and accepted", None

    candidates.sort(key=lambda c: (c[0], c[1]))
    _, objective_id, step_id, task = candidates[0]
    step = steps_by_key[(objective_id, step_id)]
    chose = {"objective": objective_id, "step": step_id}

    needs_gpu = step.capability in GPU_CAPABILITIES
    if needs_gpu and not config.allow_gpu:
        return looked_at, chose, f"step {step_id!r} needs the GPU and allow_gpu is false", None
    if needs_gpu and run_in_progress(root):
        return looked_at, chose, f"step {step_id!r} needs the GPU and a run is already in progress", None
    if config.max_dispatch_per_beat < 1:
        return looked_at, chose, "max_dispatch_per_beat is 0; nothing may be dispatched this beat", None

    build_agent = dispatch or _default_build_agent(root)
    scoped = replace(task, spec=apply_policy(task.spec, policy_for("proposal")))
    verdict = swarm.run_wave(build_agent, [scoped], log=lambda _msg: None, root=root)[0]
    run = subagents.SubagentRun(
        objective=objective_id, step=step_id, task_id=verdict.task_id, route=verdict.agent,
        accepted=verdict.accepted, wall_s=verdict.wall_s, files=tuple(verdict.files), reasons=tuple(verdict.reasons),
    )
    subagents.record_run(root, run)
    result = {"accepted": run.accepted, "route": run.route, "files": list(run.files), "reasons": list(run.reasons)}
    verb = "accepted" if run.accepted else "rejected"
    return looked_at, chose, f"dispatched {objective_id}:{step_id} ({verb})", result


def _obligation_scratch(request_id: str, index: int) -> str:
    return f"proposals/requests/{request_id}/{index}"


def _obligation_prompt(request_text: str, criterion_text: str, scratch: str, validate: str) -> str:
    return (
        f"Operator request (verbatim): {request_text}\n\n"
        f"Oldest unmet acceptance criterion: {criterion_text}\n\n"
        "Everything you write is a PROPOSAL toward this criterion, not evidence: nothing you produce may write to "
        "the ledger, research/, gates/ or pravrudhi_kernel/, and no number you state may be presented as a result.\n"
        f"Deliverable, written only under {scratch}/ using RELATIVE paths: a README.md stating the approach and "
        "what would count as evidence this criterion is met; plus any scripts. Scripts must at least compile.\n"
        f"Validate with `{validate}`."
    )


# A criterion is proposal-shaped work like an evaluate/corpus step (subagents._TIER_BY_CAPABILITY), not a
# candidate-shaping one — a fixed policy choice, not a measurement.
_OBLIGATION_TIER = "standard"


def _default_review_agent(root: Path) -> Any:
    """The read-only agent the completion gate reviews with, built the way the CLI builds it. A machine with no
    coding agent installed returns empty findings, which the gate treats as a review that did not do its job
    and refuses on — the fail-closed direction, and the right one for an unattended loop."""
    from pravrudhi.agents.registry import build_agent as make_agent

    def dispatch(task: Any) -> str:
        agent = make_agent(root, "claude-code", "sonnet")
        if agent is None:
            return ""
        workspace = agent.create_workspace(task.task_id)
        run = agent.run(task.prompt, workspace, timeout_s=task.timeout_s)
        agent.stop(workspace)
        return str(run.text)

    return dispatch


def _beat_completion_gate(root: Path, request_id: str) -> ActionResult:
    """Run the completion gate on a fully evidenced request, and act on either answer.

    Passing closes the request, which is what the operator delegated. Refusing means the work is not done —
    so the request goes back to `in_progress` carrying the reviewer's own finding as its note. That is not
    bookkeeping: while it sits at `delivered` it is "awaiting the gate" and every future beat chooses it again,
    which is exactly how this loop wedged. Back at `in_progress` its unmet work is what the next beat sees, and
    the engine moves.
    """
    from pravrudhi.application import completion

    try:
        result = completion.gate(root, request_id, dispatch=_default_review_agent(root), e2e="uv run pytest -q")
    except Exception as error:  # noqa: BLE001 (a gate that cannot run must not stop the heartbeat)
        return (
            {"request": request_id},
            f"the completion gate could not run on {request_id}: {error}",
            {"kind": "gate", "ran": False, "error": str(error)},
        )

    if result.passed:
        requests.advance(root, request_id, "verified", note=result.reason)
        return (
            {"request": request_id},
            f"{request_id} passed the completion gate and is verified",
            {"kind": "gate", "ran": True, "passed": True},
        )

    finding = (result.review.findings.strip() if result.review is not None else "") or result.reason
    requests.advance(root, request_id, "in_progress", note=f"completion gate refused: {finding}"[:4000])
    return (
        {"request": request_id},
        f"{request_id} did not pass the completion gate and is building again: {result.reason}",
        {"kind": "gate", "ran": True, "passed": False, "reason": result.reason},
    )


def _beat_obligations(root: Path, dispatch: DispatchFn | None) -> ActionResult:
    """`seva` (obligations): the oldest unmet request criterion (`requests.next_unmet`), dispatched through the
    swarm exactly like a capability step, scoped to its own proposal scratch directory under `proposals/requests/`."""
    owed = requests.next_obligation(root)
    if owed is None:
        return None, "every captured request is verified; nothing is owed", None
    if owed["kind"] == "verify_request":
        # Everything on this request is evidenced and it has not been through the gate, so the work is the gate,
        # not more building. Reporting "no request has an unmet criterion" and stopping was how the loop came to
        # say it had nothing to do while three requests were still owed.
        #
        # And then it ran the gate for nobody. Naming the command an operator could type is not doing the work:
        # every beat chose this same request, returned no result, and the engine idled for a day while its own
        # adversarial reviewer sat on a finding nobody had read. A beat that can only describe the next action
        # is not a heartbeat. So the gate runs here.
        return _beat_completion_gate(root, str(owed["request"]))
    if owed["kind"] == "advance_request":
        return (
            {"request": str(owed["request"])},
            f"{owed['description']} — every criterion carries evidence, so it is ready to move",
            None,
        )
    found = requests.next_unmet(root)
    if found is None:  # pragma: no cover - next_obligation already answered meet_criterion
        return None, "every captured request is verified; nothing is owed", None
    request, criterion, index = found
    scratch = _obligation_scratch(request.id, index)
    (root / scratch).mkdir(parents=True, exist_ok=True)
    validate = f'test -n "$(ls -A {scratch})" && uv run python -m compileall -q {scratch}'
    spec = TaskSpec(
        task_id=f"request:{request.id}:{index}",
        prompt=_obligation_prompt(request.text, criterion.text, scratch, validate),
        allowed_paths=(f"{scratch}/*",),
        validate=validate,
    )
    task = swarm.SwarmTask(spec, _OBLIGATION_TIER, why=f"oldest unmet criterion of request {request.id}")
    scoped = replace(task, spec=apply_policy(task.spec, policy_for("proposal")))
    build_agent = dispatch or _default_build_agent(root)
    verdict = swarm.run_wave(build_agent, [scoped], log=lambda _msg: None, root=root)[0]
    chose = {"request": request.id, "criterion": str(index)}
    result = {
        "accepted": verdict.accepted, "route": verdict.agent, "files": list(verdict.files),
        "reasons": list(verdict.reasons),
    }
    verb = "accepted" if verdict.accepted else "rejected"
    return chose, f"dispatched request {request.id} criterion {index} ({verb})", result


# The cheapest-first order to try a failing doctor check's remedy in: (cost, remedy description). `gpu` is
# excluded because `doctor.run_doctor` reports it `ok=True` unconditionally, so it can never fail.
_CONTINUITY_REMEDIES: dict[str, tuple[int, str]] = {
    "initialised": (1, "run `pravrudhi init` to create the missing config or ledger"),
    "prereg": (2, "write the missing pre-registration file(s) under research/prereg/"),
    "pools": (3, "seal a pool so a manifest exists under .pravrudhi/kernel/pools"),
    "docker": (4, "install or start Docker so the sandbox runner is available"),
    "ledger": (5, "investigate and repair the ledger integrity failure before any further run"),
}
_DEFAULT_REMEDY_COST = 99


def _continuity_remedy(name: str) -> tuple[int, str]:
    return _CONTINUITY_REMEDIES.get(name, (_DEFAULT_REMEDY_COST, f"diagnose and repair the failing {name!r} check"))


def _beat_continuity(sthiti: kshudha.Drive) -> ActionResult:
    """`sthiti` (continuity): the cheapest failing doctor check's remedy, read from the drive's own `sources`
    (`doctor:<name>=ok|fail`, from `kshudha.sthiti_drive`) rather than re-running `doctor.run_doctor`. This never
    executes a repair itself — installing Docker or rewriting the ledger is not something a heartbeat does
    unattended — it only proposes which one to do next, same as every other beat action."""
    failing = [s.split(":", 1)[1].split("=", 1)[0] for s in sthiti.sources if s.endswith("=fail")]
    if not failing:
        return None, "no continuity check is currently failing; nothing to remedy", None
    name = min(failing, key=lambda n: (_continuity_remedy(n)[0], n))
    _, remedy = _continuity_remedy(name)
    chose = {"check": name}
    reason = f"running the remedy for the failing {name!r} continuity check: {remedy}"
    return chose, reason, {"check": name, "remedy": remedy}


def _beat_diagnostic(drive: kshudha.Drive) -> ActionResult:
    """`pramana_navyata` (freshness) and `unnati_avakasha` (benchmark headroom) have no wired dispatch action in
    this codebase — no evidence-freshness source, no budgeted benchmark-trial runner — so winning never dispatches
    anything; it always produces this bounded, honest diagnostic instead of a fabricated action."""
    chose = {"drive": drive.wire_name}
    if drive.unknown:
        detail = drive.blocked_reason or "no source is wired into the engine yet"
    else:
        detail = f"deficit={drive.deficit:.4f} but no dispatch action is wired for {drive.wire_name} yet"
    reason = f"{drive.wire_name}: diagnostic only; {detail}"
    result = {"kind": "diagnostic", "unknown": drive.unknown, "sources": list(drive.sources)}
    return chose, reason, result


def _beat_resources(sadhana: kshudha.Drive) -> ActionResult:
    """`sadhana` (resources): there is no route to dispatch a usable coding-agent route into existence, so this
    records the desire once rather than dispatching a doomed action — used only when no other drive is eligible
    to take over this beat."""
    chose = {"drive": "resources"}
    reason = "no usable coding-agent route is available; recording the desire, nothing else is eligible to act"
    result = {"kind": "desire", "sources": list(sadhana.sources)}
    return chose, reason, result


def _dispatch_drive(
    drive_id: str, *, root: Path, config: HeartbeatConfig, dispatch: DispatchFn | None,
    drives_by_id: dict[str, kshudha.Drive],
) -> tuple[tuple[str, ...], dict[str, str] | None, str, dict[str, Any] | None]:
    """The action wired to one drive (never `sadhana`, which the caller resolves to a fallback drive first)."""
    if drive_id == "samarthya":
        return _beat_capability(root, config, dispatch)
    if drive_id == "seva":
        chose, reason, result = _beat_obligations(root, dispatch)
        return (), chose, reason, result
    if drive_id == "sthiti":
        chose, reason, result = _beat_continuity(drives_by_id["sthiti"])
        return (), chose, reason, result
    chose, reason, result = _beat_diagnostic(drives_by_id[drive_id])
    return (), chose, reason, result


def _next_eligible(appetite: kshudha.Appetite, exclude: str) -> str | None:
    """The drive `sadhana` yields to: the largest eligible measured deficit among the rest, falling back to an
    unknown drive worth a diagnostic, mirroring how `kshudha.select` itself ranks `largest_unmet` and diagnostics —
    without touching `kshudha.py`, since this is heartbeat's fallback, not the appetite's own selection."""
    candidates = [
        d for d in appetite.drives if d.id != exclude and d.eligible and d.deficit is not None and d.deficit > 0
    ]
    if candidates:
        candidates.sort(key=lambda d: (-d.pressure, d.id))
        return candidates[0].id
    diagnostics = sorted(
        (d for d in appetite.drives if d.id != exclude and d.unknown and d.weight > 0), key=lambda d: d.id,
    )
    return diagnostics[0].id if diagnostics else None


def beat(root: Path, *, dispatch: DispatchFn | None = None, now: datetime | None = None) -> BeatRecord:
    """One heartbeat: measure the six drives (`kshudha.measure`), let them select which one wins
    (`kshudha.select`), and dispatch the action wired to that drive — never the heartbeat's own precedence rule.

    `dispatch` takes the shape `swarm.run_wave` already expects of a `build_agent`: `(name, model) -> agent`.
    Injecting it is what lets a test exercise every branch here without ever running a real agent; production
    callers may pass one, or leave it unset to use the fleet's own `agents.registry.build_agent`.
    """
    root = Path(root)
    moment = now.astimezone(UTC) if now and now.tzinfo else (now.replace(tzinfo=UTC) if now else datetime.now(UTC))
    config = load_config(root)

    if moment.hour in config.quiet_hours:
        return _finish(
            root, moment, (), None, f"quiet hours: {moment.hour:02d}:00 UTC is in {config.quiet_hours}", None,
            drive=None, drive_deficit=None, sentence="",
        )

    appetite_config = kshudha.load_config()
    state = kshudha.load_state(root)
    drives = kshudha.measure(root, appetite_config)
    overdue = kshudha.seva_overdue(requests.backlog(root), appetite_config)
    appetite = kshudha.select(drives, state=state, overdue=overdue, config=appetite_config, now=moment)
    kshudha.save_state(root, state)

    sentence = kshudha.sentence(appetite)
    drive_id = appetite.selected
    drives_by_id = {d.id: d for d in appetite.drives}

    if drive_id is None:
        reason = appetite.resting_reason or "resting: every drive is satisfied"
        return _finish(root, moment, (), None, reason, None, drive=None, drive_deficit=None, sentence=sentence)

    drive_wire = kshudha.WIRE_NAMES[drive_id]
    drive_deficit = drives_by_id[drive_id].deficit

    effective_id = drive_id
    fallback_note = ""
    if drive_id == "sadhana":
        next_id = _next_eligible(appetite, "sadhana")
        if next_id is None:
            chose, reason, result = _beat_resources(drives_by_id["sadhana"])
            return _finish(
                root, moment, (), chose, reason, result, drive=drive_wire, drive_deficit=drive_deficit,
                sentence=sentence,
            )
        fallback_note = (
            "resources have the largest eligible deficit but no usable route exists; recording the desire and "
            f"yielding to {kshudha.WIRE_NAMES[next_id]}: "
        )
        effective_id = next_id

    looked_at, chose, reason, result = _dispatch_drive(
        effective_id, root=root, config=config, dispatch=dispatch, drives_by_id=drives_by_id,
    )
    if fallback_note:
        reason = fallback_note + reason

    return _finish(
        root, moment, looked_at, chose, reason, result, drive=drive_wire, drive_deficit=drive_deficit,
        sentence=sentence,
    )


__all__ = ["BeatRecord", "GPU_CAPABILITIES", "HeartbeatConfig", "beat", "history", "load_config", "log_path"]
