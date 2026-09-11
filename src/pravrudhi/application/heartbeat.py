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

import contextlib
import json
import os
import re
import subprocess
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


_ATTEMPTS_FILE = ".pravrudhi/criterion-attempts.json"

# Three paid attempts at one criterion is enough to learn that this loop cannot finish it unaided. The number is
# small on purpose: each attempt is a real dispatch to a real model, and the failure being guarded against is
# spending, not correctness.
MAX_CRITERION_ATTEMPTS = 3

# Paper stalls are free of model calls but each one reads and rewrites the requests store; a beat that meets
# a run of unbuildable criteria stalls this many before it yields, so one beat cannot spend its whole slot
# on bookkeeping if a triage batch drafted a hundred kernel criteria at once.
MAX_PAPER_STALLS_PER_BEAT = 25

# How much of a failed validator's output the beat keeps in its journal row: enough for pytest's summary and the
# last traceback, small enough that heartbeat.jsonl stays readable.
VALIDATION_OUTPUT_TAIL = 1500


def _attempts_path(root: Path) -> Path:
    return Path(root) / _ATTEMPTS_FILE


def _attempts_all(root: Path) -> dict[str, int]:
    try:
        data = json.loads(_attempts_path(root).read_text())
        return {str(k): int(v) for k, v in data.items()} if isinstance(data, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _attempt_key(request_id: str, index: int) -> str:
    return f"{request_id}:{index}"


def attempts(root: Path, request_id: str, index: int) -> int:
    """How many times this exact criterion has been dispatched without moving."""
    return _attempts_all(root).get(_attempt_key(request_id, index), 0)


def record_attempt(root: Path, request_id: str, index: int) -> int:
    """Count one dispatch. On disk, because an hourly loop that forgot on restart would never reach any budget."""
    data = _attempts_all(root)
    key = _attempt_key(request_id, index)
    data[key] = data.get(key, 0) + 1
    path = _attempts_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1, sort_keys=True))
    return data[key]


def clear_attempts(root: Path, request_id: str, index: int) -> None:
    """A criterion that moved has not stalled, whatever it cost to get there."""
    data = _attempts_all(root)
    if data.pop(_attempt_key(request_id, index), None) is None:
        return
    _attempts_path(root).write_text(json.dumps(data, indent=1, sort_keys=True))


def stalled(root: Path, request_id: str, index: int) -> bool:
    """Whether this criterion has spent its budget and should be left to the operator.

    Requests r-5795501a criterion 7 was dispatched nine times in one day, five of them accepted by the swarm and
    every one refused by the completion gate, while the criterion stayed unmet and the Lite Plan seat ran into
    its usage limit. Retrying is right; retrying the identical task hourly for ever is a standing order to spend.
    """
    return attempts(root, request_id, index) >= MAX_CRITERION_ATTEMPTS


_GATE_ATTEMPTS_FILE = ".pravrudhi/gate-attempts.json"
_DISPATCH_FAILURES_FILE = ".pravrudhi/dispatch-failures.json"

# Like criterion attempts, gate attempts are budgeted. A gate that crashes should not rebuild a review agent
# every hour forever. Three attempts is the same budget as criteria.
MAX_GATE_ATTEMPTS = 3

# Dispatch-level failures (validation, workspace race, etc.) are separate from judged attempts. A criterion
# should be parked only after a configured number of dispatch failures, independent of judged attempt count.
MAX_DISPATCH_FAILURES = 3


def _gate_attempts_path(root: Path) -> Path:
    return Path(root) / _GATE_ATTEMPTS_FILE


def _gate_attempts_all(root: Path) -> dict[str, int]:
    try:
        data = json.loads(_gate_attempts_path(root).read_text())
        return {str(k): int(v) for k, v in data.items()} if isinstance(data, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def gate_attempts(root: Path, request_id: str) -> int:
    """How many times the completion gate has been run on this request without succeeding."""
    return _gate_attempts_all(root).get(request_id, 0)


def record_gate_attempt(root: Path, request_id: str) -> int:
    """Count one gate execution. On disk, because an hourly loop that forgot on restart would never reach budget."""
    data = _gate_attempts_all(root)
    data[request_id] = data.get(request_id, 0) + 1
    path = _gate_attempts_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1, sort_keys=True))
    return data[request_id]


def gate_stalled(root: Path, request_id: str) -> bool:
    """Whether this request's gate has spent its budget and should be left to the operator."""
    return gate_attempts(root, request_id) >= MAX_GATE_ATTEMPTS


def _dispatch_failures_path(root: Path) -> Path:
    return Path(root) / _DISPATCH_FAILURES_FILE


def _dispatch_failures_all(root: Path) -> dict[str, int]:
    try:
        data = json.loads(_dispatch_failures_path(root).read_text())
        return {str(k): int(v) for k, v in data.items()} if isinstance(data, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def dispatch_failures(root: Path, request_id: str, index: int) -> int:
    """How many times dispatch has failed (before judge ran) for this criterion."""
    key = f"{request_id}:{index}"
    return _dispatch_failures_all(root).get(key, 0)


def record_dispatch_failure(root: Path, request_id: str, index: int) -> int:
    """Count one dispatch-level failure (accepted=False). Separate from judged attempts."""
    data = _dispatch_failures_all(root)
    key = f"{request_id}:{index}"
    data[key] = data.get(key, 0) + 1
    path = _dispatch_failures_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1, sort_keys=True))
    return data[key]


def dispatch_failures_exhausted(root: Path, request_id: str, index: int) -> bool:
    """Whether this criterion has spent its dispatch failure budget."""
    return dispatch_failures(root, request_id, index) >= MAX_DISPATCH_FAILURES


def _obligation_prompt(
    request_text: str, criterion_text: str, scratch: str, validate: str, *, prior: str = ""
) -> str:
    """`prior` is why the last attempt at this criterion was judged short.

    Three dispatches at the same criterion each started from nothing and produced twenty-odd files apiece,
    because none of them was told what the previous one had failed to do. A retry that carries the reason is a
    second attempt; one that does not is the first attempt again at full price.
    """
    fell_short = f"A previous attempt was judged NOT to meet this criterion because: {prior}\n\n" if prior else ""
    return (
        f"Operator request (verbatim): {request_text}\n\n"
        f"Oldest unmet acceptance criterion: {criterion_text}\n\n"
        f"{fell_short}"
        "Everything you write is a PROPOSAL toward this criterion, not evidence: nothing you produce may write to "
        "the ledger, research/, gates/ or pravrudhi_kernel/, and no number you state may be presented as a result.\n"
        f"Deliverable, written only under {scratch}/ using RELATIVE paths: a README.md stating the approach and "
        "what would count as evidence this criterion is met; plus any scripts. Scripts must at least compile.\n"
        f"Validate with `{validate}`."
    )


_JUDGE_MARKER = "verdict:"


def _judge_prompt(request_text: str, criterion_text: str, files: list[str]) -> str:
    listing = "\n".join(f"  - {f}" for f in files) or "  (nothing)"
    return (
        "You are judging one acceptance criterion. You do not write code and you fix nothing.\n\n"
        f"Operator request (verbatim): {request_text}\n\n"
        f"The criterion: {criterion_text}\n\n"
        f"What the last dispatch produced, relative to the repository root:\n{listing}\n\n"
        "Read those files. Decide whether they actually satisfy the criterion as written - not whether they are "
        "good work, and not whether they describe satisfying it. A proposal that explains what would meet the "
        "criterion does not meet it.\n"
        "Answer with a first line of exactly `VERDICT: met` or `VERDICT: not met`, then one short paragraph "
        "saying why. If you say not met, say what is missing, because the next attempt is given your reason."
    )


def _judged(text: str) -> tuple[bool, str]:
    """Whether the judge said met, fail-closed.

    Anything that is not an explicit `VERDICT: met` is not met - the discipline `completion._judge_review`
    applies to a confident summary that does not show its work, for the same reason: this decision closes a
    criterion, and a judge that cannot be bothered to say so plainly has not made it.
    """
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    for i, line in enumerate(lines):
        low = line.lower()
        if low.startswith(_JUDGE_MARKER):
            said = low.split(":", 1)[1].strip()
            reason = " ".join(lines[i + 1:])[:300] or line
            return said.startswith("met"), reason
    return False, (" ".join(lines)[:300] or "the judge said nothing")


def _default_judge(root: Path) -> Any:
    """The read-only agent that judges a criterion, on the cheap seat.

    Judging is reading and answering, which is what `subagents` calls mechanical work; paying design rates to
    ask "does this file do what the criterion says" is how a plan's allowance disappears into bookkeeping.
    """
    def ask(*, prompt: str) -> str:
        agent = _registry_build_agent(root, "claude-code", "haiku")
        if agent is None:
            return ""
        workspace = None
        try:
            workspace = agent.create_workspace("judge")
            return str(agent.run(prompt, workspace, timeout_s=600).text)
        except (OSError, RuntimeError, ValueError):
            # A machine with no usable agent, or a root that is not a checkout, must not take the beat down: an
            # unjudgeable criterion is simply not met, which is the fail-closed direction and the same one
            # `completion._default_review_agent` takes when it cannot review.
            return ""
        finally:
            if workspace is not None:
                with contextlib.suppress(OSError, RuntimeError):
                    agent.stop(workspace)

    return ask


def _last_judgement(root: Path, request_id: str, index: int) -> str:
    """The reason the previous attempt at this criterion was refused, for the next attempt to start from."""
    request = requests.get(root, request_id)
    prefix = _judgement_note(index, "")
    for entry in reversed(request.notes if request else []):
        note = str(entry.get("note", "")) if isinstance(entry, dict) else str(entry)
        if note.startswith(prefix):
            return note[len(prefix):].strip()
    return ""


def _judgement_note(index: int, why: str) -> str:
    return f"criterion {index} not yet met: {why}"


# A criterion is proposal-shaped work like an evaluate/corpus step (subagents._TIER_BY_CAPABILITY), not a
# candidate-shaping one — a fixed policy choice, not a measurement.
_OBLIGATION_TIER = "standard"


_REVIEW_CRITERION_PREFIX = "Answer the completion review's finding: "


_NAMES_SOMETHING = re.compile(r"`[^`]+`|\b[\w-]+\.(?:py|tsx|ts|jsx|js|ya?ml|json|md|sh|toml)\b")


def _names_something(line: str) -> bool:
    """Whether a line states a finding rather than announcing that one exists.

    The label guard above catches "Summary of the strongest reason:". It does not catch the sentence that
    follows a heading in a well-written review: "I inspected the actual code and assets behind each criterion
    rather than trusting the citations, and found a real reason the completion does not satisfy the operator's
    request." That is long and has no trailing colon, so it became the criterion - and it names nothing, so the
    three agents that picked it up could only guess, produced twenty-odd files apiece, and the attempt budget
    parked the request. A finding a builder can act on says which file, module or asset is wrong; a preamble
    describes the reviewing. Naming a thing is the difference, and these reviewers write those names in
    backticks or as filenames.
    """
    return bool(_NAMES_SOMETHING.search(line))


def _criterion_from_finding(root: Path, request_id: str, finding: str) -> bool:
    """Turn a blocking review into one unmet criterion, unless its finding is already recorded.

    The same objection must not accumulate a new criterion on every beat: an hourly loop would bury the request
    under identical demands and never finish any of them. One open review criterion at a time is enough, because
    until it is met the gate will refuse for the same reason anyway.
    """
    request = requests.get(root, request_id)
    if request is None:
        return False
    if any(c.text.startswith(_REVIEW_CRITERION_PREFIX) and not c.met for c in request.criteria):
        return False
    # The first line that actually says something. A review opens with headings and label lines ("Summary of the
    # strongest reason:"), and taking the first non-heading line produced a criterion reading "…strongest
    # reason:" — a demand with the demand missing, which is worthless to whoever builds against it.
    headline, fallback = "", ""
    for raw in finding.splitlines():
        line = raw.strip().lstrip("#").strip().lstrip("*").strip()
        if not line or line.startswith(("---", "===")):
            continue
        if line.endswith(":") or len(line) < 40:
            continue  # a label, not the finding it labels
        fallback = fallback or line
        if _names_something(line):
            headline = line
            break
    headline = headline or fallback or " ".join(finding.split())[:300]
    requests.add_criteria(
        root, request_id,
        [requests.Criterion(text=f"{_REVIEW_CRITERION_PREFIX}{headline[:300]}", source="engine")],
    )
    return True


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

    # Check if the gate is already stalled (spent its attempt budget)
    if gate_stalled(root, request_id):
        return (
            {"request": request_id},
            f"the completion gate on {request_id} has stalled after {MAX_GATE_ATTEMPTS} attempts; leaving it for the operator",
            {"kind": "gate", "ran": False, "stalled": True, "attempts": gate_attempts(root, request_id)},
        )

    record_gate_attempt(root, request_id)

    try:
        result = completion.gate(root, request_id, dispatch=_default_review_agent(root), e2e="uv run pytest -q")
    except Exception as error:  # noqa: BLE001 (a gate that cannot run must not stop the heartbeat)
        return (
            {"request": request_id},
            f"the completion gate could not run on {request_id}: {error}",
            {"kind": "gate", "ran": False, "error": str(error), "attempts": gate_attempts(root, request_id)},
        )

    if result.passed:
        requests.advance(root, request_id, "verified", note=result.reason)
        return (
            {"request": request_id},
            f"{request_id} passed the completion gate and is verified",
            {"kind": "gate", "ran": True, "passed": True},
        )

    finding = (result.review.findings.strip() if result.review is not None else "") or result.reason

    # A blocking review does not mean the criteria are met and something else is wrong. It means the criteria
    # were too narrow — the reviewer's whole job is to find what the acceptance wording let through. So the
    # finding becomes an unmet criterion, which is what gives the next beat something to build.
    #
    # Without this the request only oscillates: refused back to `in_progress`, called "ready to move" because
    # every existing criterion still carries evidence, advanced to `delivered`, refused again, forever.
    added = _criterion_from_finding(root, request_id, finding)
    requests.advance(root, request_id, "in_progress", note=f"completion gate refused: {finding}"[:4000])
    return (
        {"request": request_id},
        f"{request_id} did not pass the completion gate and is building again: {result.reason}",
        {"kind": "gate", "ran": True, "passed": False, "reason": result.reason, "criterion_added": added},
    )


def _triage_complete(root: Path) -> Callable[[str], str] | None:
    """The chat seat used to decompose a prose ask, or `None` when none is configured or reachable.

    Model access goes through the OpenAI-compatible client, and the criteria come back under
    `requests.CRITERIA_SCHEMA` so the answer is a closed array rather than prose to be parsed. `None` is a
    normal outcome, not a fault: triage then falls back to the deterministic drafter.
    """
    with contextlib.suppress(Exception):
        from pravrudhi.application.chat import chat_endpoint
        from pravrudhi.models.openai_compat import ChatClient

        client = ChatClient(
            chat_endpoint(),
            model=os.environ.get("PRAVRUDHI_CHAT_MODEL", "").strip() or "local",
            api_key=os.environ.get("PRAVRUDHI_CHAT_API_KEY", "").strip() or None,
        )

        def complete(prompt: str) -> str:
            result = client.chat(
                [{"role": "user", "content": prompt}],
                temperature=0.2, max_tokens=1024, json_schema=requests.CRITERIA_SCHEMA,
            )
            return str(result.text)

        # Reachability is checked HERE, not on first use: an unreachable endpoint used to make `decompose_ask`
        # return [] on every ask, which the caller could not tell from "the model read it and found nothing",
        # so every prose ask got the verbatim fallback and the fleet's own seats were never asked.
        client.chat([{"role": "user", "content": "reply with the single word ok"}], temperature=0.0, max_tokens=4)
        return complete
    return _fleet_complete(root)


#: The seats triage may fall back to when no chat endpoint answers, in order. The loop's own CLI seats can
#: decompose an ask as well as a hosted model can, and the operator asked that the fleet be used.
_TRIAGE_VENDORS: tuple[str, ...] = ("claude-cli", "codex-cli")


def _fleet_complete(root: Path) -> Callable[[str], str] | None:
    """Decompose through the first CLI seat that is installed, or `None` when there is none.

    Only for an initialised workspace: a bare directory (every test's `tmp_path`) has no fleet, and a triage
    that phoned a real seat from a unit test would spend the operator's quota on "step up to bigger things".
    """
    if not (Path(root) / ".pravrudhi" / "config.yaml").exists():
        return None
    with contextlib.suppress(Exception):
        from pravrudhi.application import nyaya, panel

        ready = [v["id"] for v in nyaya.available_vendors(root, _TRIAGE_VENDORS) if v["available"]]
        if not ready:
            return None
        vendor = panel.VENDORS[ready[0]]

        def complete(prompt: str) -> str:
            text = panel.ask_vendor(vendor, prompt + "\n\nReply with the JSON object only.").text
            start, end = text.find("{"), text.rfind("}")
            return text[start : end + 1] if start >= 0 and end > start else text

        return complete
    return None


def _beat_triage(root: Path, *, complete: Callable[[str], str] | None = None) -> ActionResult:
    """Nothing has criteria to work, so the work is giving an ask some.

    This branch used to report "every captured request is verified; nothing is owed". That sentence was false
    whenever an ask sat in `captured`, and on 2026-09-09 it was false twenty-eight times over: `next_obligation`
    filters on `r.open and r.criteria`, so every undrafted ask was invisible to it, and the loop described that
    blindness as completion. A drive that cannot see work must not conclude there is none.

    Drafting is one beat's action and dispatching is the next one's. Keeping them apart means the criteria the
    engine wrote are on the record, and readable, before anything is paid to build against them.
    """
    pending = requests.untriaged(root)
    if not pending:
        return None, "every captured request is verified; nothing is owed", None
    model = complete if complete is not None else _triage_complete(root)
    for request in pending:
        triaged = requests.triage(root, request.id, complete=model)
        if triaged is None:
            continue  # states nothing to draft from; the next ask may
        texts = [c.text for c in triaged.criteria]
        return (
            {"request": request.id},
            f"{request.id} had no acceptance criteria, so the loop could not see it; "
            f"drafted {len(texts)} from the ask",
            {"kind": "triage", "request": request.id, "criteria": texts},
        )
    return (
        None,
        f"{len(pending)} captured ask(s) state nothing the engine can draft a criterion from; "
        f"they need the operator's words, not another beat",
        None,
    )


def build_paths_for(text: str) -> tuple[str, ...]:
    """Extract repository paths from criterion text and widen to directory globs.

    Returns paths under allowed prefixes (src/, tests/, app/frontend/src/, scripts/, docs/, configs/, plugin/),
    widened to their directory glob. Returns () if any named path is under protected prefixes.

    Recognized forms:
    - Backticked paths: `src/foo.py` -> src/*
    - Bare filenames: "update utils.py" (under allowed dirs)
    - tests/* is always included
    """
    from pravrudhi.application.selfbuild import PROTECTED_PREFIXES

    if not text or not text.strip():
        return ()

    # Extract backticked paths
    backticked = re.findall(r'`([^`]+)`', text)

    # Also look for bare Python/TypeScript/shell/yaml/markdown file references
    # This is more forgiving to capture mentions like "update handler.py" or "create test_foo.py"
    bare_files = re.findall(r'\b([\w_-]+\.(?:py|ts|tsx|sh|yaml|yml|md))\b', text)

    all_paths = list(backticked) + list(bare_files)

    # Check if any path is under protected prefixes
    for path in all_paths:
        if any(path.startswith(prefix) for prefix in PROTECTED_PREFIXES):
            return ()

    # Widen paths to directory globs and normalize
    widened: set[str] = set()

    for path in all_paths:
        path = path.strip()
        if not path:
            continue

        # If it's a file, widen to directory
        if "/" in path or "." in path:
            parts = path.split("/")
            if parts[0] in ("src", "tests", "app", "scripts", "docs", "configs", "plugin"):
                widened.add(f"{parts[0]}/*")
            elif path.startswith("app/frontend/"):
                widened.add("app/frontend/src/*")
        else:
            # Bare filename - try to infer from context or add tests
            if path.endswith(".py"):
                widened.add("src/*")
            elif path.startswith("test"):
                widened.add("tests/*")

    # Always include tests
    widened.add("tests/*")

    return tuple(sorted(widened))


_BUILD_TIER = "design"
"""Real code is design-tier work: the routing table names the seats that may write it."""

from pravrudhi.application.integrate import BUILD_VALIDATE  # noqa: E402  one command for worktree and main tree


def _build_prompt(request_text: str, criterion_text: str, paths: tuple[str, ...], validate: str, *, prior: str = "") -> str:
    fell_short = f"A previous attempt was judged NOT to meet this criterion because: {prior}\n\n" if prior else ""
    return (
        f"Operator request (verbatim): {request_text}\n\n"
        f"Acceptance criterion to deliver: {criterion_text}\n\n"
        f"{fell_short}"
        "You are MAKING this change in the engine's own source, not proposing it. House rules: a failing test "
        "first, then the change; constants in configs/ or an existing constants module, never magic numbers in "
        "code; Sanskrit primary keys in identifiers where the module already uses them; no new dependencies. "
        "You may write only under: " + ", ".join(paths) + ". You may never write under pravrudhi_kernel/, "
        "research/, gates/ or .pravrudhi/, and no number you state may be presented as a result. "
        f"Before you finish, `{validate}` must pass in your worktree. Do not commit."
    )


def dispatch_mode(criterion: requests.Criterion) -> str:
    """Determine dispatch mode: 'build' or 'proposal' (default).

    Returns 'build' when:
    - criterion.mode is explicitly set to "build", OR
    - criterion.mode is unset (defaults to "proposal") AND build_paths_for returns non-empty
      AND the text names a code file (.py, .ts, .tsx, .sh, .yaml, .md)

    Otherwise returns 'proposal'.
    """
    # Explicit build mode always uses build
    if criterion.mode == "build":
        return "build"

    # Auto-detect: check if paths are named and file is a code file
    paths = build_paths_for(criterion.text)
    if not paths:
        return "proposal"

    # A code file, or a backticked path under a prefix the loop may write under (a directory counts: r-35e8ce7b
    # criterion 0 named `docs/blueprint/02-design/` and a `.pdf`, went the proposal way twice, and the judge
    # refused it twice for the sandbox reason).
    code_extensions = r'\.(py|ts|tsx|js|sh|yaml|yml|md)\b'  # .js: the desktop shell (app/desktop) is plain JS
    if re.search(code_extensions, criterion.text) or re.search(_BUILD_PREFIX_IN_BACKTICKS, criterion.text):
        return "build"

    return "proposal"


_BUILD_PREFIX_IN_BACKTICKS = r"`(?:src|tests|app|scripts|docs|configs|plugin)/"


def unbuildable(root: Path, criterion: requests.Criterion) -> str | None:
    """Why no dispatch can meet this criterion in this checkout, or None when one might.

    Two shapes cost the loop three paid attempts each before anyone read the reason. A criterion that names the
    kernel: T0 changes are an ADR accepted under the delegation before the commit (ADR-0047), which a sandboxed
    agent cannot produce, so build_paths_for() returns () and the proposal fallback writes a README the gate then
    refuses. And a criterion whose named paths are gitignored here (docs/blueprint/ is local): the worktree can
    write them and integrate cannot commit them, so "met" can never carry a commit."""
    from pravrudhi.application.selfbuild import PROTECTED_PREFIXES

    named = [p.strip() for p in re.findall(r"`([^`]+)`", criterion.text) if "/" in p]
    protected = [p for p in named if any(p.startswith(prefix) for prefix in PROTECTED_PREFIXES)]
    if protected:
        return (
            f"names {', '.join(sorted(set(protected)))}: a change under a protected prefix ({', '.join(PROTECTED_PREFIXES)}) "
            "is an ADR accepted before the commit (ADR-0047) or a kernel-computed result, never a swarm dispatch"
        )
    engine_like = [
        p for p in named
        if p.split("/")[0] in ("src", "pravrudhi", "app", "scripts", "plugin") or p.endswith((".py", ".ts", ".tsx"))
    ]
    if engine_like and not (root / "src").is_dir():
        # A product install runs the engine from a wheel: there is no engine source in this root to change, and a
        # worktree here can only produce files nothing imports. The ask belongs upstream in the studio backlog.
        return (
            f"names engine source ({', '.join(sorted(set(engine_like))[:4])}) and this root has no engine source "
            "checkout (no src/ tree): a wheel install cannot build the engine; it belongs upstream in the studio backlog"
        )
    repo_paths = [p for p in named if p.split("/")[0] in ("src", "tests", "app", "scripts", "docs", "configs", "plugin")]
    if repo_paths and (root / ".git").exists():
        probe = subprocess.run(
            ["git", "check-ignore", "--", *repo_paths], cwd=root, capture_output=True, text=True, check=False,
        )
        ignored = [line for line in probe.stdout.splitlines() if line]
        if ignored and len(ignored) == len(set(repo_paths)):
            return (
                f"every path it names is ignored in this checkout ({', '.join(ignored)}): a worktree can write "
                "them and integrate cannot commit them, so no dispatch can meet it here"
            )
    return None


def _beat_obligations(root: Path, dispatch: DispatchFn | None, *, judge: Any = None) -> ActionResult:
    """`seva` (obligations): the oldest unmet request criterion (`requests.next_unmet`), dispatched through the
    swarm exactly like a capability step, scoped to its own proposal scratch directory under `proposals/requests/`.

    When a criterion is in 'build' mode, it is dispatched with allowed_paths set to the paths the criterion
    names, under the 'selfbuild' sandbox policy. On MET, the worktree is integrated into the main tree,
    validated, and committed. On validation failure or conflict, files are restored and the criterion stays unmet."""
    owed = requests.next_obligation(root)
    if owed is None:
        return _beat_triage(root)
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
    if owed["kind"] == "parked_request":
        # Every unmet criterion on every request WITH criteria has spent its attempt budget. That is not the
        # same as nothing to do: an ask with no criteria is invisible to `next_obligation`, and on 2026-09-11
        # ninety of them sat captured while both loops reported the same parked request every hour for five
        # hours with `looked_at: []`. Giving one of them criteria is work this beat can do; only when there is
        # none left is "parked" the whole truth. `watchdog._parked_criteria` still reports the parked state.
        if requests.untriaged(root):
            return _beat_triage(root)
        return (
            {"request": str(owed["request"])},
            f"{owed['request']} is parked: {owed['description']}",
            {"kind": "parked", "criteria": str(owed["text"])},
        )
    if owed["kind"] == "advance_request":
        # Ready to move, so move it. Describing the move and returning nothing was the same failure as the
        # branch above, and together they oscillated: the gate refused a delivered request back to
        # `in_progress`, this branch called it ready, and neither ever changed anything.
        # Guarded even though `next_obligation` now agrees with the guard. A beat that raises kills the unit
        # for an hour, and the two disagreed silently for a day before anyone read the journal; a refusal here
        # is a thing to report, never a thing to crash on.
        try:
            requests.advance(root, str(owed["request"]), "delivered", note="every criterion carries evidence")
        except requests.RequestError as e:
            return (
                {"request": str(owed["request"])},
                f"{owed['request']} looked ready but the state machine refused: {e}",
                {"kind": "advance_refused", "error": str(e)},
            )
        return (
            {"request": str(owed["request"])},
            f"{owed['request']} has evidence on every criterion and is now delivered, awaiting the gate",
            {"kind": "advance", "to": "delivered"},
        )
    paper: list[dict[str, Any]] = []
    while True:
        found = requests.next_unmet(root)
        if found is None:
            if paper:
                return (
                    {"request": paper[-1]["request"], "criterion": str(paper[-1]["criterion"])},
                    f"stalled {len(paper)} criterion(s) no dispatch here can meet, and nothing else is owed",
                    {"kind": "unbuildable", "stalled": paper, **paper[-1]},
                )
            return _beat_triage(root)  # pragma: no cover - next_obligation already answered meet_criterion
        request, criterion, index = found
        why_not = unbuildable(root, criterion)
        if why_not is None:
            break
        # Spend the budget on paper rather than on three model calls that cannot succeed, say why where the
        # operator and the next triage will read it, and keep going: stalling is bookkeeping, not the beat's
        # work. The first live beat with this check stalled one kernel criterion and went home for 20 minutes.
        while not stalled(root, request.id, index):
            record_attempt(root, request.id, index)
        requests.note(root, request.id, f"criterion {index} unbuildable: {why_not}")
        paper.append({"request": request.id, "criterion": index, "why": why_not, "criterion_text": criterion.text[:300]})
        if len(paper) >= MAX_PAPER_STALLS_PER_BEAT:
            return (
                {"request": request.id, "criterion": str(index)},
                f"stalled {len(paper)} criterion(s) no dispatch here can meet; the rest wait for the next beat",
                {"kind": "unbuildable", "stalled": paper, **paper[-1]},
            )
    if stalled(root, request.id, index):
        # Spent its budget: this loop has dispatched this exact criterion MAX_CRITERION_ATTEMPTS times without
        # moving it, so a further identical dispatch buys nothing and costs a real model call. Say so where the
        # operator will see it and let the beat spend itself elsewhere.
        return (
            {"request": request.id, "criterion": str(index)},
            f"{request.id} criterion {index} has stalled after {attempts(root, request.id, index)} attempts; "
            f"leaving it for the operator rather than paying to retry it again",
            {"kind": "stalled", "request": request.id, "criterion": index,
             "attempts": attempts(root, request.id, index), "criterion_text": criterion.text[:300]},
        )
    mode = dispatch_mode(criterion)
    task_id = f"request:{request.id}:{index}"
    if mode == "build":
        # The change itself, in the agent's own worktree under selfbuild's write policy, validated by the
        # engine's own tests. Until 2026-09-11 every obligation went the proposal way below, so the swarm could
        # only ever write a README while the gate judged the operator's actual ask -- the structural reason the
        # gate refused what the swarm accepted, twelve dispatches running.
        paths = build_paths_for(criterion.text)
        spec = TaskSpec(
            task_id=task_id,
            prompt=_build_prompt(request.text, criterion.text, paths, BUILD_VALIDATE,
                                 prior=_last_judgement(root, request.id, index)),
            allowed_paths=paths,
            validate=BUILD_VALIDATE,
        )
        task = swarm.SwarmTask(spec, _BUILD_TIER, why=f"oldest unmet criterion of request {request.id} (build)")
        scoped = replace(task, spec=apply_policy(task.spec, policy_for("selfbuild")))
    else:
        scratch = _obligation_scratch(request.id, index)
        (root / scratch).mkdir(parents=True, exist_ok=True)
        validate = f'test -n "$(ls -A {scratch})" && uv run python -m compileall -q {scratch}'
        spec = TaskSpec(
            task_id=task_id,
            prompt=_obligation_prompt(request.text, criterion.text, scratch, validate,
                                      prior=_last_judgement(root, request.id, index)),
            allowed_paths=(f"{scratch}/*",),
            validate=validate,
        )
        task = swarm.SwarmTask(spec, _OBLIGATION_TIER, why=f"oldest unmet criterion of request {request.id}")
        scoped = replace(task, spec=apply_policy(task.spec, policy_for("proposal")))
    build_agent = dispatch or _default_build_agent(root)
    # H4: Do NOT record attempt before dispatch. Dispatch-level failures (validation, workspace race)
    # return before judge runs and must not consume a judged attempt.
    verdict = swarm.run_wave(build_agent, [scoped], log=lambda _msg: None, root=root)[0]
    chose = {"request": request.id, "criterion": str(index)}
    result = {
        "accepted": verdict.accepted, "route": verdict.agent, "files": list(verdict.files),
        "reasons": list(verdict.reasons),
    }
    verb = "accepted" if verdict.accepted else "rejected"
    if not verdict.accepted:
        # H4: Dispatch-level failure (accepted=False before judge). Record separately, do not consume
        # a judged attempt. If dispatch failures are exhausted, park the criterion.
        dispatch_fails = record_dispatch_failure(root, request.id, index)
        result["dispatch_failures"] = dispatch_fails
        if verdict.validation_output:
            # Two build dispatches on 2026-09-11 were rejected as "validation failed" and the reason lived only in
            # a Verdict nobody kept; the tail of the validator's output is the difference between a beat the next
            # reader can act on and one they must reproduce by hand.
            result["validation_output"] = verdict.validation_output[-VALIDATION_OUTPUT_TAIL:]
        if dispatch_failures_exhausted(root, request.id, index):
            # Too many dispatch-level transients; park this criterion
            return (
                chose,
                f"dispatched request {request.id} criterion {index} ({verb}, dispatch failed {dispatch_fails} times); "
                f"parked after {MAX_DISPATCH_FAILURES} dispatch failures",
                result
            )
        return chose, f"dispatched request {request.id} criterion {index} ({verb})", result

    # Accepted says the diff stayed in scope and the validate command passed. It does not say the criterion is
    # satisfied, so the beat asks rather than assuming - and until it did, nothing in the engine ever called
    # `requests.meet`, which left every criterion a dead end that could only be retried until the budget parked
    # it. Fail-closed: an unclear answer is not met.
    # H4: Now record the judged attempt, only after dispatch accepted and we will run the judge.
    record_attempt(root, request.id, index)
    answer = (judge or _default_judge(root))(
        prompt=_judge_prompt(request.text, criterion.text, list(verdict.files)))
    met, why = _judged(answer)
    result["judged"] = "met" if met else "not met"
    result["judgement"] = why
    if met:
        result["mode"] = mode
        if mode == "build":
            # Met in the worktree is not met in the tree the operator runs. Integration merges the agent's
            # branch three-way, validates in the main tree, commits as the house identity and records the
            # commit as the evidence; a conflict or a failing validate leaves the criterion unmet with a note.
            from pravrudhi.agents.base import GitWorktreeMixin
            from pravrudhi.application import integrate

            worktree = root / ".worktrees" / f"agent-{GitWorktreeMixin.ref_safe(task_id)}"
            outcome = integrate.integrate_build_criterion(
                root, {task_id: worktree}, request.id, index, validate=BUILD_VALIDATE,
            )
            result["integration"] = outcome.to_dict()
            if not outcome.ok:
                return chose, f"request {request.id} criterion {index} judged met but not integrated: {outcome.why}", result
            clear_attempts(root, request.id, index)
            return chose, f"request {request.id} criterion {index} is met and integrated as {outcome.commit}: {why}", result
        requests.meet(root, request.id, index,
                      [requests.Evidence(kind="file", ref=f, note="produced for this criterion")
                       for f in verdict.files])
        clear_attempts(root, request.id, index)
        return chose, f"request {request.id} criterion {index} is met: {why}", result
    requests.note(root, request.id, _judgement_note(index, why))
    return chose, f"dispatched request {request.id} criterion {index} ({verb}, judged not met): {why}", result


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
    # "proposing", not "running": this beat deliberately executes nothing, and the previous wording said
    # otherwise once an hour for eight hours on the product workspace while the pool went unsealed and the
    # deficit never moved. The journal is what the operator reads to see whether the loop is working.
    reason = f"proposing the remedy for the failing {name!r} continuity check (not run here): {remedy}"
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
