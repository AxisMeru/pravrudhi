"""The swarm page reported and could not act.

`application/swarm.py` and `application/subagents.py` already dispatch delegated work safely and route it by
measured outcome, but only from a compiled objective plan. Nothing let an operator hand the swarm an ad hoc brief
and watch it run: "where are the tools, skills, plugins, multi-agent orchestration", and the swarm page rendered
routing rows with no control that dispatched anything. This module is that control's backing store and driver.

A `Job` is one ad hoc brief, queued as a plain JSON file under `.pravrudhi/jobs/` so a read route can list it
without a server that has been running since submission. `submit` refuses a job outright rather than queue
something that could never be dispatched safely: no declared allowed paths, or a path that reaches outside the
workspace. `run_next` takes the oldest queued job, narrows it to a named sandbox policy exactly as
`application/sandbox_policy.py` already does for compiled plans, and dispatches it through the same swarm
machinery `application/subagents.py` uses -- the router when no agent is pinned, `delegate.dispatch` directly
when one is. The dispatch itself runs in a background thread so the caller (an API request) returns immediately;
`max_concurrent` and `max_queued`, read from the packaged `dispatch.yaml`, keep a burst of submissions from
starting more agent processes than the machine can sustain or queuing more than an operator can review.

Nothing here writes to the ledger, `research/`, `gates/` or `pravrudhi_kernel/`: a job's `allowed_paths` are
whatever the caller declares, narrowed by the sandbox policy exactly as any other dispatched task, and a job's
outcome is operational state beside the workspace's other run logs, never evidence.
"""

from __future__ import annotations

import json
import os
import threading
import uuid
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import yaml

from pravrudhi.application import swarm
from pravrudhi.application.delegate import TaskSpec, Verdict, dispatch
from pravrudhi.application.sandbox_policy import SandboxPolicyError, apply_policy, policy_for
from pravrudhi.application.swarm import SCOPE_PREAMBLE, TIERS, SwarmTask

PACKAGED_CONFIG = Path(__file__).resolve().parents[1] / "assets" / "configs" / "dispatch.yaml"

JobState = Literal["queued", "running", "accepted", "rejected", "cancelled"]

_LOCK = threading.Lock()


class DispatchError(ValueError):
    """A submitted brief this board refuses outright: no allowed paths, an escaping path, or a full queue."""


@dataclass
class Job:
    """One ad hoc dispatch, persisted as `.pravrudhi/jobs/<id>.json`. Not evidence: see module docstring."""

    id: str
    title: str
    brief: str
    allowed_paths: tuple[str, ...]
    validate: str
    tier: str
    policy: str
    agent: str | None
    state: JobState
    created: str
    started: str | None = None
    ended: str | None = None
    route: str | None = None
    accepted: bool | None = None
    reasons: tuple[str, ...] = field(default_factory=tuple)
    files: tuple[str, ...] = field(default_factory=tuple)
    wall_s: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["allowed_paths"] = list(self.allowed_paths)
        d["reasons"] = list(self.reasons)
        d["files"] = list(self.files)
        return d


@dataclass(frozen=True)
class DispatchConfig:
    max_concurrent: int
    max_queued: int


def load_config(path: Path | None = None) -> DispatchConfig:
    raw = yaml.safe_load((path or PACKAGED_CONFIG).read_text())
    return DispatchConfig(
        max_concurrent=int(raw.get("max_concurrent", 2)),
        max_queued=int(raw.get("max_queued", 20)),
    )


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _escapes(path: str) -> bool:
    """Whether a declared path could resolve outside the workspace root.

    A path is refused if it is absolute, home-relative, or normalises to something that climbs above the root
    (`../x`, or bare `..`). Glob characters are left alone: `os.path.normpath` does not understand `*` or `**`,
    but it only ever collapses `.` and `..` segments, which is the only thing this check needs.
    """
    if not path or path.startswith("/") or path.startswith("~"):
        return True
    normalised = os.path.normpath(path)
    return normalised == ".." or normalised.startswith(f"..{os.sep}")


def _job_from_dict(d: dict[str, Any]) -> Job:
    return Job(
        id=str(d["id"]),
        title=str(d["title"]),
        brief=str(d["brief"]),
        allowed_paths=tuple(d.get("allowed_paths", ())),
        validate=str(d["validate"]),
        tier=str(d["tier"]),
        policy=str(d["policy"]),
        agent=d.get("agent"),
        state=str(d["state"]),  # type: ignore[arg-type]
        created=str(d["created"]),
        started=d.get("started"),
        ended=d.get("ended"),
        route=d.get("route"),
        accepted=d.get("accepted"),
        reasons=tuple(d.get("reasons", ())),
        files=tuple(d.get("files", ())),
        wall_s=float(d.get("wall_s", 0.0)),
    )


def jobs_dir(root: Path) -> Path:
    return Path(root) / ".pravrudhi" / "jobs"


def _job_path(root: Path, job_id: str) -> Path:
    return jobs_dir(root) / f"{job_id}.json"


def _write(root: Path, job: Job) -> None:
    d = jobs_dir(root)
    d.mkdir(parents=True, exist_ok=True)
    p = _job_path(root, job.id)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(job.to_dict(), sort_keys=True))
    tmp.replace(p)


def _read(root: Path, job_id: str) -> Job | None:
    p = _job_path(root, job_id)
    if not p.is_file():
        return None
    try:
        return _job_from_dict(json.loads(p.read_text()))
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None


def _read_all(root: Path) -> list[Job]:
    d = jobs_dir(root)
    if not d.exists():
        return []
    out: list[Job] = []
    for p in sorted(d.glob("*.json")):
        try:
            out.append(_job_from_dict(json.loads(p.read_text())))
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            continue  # a corrupt job file must not blind the board to the rest
    return out


def get(root: Path, job_id: str) -> Job | None:
    return _read(root, job_id)


def jobs(root: Path, n: int = 50) -> list[Job]:
    """The most recently created jobs, newest first."""
    return sorted(_read_all(root), key=lambda j: j.created, reverse=True)[: max(0, n)]


def submit(
    root: Path,
    *,
    title: str,
    brief: str,
    allowed_paths: tuple[str, ...],
    validate: str,
    tier: str,
    policy: str = "proposal",
    agent: str | None = None,
) -> Job:
    """Queue a job, or refuse it. Refused outright, before anything is written: no declared allowed path, a path
    that escapes the workspace, an unknown tier, or a queue already at `dispatch.yaml`'s `max_queued`."""
    paths = tuple(allowed_paths)
    if not paths:
        raise DispatchError("a job must declare at least one allowed path")
    escaping = sorted(p for p in paths if _escapes(p))
    if escaping:
        raise DispatchError(f"allowed path(s) escape the workspace: {', '.join(escaping)}")
    if tier not in TIERS:
        raise DispatchError(f"unknown tier {tier!r}; expected one of {', '.join(TIERS)}")
    cfg = load_config()
    with _LOCK:
        queued = [j for j in _read_all(root) if j.state == "queued"]
        if len(queued) >= cfg.max_queued:
            raise DispatchError(f"the queue is full ({cfg.max_queued} job(s) already queued)")
        job = Job(
            id=uuid.uuid4().hex[:12],
            title=title,
            brief=brief,
            allowed_paths=paths,
            validate=validate,
            tier=tier,
            policy=policy,
            agent=agent or None,
            state="queued",
            created=_now(),
        )
        _write(root, job)
    return job


def cancel(root: Path, job_id: str) -> Job:
    """Stop a queued job. A job already running or finished is left as it is: only a queued job can be stopped
    before it costs anything."""
    with _LOCK:
        job = _read(root, job_id)
        if job is None:
            raise DispatchError(f"no such job {job_id!r}")
        if job.state == "queued":
            job.state = "cancelled"
            job.ended = _now()
            _write(root, job)
        return job


def _model_for(agent_name: str) -> str | None:
    """The model the routing table gives this agent, or None to let the agent choose its own.

    An agent name is not enough to run anything: `opencode:alibaba` and `opencode:alibaba-plan` are the same
    program against two endpoints serving different models, so the model has to come from the route rather than
    from the agent's default. `swarm.py` already takes it from `route.pair()`; this is the same lookup for the
    path where the operator pinned an agent by hand.
    """
    from pravrudhi.application import routing

    try:
        table = routing.load_table()
    except Exception:  # noqa: BLE001 (an unreadable table must not stop a pinned dispatch)
        return None
    for route in table.routes.values():
        name, model = route.pair()
        if name == agent_name and model:
            return model
    return None


def _finish(root: Path, job: Job, verdict: Verdict) -> None:
    job.state = "accepted" if verdict.accepted else "rejected"
    job.route = verdict.agent
    job.accepted = verdict.accepted
    job.reasons = tuple(verdict.reasons)
    job.files = tuple(verdict.files)
    job.wall_s = verdict.wall_s
    job.ended = _now()
    with _LOCK:
        _write(root, job)


def _run(root: Path, job: Job, build_agent: Any, log: Any) -> None:
    spec = TaskSpec(task_id=job.id, prompt=job.brief, allowed_paths=job.allowed_paths, validate=job.validate)
    try:
        policy = policy_for(job.policy)
    except SandboxPolicyError as e:
        _finish(root, job, Verdict(job.id, job.agent or "", False, [str(e)]))
        return
    spec = apply_policy(spec, policy)
    if job.agent:
        # A pinned agent bypasses the router entirely: the operator named exactly who should do this work.
        # The router is still where the model lives, though. Passing None here let the agent fall back to its
        # own default, and for `opencode:alibaba-plan` that default is a model the plan endpoint does not
        # serve — every job pinned to it answered "Model not exist." while the route reported ready.
        agent_obj = build_agent(job.agent, _model_for(job.agent))
        if agent_obj is None:
            _finish(root, job, Verdict(job.id, job.agent, False, [f"agent {job.agent!r} is not available here"]))
            return
        scoped = replace(spec, prompt=SCOPE_PREAMBLE + spec.prompt)
        verdict = dispatch(agent_obj, scoped, log=log)
    else:
        task = SwarmTask(spec, tier=job.tier, why=job.title)
        verdicts = swarm.run_wave(build_agent, [task], log=log, root=root)
        verdict = verdicts[0] if verdicts else Verdict(job.id, "", False, ["the swarm produced no result"])
    _finish(root, job, verdict)


def run_next(root: Path, build_agent: Any, *, log: Any = print) -> Job | None:
    """Start the oldest queued job, if the board has room for it.

    Returns the job (now `running`) once it has been handed off, or `None` when there is nothing queued or the
    board is already at `dispatch.yaml`'s `max_concurrent`. The dispatch itself runs in a background thread, so
    this call returns as soon as the job is marked running rather than blocking for the run's duration.
    """
    cfg = load_config()
    with _LOCK:
        all_jobs = _read_all(root)
        running = [j for j in all_jobs if j.state == "running"]
        if len(running) >= cfg.max_concurrent:
            return None
        queued = sorted((j for j in all_jobs if j.state == "queued"), key=lambda j: j.created)
        if not queued:
            return None
        job = queued[0]
        job.state = "running"
        job.started = _now()
        _write(root, job)
    threading.Thread(target=_run, args=(root, job, build_agent, log), daemon=True).start()
    return job


__all__ = [
    "DispatchConfig",
    "DispatchError",
    "Job",
    "cancel",
    "get",
    "jobs",
    "jobs_dir",
    "load_config",
    "run_next",
    "submit",
]
