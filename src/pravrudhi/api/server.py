"""`pravrudhi serve`: FastAPI over the ledger. Everything shown is replayed; nothing is hand-set.

Endpoints: /health, /status, /candidates, /candidates/{id}, /observations, /inbox, /evidence/{name}, /swarm,
/swarm/live, POST /inbox/sign (operator identity required; refused for agent identities)."""

from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, FastAPI, Header, HTTPException
from fastapi.routing import APIRoute
from pydantic import BaseModel

from pravrudhi import KERNEL_VERSION, __version__
from pravrudhi.agents.registry import survey
from pravrudhi.api import roles
from pravrudhi.api.chat import build_chat_router
from pravrudhi.api.identity import CurrentUserDep, RequireIdentity, User, auth_mode
from pravrudhi.api.localguard import install as install_local_guard
from pravrudhi.api.runs import build_router as build_runs_router
from pravrudhi.api.schemas import (
    AgentCooldownsResponse,
    AgentsResponse,
    AgentTraceResponse,
    AppetiteResponse,
    ApplyResultResponse,
    BacklogResponse,
    BenchmarksResponse,
    CandidateDetailResponse,
    CandidatesResponse,
    DiffResponse,
    DiffsResponse,
    DispatchResponse,
    DoctorResponse,
    EvidenceResponse,
    ExternalResultsResponse,
    FleetInstallsResponse,
    FleetResponse,
    ForgottenResponse,
    HealthResponse,
    HealthStateResponse,
    HeartbeatResponse,
    InboxListingResponse,
    JobRequest,
    JobResponse,
    JobsResponse,
    LiveAgentsResponse,
    LoomResponse,
    MarkdownResponse,
    MarkReadRequest,
    MemoryNoteResponse,
    MemoryResponse,
    MeResponse,
    MessagingRequest,
    MessagingStatusResponse,
    NightsResponse,
    NotificationsResponse,
    ObjectiveDetailResponse,
    ObjectiveResponse,
    ObjectivesResponse,
    ObservationsResponse,
    PanelVendorsResponse,
    ParityResponse,
    PlanResponse,
    ProviderKeyRemovedResponse,
    ProviderKeyResponse,
    ProvidersResponse,
    RecipesResponse,
    RequestAdvanceRequest,
    RequestCaptureRequest,
    RequestEvidenceRequest,
    RequestResponse,
    RosterResponse,
    SandboxesResponse,
    SearchResponse,
    SignResponse,
    StatusResponse,
    SubagentsResponse,
    SvasthyaResponse,
    SwarmResponse,
    TokenResponse,
    ToolsResponse,
    UpdateConfigResponse,
    UpdateLastCheckResponse,
    UpdateStatusResponse,
    WorkspaceResponse,
    WorkspacesResponse,
)

# One set, from the module that carries the delegation (ADR-0040). A copy here omitted `agent-for-operator`,
# so that identity fell through to the human path and signed with no condition checked.
from pravrudhi.application.delegation import AGENT_IDENTITIES
from pravrudhi.application.doctor import run_doctor
from pravrudhi.application.evidence import render_h1
from pravrudhi.application.external import external_rows
from pravrudhi.application.night import inbox_listing
from pravrudhi.application.notifications import emit as emit_notification
from pravrudhi.application.notifications import mark_read as mark_notifications_read
from pravrudhi.application.notifications import recent as recent_notifications
from pravrudhi.application.notifications import unread as unread_notifications
from pravrudhi.application.requests import Evidence, Request, RequestError
from pravrudhi.application.requests import advance as advance_request
from pravrudhi.application.requests import backlog as requests_backlog
from pravrudhi.application.requests import capture as capture_request
from pravrudhi.application.requests import get as get_request
from pravrudhi.application.requests import meet as meet_criterion
from pravrudhi.application.requests import staleness as request_staleness
from pravrudhi.application.status import status
from pravrudhi.hosts.fleet import fleet_report
from pravrudhi_kernel.ledger import LedgerWriter, replay
from pravrudhi_kernel.ledger.verify import iter_events

# The three ways this engine launches a coding agent as a subprocess. Matched against a process's argv so a live
# dispatch can be told apart from a stalled one; see `_scan_live_agents`.
_LIVE_AGENT_PATTERNS: dict[str, str] = {"claude -p": "claude", "codex exec": "codex", "agent_code": "agent_code"}


def _scan_live_agents() -> list[dict[str, Any]]:
    """The API had no way to show which agent processes were actually running on this machine: the routing log
    and the subagent/self-build run logs record what was dispatched and what came back, but nothing in between,
    so an operator watching a long dispatch could not tell a live worker from a stalled one. This reads the
    process table once and keeps only pid, elapsed time, which launch pattern matched, and (if the process's
    cwd is a `.worktrees/` checkout) that path -- never the full command line, which could carry a secret."""
    try:
        out = subprocess.run(
            ["ps", "-eo", "pid,etimes,args"], capture_output=True, text=True, timeout=5, check=False
        ).stdout
    except (OSError, subprocess.TimeoutExpired):
        return []
    rows: list[dict[str, Any]] = []
    for line in out.splitlines()[1:]:
        parts = line.strip().split(None, 2)
        if len(parts) < 3:
            continue
        pid_s, etimes_s, args = parts
        kind = next((k for pattern, k in _LIVE_AGENT_PATTERNS.items() if pattern in args), None)
        if kind is None:
            continue
        try:
            pid, elapsed_s = int(pid_s), int(etimes_s)
        except ValueError:
            continue
        worktree: str | None = None
        try:
            cwd = os.readlink(f"/proc/{pid}/cwd")
            if "/.worktrees/" in cwd:
                worktree = cwd
        except OSError:
            pass
        rows.append({"pid": pid, "elapsed_s": elapsed_s, "kind": kind, "worktree": worktree})
    return rows


class BenchmarkRequest(BaseModel):
    id: str = ""
    tool: str = "lm-eval"
    metric: str
    direction: str = "up"


class ObjectiveRequest(BaseModel):
    """What the user wants, stated by the user. The engine records it verbatim and does not interpret it."""

    id: str
    intent: str
    track: str
    benchmarks: list[BenchmarkRequest]
    domain: str = ""
    recipes: list[str] = []
    target_delta: float | None = None
    notes: str = ""


class RememberRequest(BaseModel):
    """A durable fact to store, with where it came from."""

    text: str
    source: str = ""


class WorkspaceRequest(BaseModel):
    slug: str


class SignRequest(BaseModel):
    pack: str
    decision: str  # approve | reject | defer
    note: str = ""


class ProviderKeyRequest(BaseModel):
    """A bring-your-own key to validate and store, with an optional base URL for an OpenAI-compatible endpoint."""

    key: str
    base_url: str | None = None


class UpdateConfigRequest(BaseModel):
    """The operator's update policy, set from the settings page."""

    channel: Literal["dev", "release"] = "release"
    auto_apply: bool = False
    check_interval_min: int = 1440
    keep_previous: int = 2


class UpdateApplyRequest(BaseModel):
    """Which channel to apply from. Falls back to the saved config's channel when unset."""

    channel: Literal["dev", "release"] | None = None


def create_app(root: Path, *, nyaya_ask_fn: Any | None = None) -> FastAPI:
    root = Path(root)
    app = FastAPI(title="pravrudhi", version=__version__)
    # Every JSON route lives under /api. The interface is a static export mounted at the root, and the two
    # namespaces collided: a browser navigating to /runs or /models was answered with JSON rather than the
    # page, because the API route matched first. Separating them is also what makes the API addressable on
    # its own, which a client library needs.
    api = APIRouter(prefix="/api")
    # A local engine that can start GPU work must not answer any page the user happens to be visiting: see
    # api/localguard.py. Cross-origin access is off unless the operator names the origins.
    # In `required` mode nobody anonymous reaches a route; added before the guard so CORS wraps its 401s.
    app.add_middleware(RequireIdentity)
    install_local_guard(app, root, enforce=os.environ.get("PRAVRUDHI_DISABLE_LOCAL_GUARD") != "1")
    # The guard returns a JSONResponse directly; declaring its resource leaves token handling intact.
    for route in app.routes:
        if isinstance(route, APIRoute) and route.path == "/api/app-token":
            app.router.routes.remove(route)
            app.add_api_route(route.path, route.endpoint, methods=["GET"], response_model=TokenResponse)
            break
    ledger = root / "research" / "ledger.jsonl"
    # Guards /update/apply and /update/rollback: both run for real, in-process, on the threadpool FastAPI already
    # runs sync routes on, so a second click while one is in flight must be refused rather than started twice.
    _update_lock = threading.Lock()
    _update_busy = False

    @api.get("/doctor")
    def doctor() -> DoctorResponse:
        return DoctorResponse.model_validate(run_doctor(root))

    @api.get("/hosts")
    def hosts() -> FleetResponse:
        return FleetResponse.model_validate(fleet_report(root))

    @api.get("/agents")
    def agents() -> AgentsResponse:
        return AgentsResponse.model_validate(
            [{"name": agent.name, "available": agent.available, "reason": agent.reason} for agent in survey(root)]
        )

    @api.get("/routes", response_model=RosterResponse)
    def routes_ep() -> RosterResponse:
        """Which model can be dispatched to right now, how well it has done, and when a spent one returns.

        Two half-views existed before this and neither answered the question: one said which tools are
        installed, the other which are sitting out a limit, and neither carried cost, record or tier. The
        strongest model once came back and went unused for half an hour because nothing joined them.
        """
        from pravrudhi.application.roster import roster

        seats = roster(root)
        return RosterResponse.model_validate(
            {"seats": [s.to_dict() for s in seats], "ready": sum(s.usable for s in seats), "total": len(seats)}
        )

    @api.get("/agents/cooldowns")
    def agent_cooldowns() -> AgentCooldownsResponse:
        """Which agents are sitting out a vendor usage limit right now, and when each returns (see
        `application.availability`) — the swarm view showed availability but never why a route disappeared."""
        from pravrudhi.application import availability

        cooling = availability.cooling(root)
        return AgentCooldownsResponse.model_validate(
            [{"agent": agent_id, "until": until} for agent_id, until in sorted(cooling.items())]
        )

    @api.get("/swarm", response_model=SwarmResponse)
    def swarm_ep() -> dict[str, Any]:
        """Nothing in the API showed the swarm itself: which agents are routed where, what has been dispatched,
        what was accepted. Agent availability, the routing table's live per-tier choice, and the last 100 runs
        of both the objective swarm and the self-build swarm, newest first."""
        from dataclasses import asdict

        from pravrudhi.application import routing, selfbuild, subagents

        return {
            "agents": [{"name": a.name, "available": a.available, "reason": a.reason} for a in survey(root)],
            "routing": routing.report(root),
            "subagent_runs": [asdict(r) for r in reversed(subagents.runs(root)[-100:])],
            "selfbuild_runs": [asdict(r) for r in reversed(selfbuild.runs(root)[-100:])],
        }

    @api.get("/swarm/live", response_model=LiveAgentsResponse)
    def swarm_live_ep() -> list[dict[str, Any]]:
        """The agent processes actually running on this machine right now, not what the run logs say happened."""
        return _scan_live_agents()

    @api.get("/jobs", response_model=JobsResponse)
    def jobs_ep(n: int = 50) -> list[dict[str, Any]]:
        """Every dispatch-board job, newest first. A read route: it answers on a workspace that has never
        dispatched anything, returning an empty list rather than an error."""
        from pravrudhi.application import dispatchboard

        return [j.to_dict() for j in dispatchboard.jobs(root, max(1, min(n, 500)))]

    @api.post("/jobs", response_model=JobResponse)
    def submit_job(req: JobRequest) -> dict[str, Any]:
        """Queue an ad hoc brief and, if the board has room, start it in the background. Refused outright if it
        names no allowed path, a path that escapes the workspace, an unknown tier, or the queue is already full."""
        from pravrudhi.agents.registry import build_agent
        from pravrudhi.application import dispatchboard

        try:
            job = dispatchboard.submit(
                root,
                title=req.title,
                brief=req.brief,
                allowed_paths=tuple(req.allowed_paths),
                validate=req.validate_cmd,
                tier=req.tier,
                policy=req.policy,
                agent=req.agent,
            )
        except dispatchboard.DispatchError as e:
            raise HTTPException(422, str(e)) from e
        dispatchboard.run_next(root, lambda name, model: build_agent(root, name, model), log=print)
        _watch_job_outcome(job.id)
        return (dispatchboard.get(root, job.id) or job).to_dict()

    def _watch_job_outcome(job_id: str) -> None:
        """A dispatched job's verdict lands in `.pravrudhi/jobs/<id>.json` from a background thread inside
        `dispatchboard.run_next` itself, with nothing to hook at the moment it happens. This polls the same file
        the interface already reads until the job leaves `running`, then turns that verdict into a notification.
        Bounded to ~30 minutes of polling so a job that never finishes does not leave a thread running forever."""
        from pravrudhi.application import dispatchboard

        def _poll() -> None:
            for _ in range(900):
                time.sleep(2)
                job = dispatchboard.get(root, job_id)
                if job is None or job.state not in ("queued", "running"):
                    break
            else:
                return
            if job is None or job.state not in ("accepted", "rejected"):
                return
            kind = "job_accepted" if job.state == "accepted" else "job_rejected"
            emit_notification(
                root, kind=kind, title=f'"{job.title}" was {job.state}',
                detail=" ".join(job.reasons), ref="/swarm", engine_root=root,
            )

        threading.Thread(target=_poll, daemon=True).start()

    @api.post("/jobs/{job_id}/cancel", response_model=JobResponse)
    def cancel_job(job_id: str) -> dict[str, Any]:
        """Stop a queued job. A job already running or finished is returned unchanged."""
        from pravrudhi.application import dispatchboard

        try:
            job = dispatchboard.cancel(root, job_id)
        except dispatchboard.DispatchError as e:
            raise HTTPException(404, str(e)) from e
        if job.state == "cancelled":
            emit_notification(
                root, kind="job_cancelled", title=f'"{job.title}" was cancelled', ref="/swarm",
                engine_root=root,
            )
        return job.to_dict()

    @api.get("/notifications", response_model=NotificationsResponse)
    def notifications_ep(n: int = 50) -> dict[str, Any]:
        """The notification feed a bell in the interface polls: recent entries newest first, and how many are
        still unread. Answers on a workspace that has never emitted one with an empty feed, not an error."""
        rows = recent_notifications(root, max(1, min(n, 500)))
        return {"notifications": [r.to_dict() for r in rows], "unread": len(unread_notifications(root))}

    @api.post("/notifications/read", response_model=NotificationsResponse)
    def notifications_read_ep(req: MarkReadRequest) -> dict[str, Any]:
        """Mark notifications read. An empty `ids` list is "mark all read"."""
        ids = req.ids or [n.id for n in unread_notifications(root)]
        mark_notifications_read(root, ids)
        rows = recent_notifications(root, 50)
        return {"notifications": [r.to_dict() for r in rows], "unread": len(unread_notifications(root))}

    @api.get("/sandboxes", response_model=SandboxesResponse)
    def sandboxes_ep() -> dict[str, Any]:
        """What a dispatched agent is doing right now: its worktree, its declared policy, what it has touched,
        and any write that policy forbade -- plus the persisted history of every violation this workspace has
        observed, so a policy is something an operator can check rather than something they must trust."""
        from dataclasses import asdict

        from pravrudhi.application.sandbox_monitor import violations as sandbox_violations
        from pravrudhi.application.sandbox_monitor import watch as watch_sandboxes

        live = [{**row, "observation": asdict(row["observation"])} for row in watch_sandboxes(root)]
        return {"live": live, "recent_violations": sandbox_violations(root, 50)}

    @api.get("/diffs", response_model=DiffsResponse)
    def diffs_ep(n: int = 20) -> list[dict[str, Any]]:
        """Dispatched tasks with a readable worktree, newest first -- the file list a diff viewer opens onto."""
        from dataclasses import asdict

        from pravrudhi.application.diffs import recent as recent_diffs

        return [asdict(s) for s in recent_diffs(root, max(1, min(n, 200)))]

    @api.get("/diffs/{task_id}", response_model=DiffResponse)
    def diff_ep(task_id: str) -> dict[str, Any]:
        """One dispatched task's worktree, diffed against the commit its branch forked from. A worktree that no
        longer exists, or a task id that would resolve outside `.worktrees/`, comes back as an empty diff with
        `reason` set rather than a 404 -- the caller is asking about a task, not a resource that may not exist yet."""
        from dataclasses import asdict

        from pravrudhi.application.diffs import worktree_diff

        return asdict(worktree_diff(root, task_id))

    @api.get("/parity", response_model=ParityResponse)
    def parity_ep() -> ParityResponse:
        from pravrudhi.application.parity import report

        return ParityResponse.model_validate(report(root).model_dump())

    @api.get("/search", response_model=SearchResponse)
    def search_ep() -> SearchResponse:
        """The shape of the search: how the candidate graph branches, and how often the budget forced a choice.

        A selection rule only earns something when the live pool exceeds what the budget can run, so the pressure
        table is the honest reading of whether the controller has been deciding anything at all.
        """
        from pravrudhi.application.archive import ancestry_report, parent_map, selection_pressure

        ledger = root / "research" / "ledger.jsonl"
        parents = parent_map(ledger)
        pressure = selection_pressure(ledger)
        return SearchResponse.model_validate({
            "ancestry": ancestry_report(parents).to_dict(),
            "pressure": [p.to_dict() for p in pressure],
            "binding_nights": sum(1 for p in pressure if p.binding),
            "declined": sum(p.declined for p in pressure),
        })

    @api.get("/appetite")
    def appetite_ep() -> AppetiteResponse:
        """What the engine wants right now: every drive's operands, the largest eligible deficit, and the
        sentence generated from those fields. A drive whose input cannot be measured reports unknown with the
        reason, never a number nobody computed."""
        from pravrudhi.application import kshudha

        state = kshudha.current(root)
        return AppetiteResponse.model_validate(
            {"drives": [d.to_dict() for d in state.drives], "appetite": state.to_dict(),
             "sentence": kshudha.sentence(state)}
        )

    @api.get("/fleet")
    def fleet_ep() -> FleetInstallsResponse:
        """Every Pravrudhi install the configured fleet roots name, read from its own workspace directory: no
        SSH, no network — an install this engine cannot see on its own filesystem is simply absent here."""
        from pravrudhi.application.fleet import known_installs

        return FleetInstallsResponse.model_validate({"installs": [i.to_dict() for i in known_installs(root)]})

    @api.get("/health-state")
    def health_state_ep() -> HealthStateResponse:
        """Whether this engine can still do the next piece of work, and every check behind that answer."""
        from pravrudhi.application.svasthya import assess

        health = assess(root)
        return HealthStateResponse.model_validate({
            "state": str(health.state),
            "checks": [{"name": c.name, "ok": bool(c.ok), "detail": str(c.detail)} for c in health.checks],
        })

    @api.get("/external", response_model_exclude_unset=True)
    def external() -> ExternalResultsResponse:
        # No ledger means nothing has been scored yet — an empty list, not a missing file. See `/nights`.
        if not ledger.exists():
            return ExternalResultsResponse.model_validate([])
        return ExternalResultsResponse.model_validate(external_rows(ledger))

    @api.get("/nights")
    def nights_ep() -> NightsResponse:
        # A workspace that has run nothing has no ledger — `research/` is gitignored and the file appears when
        # the first night does. Opening it unconditionally meant a fresh install answered this page with a
        # FileNotFoundError before its user had done anything.
        if not ledger.exists():
            return NightsResponse.model_validate([])
        starts: dict[tuple[int, str], dict[str, Any]] = {}
        rows: list[dict[str, Any]] = []
        for event in iter_events(ledger):
            if event.kind != "audit":
                continue
            payload = event.payload
            track = payload.get("track", "lora")
            key = (event.night, track)
            if payload.get("kind") == "night_start":
                starts[key] = payload
            elif payload.get("kind") == "night_end":
                start = starts.get(key, {})
                rows.append(
                    {
                        "night": event.night,
                        "track": track,
                        "selection_policy": start.get("selection_policy"),
                        "spent_gpu_h": payload.get("spent_gpu_h"),
                        "outcomes": payload.get("outcomes"),
                        "incumbent": payload.get("incumbent"),
                    }
                )
        return NightsResponse.model_validate(rows)

    @api.get("/h1/{track}/{nights}")
    def h1(track: str, nights: str) -> MarkdownResponse:
        if not re.fullmatch(r"[0-9]+(?:-[0-9]+)*", nights):
            raise HTTPException(400, "nights must be dash-separated non-negative integers")
        try:
            parsed_nights = tuple(int(night) for night in nights.split("-"))
        except ValueError as exc:
            raise HTTPException(400, "invalid night number") from exc
        return MarkdownResponse.model_validate({"markdown": render_h1(ledger, parsed_nights, track)})

    @api.get("/health")
    def health() -> HealthResponse:
        return HealthResponse.model_validate(
            {"ok": True, "version": __version__, "kernel": KERNEL_VERSION, "ledger": ledger.exists()}
        )

    @api.get("/status")
    def status_ep() -> StatusResponse:
        return StatusResponse.model_validate(status(root))

    @api.get("/heartbeat")
    def heartbeat_ep(n: int = 100) -> HeartbeatResponse:
        from pravrudhi.application.heartbeat import history

        beats = [b.to_dict() for b in history(root, max(1, min(n, 1000)))]
        beats.reverse()
        return HeartbeatResponse.model_validate({"beats": beats})

    @api.get("/svasthya")
    def svasthya_ep() -> SvasthyaResponse:
        """The engine's own survival state (design §5.1), with every failing check named and its detail — the
        single most useful thing the machines page can say when something is wrong, so it gets its own route
        rather than being buried in `/doctor`, which judges a workspace's setup, not its health right now."""
        from pravrudhi.application import svasthya

        return SvasthyaResponse.model_validate(svasthya.assess(root).to_dict())

    def _update_last_checked() -> str | None:
        """Mirrors update_apply.py's own (private) last-check file: the machines page needs to say when this
        install last looked for a release, not just whether one is available."""
        from datetime import UTC, datetime

        path = root / ".pravrudhi" / "update-last-check"
        if not path.is_file():
            return None
        try:
            when = float(path.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            return None
        return datetime.fromtimestamp(when, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    @api.get("/update")
    def update_ep() -> UpdateStatusResponse:
        from pravrudhi.application.updates import status as update_status

        return UpdateStatusResponse.model_validate(update_status())

    @api.get("/update/config")
    def update_config_get() -> UpdateConfigResponse:
        from dataclasses import asdict

        from pravrudhi.application.update_apply import load_config

        return UpdateConfigResponse.model_validate(asdict(load_config(root)))

    @api.get("/update/last-check")
    def update_last_check_ep() -> UpdateLastCheckResponse:
        return UpdateLastCheckResponse.model_validate({"last_checked": _update_last_checked()})

    @api.put("/update/config")
    def update_config_put(req: UpdateConfigRequest) -> UpdateConfigResponse:
        from dataclasses import asdict

        from pravrudhi.application.update_apply import UpdateConfig, save_config

        config = UpdateConfig(
            channel=req.channel,
            auto_apply=req.auto_apply,
            check_interval_min=req.check_interval_min,
            keep_previous=req.keep_previous,
        )
        save_config(root, config)
        return UpdateConfigResponse.model_validate(asdict(config))

    @api.post("/update/apply")
    def update_apply_ep(req: UpdateApplyRequest) -> ApplyResultResponse:
        """Apply an update. Runs on FastAPI's sync-route threadpool, so it never blocks the event loop; a second
        call while one is already running is refused with 409 rather than a 200 that quietly says nothing
        happened, so the interface can tell the two apart."""
        nonlocal _update_busy
        from dataclasses import asdict

        from pravrudhi.application.update_apply import apply as apply_update

        with _update_lock:
            if _update_busy:
                raise HTTPException(409, "an update is already in progress")
            _update_busy = True
        try:
            result = apply_update(root, channel=req.channel)
        finally:
            with _update_lock:
                _update_busy = False
        return ApplyResultResponse.model_validate(asdict(result))

    @api.post("/update/rollback")
    def update_rollback_ep() -> ApplyResultResponse:
        nonlocal _update_busy
        from dataclasses import asdict

        from pravrudhi.application.update_apply import rollback as rollback_update

        with _update_lock:
            if _update_busy:
                raise HTTPException(409, "an update is already in progress")
            _update_busy = True
        try:
            result = rollback_update(root)
        finally:
            with _update_lock:
                _update_busy = False
        return ApplyResultResponse.model_validate(asdict(result))

    @api.get("/candidates")
    def candidates() -> CandidatesResponse:
        # Same as `/nights`: nothing proposed yet is an empty list, not a missing file.
        if not ledger.exists():
            return CandidatesResponse.model_validate([])
        st = replay(ledger)
        return CandidatesResponse.model_validate(
            [{"id": cid, "badge": st.badges[cid], **c.model_dump()} for cid, c in st.candidates.items()]
        )

    @api.get("/candidates/{cid}")
    def candidate(cid: str) -> CandidateDetailResponse:
        st = replay(ledger)
        if cid not in st.candidates:
            raise HTTPException(404, "unknown candidate")
        events = [ev.model_dump() for ev in iter_events(ledger) if ev.candidate_id == cid]
        return CandidateDetailResponse.model_validate(
            {"id": cid, "badge": st.badges[cid], "view": st.candidates[cid].model_dump(), "events": events}
        )

    @api.get("/observations")
    def observations(limit: int = 200) -> ObservationsResponse:
        if not ledger.exists():
            return ObservationsResponse.model_validate([])
        rows = [ev.model_dump() for ev in iter_events(ledger) if ev.kind == "observe"]
        return ObservationsResponse.model_validate(rows[-limit:])

    def _project(user: User | None, workspace: str | None) -> Path:
        """Whose project this request is about.

        Without this every caller read the directory the engine was started in, so a signed-in user asking for
        their objectives was shown the operator's. A workspace is already a complete project root — the same
        `init_project` runs inside it — so resolving here is the whole of the change.
        """
        from pravrudhi.api.workspace_root import RootError, root_for

        try:
            return root_for(user, workspace, engine_root=root)
        except RootError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    @api.get("/objectives")
    def objectives_ep(
        workspace: str | None = None, user: User | None = CurrentUserDep
    ) -> ObjectivesResponse:
        """Every objective in this workspace with its standing. A file that will not load is reported, not hidden."""
        from pravrudhi.application.objectives import load_all, problems, summary

        here = _project(user, workspace)
        return ObjectivesResponse.model_validate(
            {
                "objectives": [summary(here, o) for o in load_all(here)],
                "problems": [{"file": f, "reason": r} for f, r in problems(here)],
            }
        )

    @api.get("/objectives/{oid}")
    def objective_ep(
        oid: str, workspace: str | None = None, user: User | None = CurrentUserDep
    ) -> ObjectiveDetailResponse:
        from pravrudhi.application.objectives import load_all, summary
        from pravrudhi.application.recipes import resolve

        root_here = _project(user, workspace)
        for o in load_all(root_here):
            if o.id == oid:
                return ObjectiveDetailResponse.model_validate(
                    {**summary(root_here, o), "recipe_detail": resolve(o.recipes)}
                )
        raise HTTPException(404, "no such objective")

    def _draft_objective(req: ObjectiveRequest) -> Any:
        """An `ObjectiveRequest` as an in-memory `Objective`, refusing the same shapes `parse` always has. Shared
        by creation and by the plan-preview route, which compiles a draft that is never written to disk."""
        from pravrudhi.application.objectives import parse

        return parse(
            {
                "id": req.id,
                "intent": req.intent,
                "track": req.track,
                "domain": req.domain,
                "recipes": req.recipes,
                "target_delta": req.target_delta,
                "notes": req.notes,
                "benchmarks": [
                    {"id": b.id or b.metric.split()[0], "tool": b.tool, "metric": b.metric, "direction": b.direction}
                    for b in req.benchmarks
                ],
            }
        )

    @api.post("/objectives")
    def create_objective(
        req: ObjectiveRequest, workspace: str | None = None, user: User | None = CurrentUserDep
    ) -> ObjectiveResponse:
        """Record an objective. Refused if it could not be measured, because an unmeasurable goal is a wish."""
        from pravrudhi.application.objectives import ObjectiveError, summary, write

        try:
            obj = _draft_objective(req)
        except ObjectiveError as e:
            raise HTTPException(422, str(e)) from e
        here = _project(user, workspace)
        write(here, obj)
        return ObjectiveResponse.model_validate(summary(here, obj))

    def _plan_dict(obj: Any) -> dict[str, Any]:
        from dataclasses import asdict

        from pravrudhi.application.intent import compile_intent
        from pravrudhi.application.recipes import installed, library

        plan = compile_intent(obj, tuple(library()), installed_skills=frozenset(installed()))
        out = asdict(plan)
        out["objective"] = obj.id  # the full objective is already available at /api/objectives/{oid}
        return out

    @api.get("/objectives/{oid}/plan", response_model=PlanResponse)
    def objective_plan(oid: str, workspace: str | None = None, user: User | None = CurrentUserDep) -> dict[str, Any]:
        """A proposed decomposition of the intent into work. A proposal, never evidence: nothing here has run."""
        from pravrudhi.application.objectives import load_all

        for o in load_all(_project(user, workspace)):
            if o.id == oid:
                return _plan_dict(o)
        raise HTTPException(404, "no such objective")

    @api.post("/objectives/plan-preview", response_model=PlanResponse)
    def objective_plan_preview(req: ObjectiveRequest) -> dict[str, Any]:
        """The same compiler `/objectives/{oid}/plan` calls, run against a draft that has not been recorded yet --
        so the guided flow can show what a plan would look like before the objective it belongs to exists."""
        from pravrudhi.application.objectives import ObjectiveError

        try:
            obj = _draft_objective(req)
        except ObjectiveError as e:
            raise HTTPException(422, str(e)) from e
        return _plan_dict(obj)

    @api.get("/benchmarks", response_model=BenchmarksResponse)
    def benchmarks_ep() -> dict[str, Any]:
        """Every task the external tier has ever scored in this workspace, each with its last measured value.
        Choosing what success means is picking from this list, never typing an external scorer's task syntax."""
        from pravrudhi.application.external import headlines

        latest: dict[str, dict[str, Any]] = {}
        for r in external_rows(ledger) if ledger.exists() else []:
            try:
                pairs = headlines(r)
            except (KeyError, StopIteration, ZeroDivisionError):
                continue
            seq = int(r.get("seq") or 0)
            for name, value, _stderr, n in pairs:
                prev = latest.get(name)
                if prev is not None and prev["seq"] >= seq:
                    continue
                latest[name] = {
                    "id": name.split()[0] if name.split() else name,
                    "tool": str(r.get("tool") or ""),
                    "metric": name,
                    "track": str(r.get("track") or ""),
                    "value": value,
                    "n": n,
                    "seq": seq,
                }
        return {"benchmarks": sorted(latest.values(), key=lambda x: x["metric"])}

    @api.get("/memory", response_model=MemoryResponse)
    async def memory_ep(user: User | None = CurrentUserDep) -> dict[str, Any]:
        """What belongs to the caller. A logged-in user's memory lives in Supabase; a local engine's on disk. Either
        way it is kept apart from the ledger, which owns what the loop learned."""
        from dataclasses import asdict

        from pravrudhi.application.memory_store import store_for

        store = store_for(root, user)
        return {
            "preferences": [{"key": k, **{kk: vv for kk, vv in asdict(p).items() if kk != "key"}}
                            for k, p in store.preferences().items()],
            "notes": [asdict(n) for n in store.recall("", limit=50)],
            "threads": [t.id for t in store.threads()],
        }

    @api.post("/memory/notes", response_model=MemoryNoteResponse)
    async def remember_ep(req: RememberRequest, user: User | None = CurrentUserDep) -> dict[str, Any]:
        """Record a durable fact. Refused if it reads as a bare numeric claim about a result."""
        from dataclasses import asdict

        from pravrudhi.application.memory import MemoryError as MemErr
        from pravrudhi.application.memory_store import store_for

        try:
            return asdict(store_for(root, user).remember(req.text, source=req.source or "api"))
        except MemErr as e:
            raise HTTPException(422, str(e)) from e

    @api.patch("/memory/notes/{note_id}", response_model=MemoryNoteResponse)
    async def revise_note_ep(
        note_id: str, req: RememberRequest, user: User | None = CurrentUserDep
    ) -> dict[str, Any]:
        """Correct a note in place, keeping its id. Refused on the same terms `remember` refuses, since a note
        that could be edited into a ledger number would be a way around the guard rather than an exception to it.

        404 covers both a note that does not exist and one belonging to somebody else: the store filters on the
        caller, and an answer that distinguished the two would confirm another user's note by its absence."""
        from dataclasses import asdict

        from pravrudhi.application.memory import MemoryError as MemErr
        from pravrudhi.application.memory_store import store_for

        try:
            note = store_for(root, user).revise(note_id, req.text, source=req.source or "api")
        except MemErr as e:
            raise HTTPException(422, str(e)) from e
        if note is None:
            raise HTTPException(404, "no such note")
        return asdict(note)

    @api.delete("/memory/notes/{note_id}", response_model=ForgottenResponse)
    async def forget_note_ep(note_id: str, user: User | None = CurrentUserDep) -> dict[str, Any]:
        """Delete a note. 404 rather than a silent success when there is nothing to delete, so a caller whose id
        was wrong learns it here instead of believing something was removed."""
        from pravrudhi.application.memory_store import store_for

        if not store_for(root, user).forget(note_id):
            raise HTTPException(404, "no such note")
        return {"forgotten": note_id}

    @api.get("/tools", response_model=ToolsResponse)
    def tools_ep() -> dict[str, Any]:
        """The tools, connectors and plugins this engine can draw on, each marked available or not on this machine.
        A catalogue, not an execution layer: listing a tool is not a claim it has been invoked."""
        from pravrudhi.application.tools import availability

        return {"tools": availability()}

    def _objective_and_plan(oid: str, project_root: Path) -> tuple[Any, Any]:
        from pravrudhi.application.intent import compile_intent
        from pravrudhi.application.objectives import load_all
        from pravrudhi.application.recipes import installed, library

        for o in load_all(project_root):
            if o.id == oid:
                return o, compile_intent(o, tuple(library()), installed_skills=frozenset(installed()))
        raise HTTPException(404, "no such objective")

    @api.get("/objectives/{oid}/loom", response_model=LoomResponse)
    def objective_loom(oid: str, workspace: str | None = None, user: User | None = CurrentUserDep) -> dict[str, Any]:
        """The plan as Loom source. Readable and editable by a person; nothing in it has run."""
        from pravrudhi.application.loom import lift, lower, to_plan_steps

        o, plan = _objective_and_plan(oid, _project(user, workspace))
        source = lower(plan)
        return {"objective": o.id, "source": source, "steps": list(to_plan_steps(lift(source)))}

    @api.get("/objectives/{oid}/subagents", response_model=SubagentsResponse)
    def objective_subagents(
        oid: str, workspace: str | None = None, user: User | None = CurrentUserDep
    ) -> dict[str, Any]:
        """What the engine would dispatch for this plan, and what it has dispatched so far."""
        from dataclasses import asdict

        from pravrudhi.application.subagents import preview, runs

        here = _project(user, workspace)
        o, plan = _objective_and_plan(oid, here)
        return {"preview": preview(o, plan, here), "runs": [asdict(r) for r in runs(here, o.id)]}

    @api.post("/objectives/{oid}/subagents", response_model=DispatchResponse)
    def objective_dispatch(
        oid: str, workspace: str | None = None, user: User | None = CurrentUserDep
    ) -> dict[str, Any]:
        """Hand the plan's tasks to the swarm in the background. Everything they produce is a proposal."""
        import threading

        from pravrudhi.agents.registry import build_agent
        from pravrudhi.application.subagents import dispatch_plan, tasks_from_plan

        here = _project(user, workspace)
        o, plan = _objective_and_plan(oid, here)
        n = len(tasks_from_plan(o, plan, root=here))
        threading.Thread(
            target=dispatch_plan, args=(o, plan),
            kwargs={"root": here, "build_agent": lambda name, model: build_agent(here, name, model), "log": print},
            daemon=True,
        ).start()
        return {"objective": o.id, "started": n}

    @api.get("/me", response_model=MeResponse)
    async def me(user: User | None = CurrentUserDep) -> dict[str, Any]:
        """Who is asking. Says so plainly when identity is disabled rather than inventing an anonymous user."""
        from pravrudhi.api.edition import edition_for, tagline_for

        which = edition_for(user)
        base = {
            "mode": str(auth_mode()), "authenticated": user is not None,
            "edition": which, "tagline": tagline_for(which),
        }
        return base if user is None else {**base, "id": user.id, "email": user.email, "role": user.role}

    @api.get("/workspaces", response_model=WorkspacesResponse)
    async def workspaces_ep(user: User | None = CurrentUserDep) -> dict[str, Any]:
        """The caller's workspaces. Each is a separate directory with its own ledger; none shares evidence."""
        from pravrudhi.application.workspaces import list_workspaces, workspace_dir

        if user is None:
            return {"owner": "local", "workspaces": [{"slug": "local", "path": str(root)}]}
        return {"owner": user.id, "workspaces": [
            {"slug": s, "path": str(workspace_dir(user.id, s))} for s in list_workspaces(user.id)
        ]}

    @api.post("/workspaces", response_model=WorkspaceResponse)
    async def create_workspace(req: WorkspaceRequest, user: User | None = CurrentUserDep) -> dict[str, Any]:
        """Create (idempotently) a workspace for the caller. Refused without an identity: a workspace has an owner."""
        from pravrudhi.application.workspaces import ensure_workspace

        if user is None:
            raise HTTPException(400, "a workspace has an owner; identity is disabled or no token was sent")
        try:
            return {"slug": req.slug, "path": str(ensure_workspace(user.id, req.slug))}
        except ValueError as e:
            raise HTTPException(422, str(e)) from e

    def _keys(user: User | None, workspace: str | None) -> Any:
        """This caller's provider keys, from their own project.

        The operator's keys live in the engine's project and a user's live in their workspace, and
        `store_for_project` refuses to give a signed-in user the former. That refusal is the point: the engine
        holds working keys, and a user who reached them could spend the operator's account from a machine the
        operator does not control.
        """
        from pravrudhi.application.credentials import CredentialBoundaryError, store_for_project

        try:
            return store_for_project(_project(user, workspace), engine_root=root, user=user)
        except CredentialBoundaryError as e:
            raise HTTPException(status_code=403, detail=str(e)) from e

    @api.get("/providers", response_model=ProvidersResponse)
    async def providers_ep(
        workspace: str | None = None, user: User | None = CurrentUserDep
    ) -> list[dict[str, Any]]:
        """The bring-your-own-key registry, marked configured or not for this caller. Never the key or a
        prefix of it — only the shape a valid key for that provider is expected to have."""
        from pravrudhi.application.credentials import PROVIDERS

        configured = set(_keys(user, workspace).configured())
        return [
            {"id": p.id, "title": p.title, "configured": p.id in configured, "key_prefix": p.key_prefix}
            for p in PROVIDERS.values()
        ]

    @api.get("/panel/vendors", response_model=PanelVendorsResponse)
    async def panel_vendors_ep(
        workspace: str | None = None, user: User | None = CurrentUserDep
    ) -> list[dict[str, Any]]:
        """The seats a comparison can use from this install, and why any of them cannot be used.

        User-facing rather than operator-only, and that is the decision this route records. Everything under
        `ADMIN_ONLY` is about Pravrudhi improving *itself*. Comparing vendors is not: it is the thing a user
        of the product does when they build their own verification layer on top of whichever models they can
        reach, which is what Track A is (ADR-0001, AxisMeru/prabhasa-nyaya). The operator's `panel run` and a
        user's comparison read the same registry and the same stored keys.

        Read-only on purpose. A key arrives through `/api/providers/{provider_id}/key`, which validates it
        against the provider; a second way in would be a second thing to get wrong, and this response can
        never carry a secret because it only ever names variables and provider ids.
        """
        from pravrudhi.application.panel import PANEL_CONFIG, VENDORS, tuned

        project = _project(user, workspace)
        rows: list[dict[str, Any]] = []
        for vendor in tuned(list(VENDORS.values()), config=project / PANEL_CONFIG):
            # `reachable` resolves a stored key against the CALLER's project, not the engine's root, so one
            # user's configured key never shows as another's.
            ok, detail = vendor.reachable_in(project)
            rows.append({
                "id": vendor.id, "interface": vendor.interface, "model": vendor.model,
                "provider": vendor.provider or None, "credential_env": vendor.credential or None,
                "params": dict(vendor.params), "reachable": ok, "detail": detail, "note": vendor.note,
            })
        return rows

    @api.post("/providers/{provider_id}/key", response_model=ProviderKeyResponse)
    async def set_provider_key(
        provider_id: str, req: ProviderKeyRequest,
        workspace: str | None = None, user: User | None = CurrentUserDep,
    ) -> dict[str, Any]:
        """Validate a bring-your-own key against the provider and store it. The validation reason is redacted
        before it leaves the process, since a probe failure can otherwise echo the key back in its message."""
        from pravrudhi.application.credentials import PROVIDERS, redact, validate

        if provider_id not in PROVIDERS:
            raise HTTPException(404, "unknown provider")
        store = _keys(user, workspace)
        validated, reason = validate(provider_id, req.key, base_url=req.base_url)
        store.put(provider_id, req.key)
        return {"provider": provider_id, "configured": True, "validated": validated, "reason": redact(reason)}

    @api.delete("/providers/{provider_id}/key", response_model=ProviderKeyRemovedResponse)
    async def delete_provider_key(
        provider_id: str, workspace: str | None = None, user: User | None = CurrentUserDep
    ) -> dict[str, Any]:
        """Remove a stored bring-your-own key."""
        from pravrudhi.application.credentials import PROVIDERS

        if provider_id not in PROVIDERS:
            raise HTTPException(404, "unknown provider")
        _keys(user, workspace).delete(provider_id)
        return {"provider": provider_id, "configured": False}

    @api.get("/messaging/telegram", response_model=MessagingStatusResponse)
    async def messaging_status_ep(
        workspace: str | None = None, user: User | None = CurrentUserDep
    ) -> dict[str, Any]:
        """Whether this caller's workspace delivers notifications to Telegram, and where."""
        from dataclasses import asdict

        from pravrudhi.application.messaging import telegram_status

        return asdict(telegram_status(_project(user, workspace), engine_root=root))

    @api.put("/messaging/telegram", response_model=MessagingStatusResponse)
    async def set_messaging_ep(
        req: MessagingRequest, workspace: str | None = None, user: User | None = CurrentUserDep
    ) -> dict[str, Any]:
        """Store or amend this workspace's own bot.

        A user brings their own: the engine's credential is the operator's and is reachable only from the
        engine's own root (`messaging.resolve_telegram`), so configuring this is the only way a user's
        notifications reach a phone. The stored token is never read back by any route."""
        from dataclasses import asdict

        from pravrudhi.application.messaging import MessagingError, set_telegram

        try:
            status = set_telegram(
                _project(user, workspace), token=req.token, chat_id=req.chat_id, enabled=req.enabled
            )
        except MessagingError as e:
            raise HTTPException(422, str(e)) from e
        return asdict(status)

    @api.delete("/messaging/telegram", response_model=MessagingStatusResponse)
    async def clear_messaging_ep(
        workspace: str | None = None, user: User | None = CurrentUserDep
    ) -> dict[str, Any]:
        """Forget this workspace's bot entirely."""
        from dataclasses import asdict

        from pravrudhi.application.messaging import clear_telegram, telegram_status

        project = _project(user, workspace)
        clear_telegram(project)
        return asdict(telegram_status(project, engine_root=root))

    @api.get("/agent-trace", response_model=AgentTraceResponse)
    def agent_trace_ep(limit: int = 100) -> dict[str, Any]:
        """What the agents have been doing, newest first: dispatched, accepted, rejected, rate limited, moved to
        another route. One chronological read across a whole wave, which the per-job records cannot give."""
        from dataclasses import asdict

        from pravrudhi.application.continuity import entries

        rows = [asdict(e) for e in entries(root, n=max(1, min(limit, 500)))]
        for row in rows:
            for key in ("detail", "agent", "objective"):
                row[key] = row.get(key) or ""
        return {"entries": list(reversed(rows))}

    @api.get("/recipes")
    def recipes_ep() -> RecipesResponse:
        """The recipe catalogue, each entry marked available or not on this machine. Not evidence: naming a recipe
        does not claim it has been run."""
        from pravrudhi.application.recipes import availability

        return RecipesResponse.model_validate({"recipes": availability()})

    @api.get("/inbox")
    def inbox() -> InboxListingResponse:
        return InboxListingResponse.model_validate(inbox_listing(root))

    @api.get("/evidence/{name}")
    def evidence(name: str) -> EvidenceResponse:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name):
            raise HTTPException(404, "no such evidence document")
        base = (root / "docs" / "evidence").resolve()
        p = (base / f"{name}.md").resolve()
        if p.parent != base or not p.is_file():
            raise HTTPException(404, "no such evidence document")
        return EvidenceResponse.model_validate({"name": name, "markdown": p.read_text()})

    def _request_response(req: Request) -> RequestResponse:
        return RequestResponse.model_validate(
            {**req.to_dict(), "staleness_days": round(request_staleness(req), 2), "progress": list(req.progress())}
        )

    @api.post("/requests")
    async def requests_capture_ep(req: RequestCaptureRequest, user: User | None = CurrentUserDep) -> RequestResponse:
        """Let the web door originate an ask, into the same store `pravrudhi requests-capture` (the operator's
        local hook) already writes to - admin-only, same as every other `/requests*` route: the backlog this
        feeds is the engine improving *itself* (CHARTER's RSI loop), not a per-user product surface, so the
        classification in `api/roles.py` is unchanged, not merely inherited by the path already being listed.
        `session` is derived from the caller, never accepted from the client - the same discipline
        `telegram_inbox.py` already applies (`session=f"telegram:{chat_id}"`), so the backlog can tell a
        web-submitted ask from a hook-submitted one without trusting a caller-supplied label."""
        session = f"web:{user.id}" if user is not None else "web"
        captured = capture_request(root, req.text, asked_at=req.asked_at, session=session)
        return _request_response(captured)

    @api.get("/requests")
    def requests_ep() -> BacklogResponse:
        return BacklogResponse.model_validate(requests_backlog(root))

    @api.get("/requests/{rid}")
    def request_ep(rid: str) -> RequestResponse:
        req = get_request(root, rid)
        if req is None:
            raise HTTPException(404, f"no request {rid}")
        return _request_response(req)

    @api.post("/requests/{rid}/advance")
    def request_advance_ep(rid: str, req: RequestAdvanceRequest) -> RequestResponse:
        try:
            return _request_response(advance_request(root, rid, req.state, note=req.note))
        except RequestError as e:
            raise HTTPException(409, str(e)) from e

    @api.post("/requests/{rid}/criteria/{index}/evidence")
    def request_evidence_ep(rid: str, index: int, req: RequestEvidenceRequest) -> RequestResponse:
        evidence = [Evidence(kind=req.kind, ref=req.ref, note=req.note)]
        try:
            updated = meet_criterion(root, rid, index, evidence)
        except RequestError as e:
            raise HTTPException(409, str(e)) from e
        criterion_text = updated.criteria[index].text if 0 <= index < len(updated.criteria) else ""
        emit_notification(
            root, kind="criterion_met", title=f"Criterion met on request {rid}",
            detail=criterion_text, ref="/requests", engine_root=root,
        )
        return _request_response(updated)

    @api.post("/inbox/sign")
    def sign(req: SignRequest, x_pravrudhi_operator: str | None = Header(default=None)) -> SignResponse:
        # ADR-0040. This route is what "giving autonomy to studio and product apps themselves" needed: the
        # apps sign through here, and it answered 403 to every agent identity. A person still signs as
        # themselves; an agent signs as the delegation's identity, under the delegation's own conditions.
        from pravrudhi.application.delegation import load_delegation, unmet_conditions

        who = (x_pravrudhi_operator or os.environ.get("PRAVRUDHI_OPERATOR") or "").strip()
        autonomous = False
        if not who or who.lower() in AGENT_IDENTITIES:
            delegation = load_delegation(root)
            if delegation is None:
                raise HTTPException(
                    403, "sign-off is a human act here: set X-Pravrudhi-Operator, or record a delegation in "
                    "configs/delegation.yaml (ADR-0040)"
                )
            allowed, why = delegation.permits("promote_t2")
            if not allowed:
                raise HTTPException(403, f"autonomous sign-off refused: {why}")
            row = next((r for r in inbox_listing(root) if r["pack"] == req.pack), None)
            # `reject` and `defer` need no evidence: declining to promote something cannot promote it. Only
            # `approve` is gated, which keeps the delegation from blocking the loop's ability to prune.
            if req.decision == "approve":
                unmet = unmet_conditions(delegation, act="promote_t2", badge=(row or {}).get("badge"))
                if unmet:
                    raise HTTPException(409, "autonomous approval refused: " + "; ".join(unmet))
            who, autonomous = delegation.identity, True
            if not req.note:
                req = req.model_copy(update={"note": delegation.citation})
        if req.decision not in ("approve", "reject", "defer"):
            raise HTTPException(400, "decision must be approve | reject | defer")
        packs = {r["pack"] for r in inbox_listing(root)}
        if req.pack not in packs:
            raise HTTPException(404, "unknown pack")
        w = LedgerWriter.open(ledger, KERNEL_VERSION)
        import hashlib

        ev = w.append(
            "signoff",
            # The actor prefix is the record of WHICH kind of signature this is. Writing `human:` for an
            # autonomous close would make the two indistinguishable in the ledger, which is the one thing
            # ADR-0040 says must stay distinguishable.
            f"{'agent' if autonomous else 'human'}:{who}",
            {
                "pack": req.pack,
                "decision": req.decision,
                "scope": "promote_T2",
                "note": req.note,
                "pack_hash": hashlib.sha256(Path(req.pack, "README.md").read_bytes()).hexdigest(),
            },
            epoch=0,
            night=replay(ledger).night,
        )
        return SignResponse.model_validate(
            {"seq": ev.seq, "this_hash": ev.this_hash, "decision": req.decision, "by": who}
        )

    app.include_router(api)
    app.include_router(build_chat_router(root))
    # prabhasa-nyaya as a product surface: a question of Indian law, answered from sources by any vendor the
    # user can reach, every citation checked against the corpus. User-facing in both editions.
    from pravrudhi.api.nyaya import build_nyaya_router

    app.include_router(build_nyaya_router(root, ask_fn=nyaya_ask_fn))
    # The run subsystem — starting work, watching it, stopping it — was written, tested and never mounted, so
    # `/api/runs` answered 404 and nothing in the product could begin anything. The desktop application could
    # sign in, list workspaces and set a band, and then had no way to act, because the route that acts was not
    # there. It is the operator's for now: `RunManager` is scoped to the engine's own project, so a run started
    # through it spends the operator's hardware under the operator's keys, which is exactly what the
    # bring-your-own-key boundary exists to prevent. Making it workspace-scoped is what moves it to the product.
    app.include_router(build_runs_router(root))
    # Attach the operator check to the surfaces about Pravrudhi improving itself. Done here, over the finished
    # route table, so the classification lives in one readable list in `roles.py` rather than in sixty
    # decorators, and a route nobody classified fails a test instead of shipping open.
    roles.gate(app)
    return app


def serve(root: Path, host: str = "127.0.0.1", port: int = 8765) -> None:
    import uvicorn

    uvicorn.run(create_app(root), host=host, port=port, log_level="info")


def _json(obj: Any) -> str:
    return json.dumps(obj, indent=2, sort_keys=True)
