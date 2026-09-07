"""Runs: the app's verb. Press Run, a night starts, and its progress streams back live.

The engine already knows how to run a night from the command line; the app should not reimplement that. A run is
therefore a supervised subprocess of the same CLI, so a night started from the browser is byte-for-byte the night
a user would start from a terminal, writes to the same ledger, and obeys the same pre-registration. What this module
adds is only what a person watching needs: a run id, live events, a stop button, and a list of what was produced.

Events are parsed from the night's own log lines rather than invented, so the app can only show what the engine
actually said. Anything the parser does not recognise is still delivered as a plain `log` event, never dropped.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import signal
import subprocess
import sys
import threading
import time
import uuid
from collections import deque
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from pravrudhi.api.identity import CurrentUserDep, User
from pravrudhi.api.schemas import (
    PromotedModelsResponse,
    RunEventsResponse,
    RunsResponse,
    RunView,
)
from pravrudhi.application.external import external_rows
from pravrudhi_kernel.ledger.verify import iter_events

_SEED = re.compile(
    r"^(?P<cid>c-\d+): seed (?P<seed>\d+) incumbent=(?P<inc>[\d.]+) candidate=(?P<cand>[\d.]+) "
    r"delta=(?P<delta>[+-]?[\d.]+) boundary=(?P<decision>\w+) \(n=(?P<n>\d+)"
)
_PROMOTED = re.compile(r"^(?P<cid>c-\d+): PROMOTED")
_PROPOSER = re.compile(r"^proposer: (?P<raw>\d+) raw, (?P<accepted>\d+) accepted")
_ROUND = re.compile(r"^round (?P<round>\d+): (?P<selected>\d+) selected, (?P<remaining>[\d.]+) GPU-h remaining")
_CLOSED = re.compile(r"^(?:harness )?night (?P<night>\d+) (?P<status>closed|aborted)")


class RunRequest(BaseModel):
    target: str = Field(pattern="^(model|harness)$")
    bench: str = ""
    budget_gpu_h: float | None = Field(default=None, gt=0, le=48)
    k: int = Field(default=8, ge=1, le=32)
    policy: str = Field(default="efe", pattern="^(efe|greedy|thompson|random)$")
    proposer_gguf: str = ""
    proposer_endpoint: str = ""


@dataclass
class Run:
    id: str
    target: str
    night: int
    request: dict[str, Any]
    started_at: float
    proc: subprocess.Popen[str] | None = None
    status: str = "running"
    events: deque[dict[str, Any]] = field(default_factory=lambda: deque(maxlen=5000))
    best_delta: float | None = None
    promoted: list[str] = field(default_factory=list)
    finished_at: float | None = None

    def view(self) -> dict[str, Any]:
        return {
            "id": self.id, "target": self.target, "night": self.night, "status": self.status,
            "request": self.request, "started_at": self.started_at, "finished_at": self.finished_at,
            "best_delta": self.best_delta, "promoted": self.promoted, "events": len(self.events),
        }


def parse_line(line: str) -> dict[str, Any]:
    """One log line to one event. Unrecognised lines are `log` events, so nothing the engine said is lost."""
    s = line.strip()
    if m := _SEED.match(s):
        d = m.groupdict()
        return {"type": "paired", "candidate": d["cid"], "seed": int(d["seed"]), "incumbent": float(d["inc"]),
                "candidate_score": float(d["cand"]), "delta": float(d["delta"]), "decision": d["decision"], "n": int(d["n"])}
    if m := _PROMOTED.match(s):
        return {"type": "promoted", "candidate": m.group("cid")}
    if m := _PROPOSER.match(s):
        return {"type": "proposed", "raw": int(m.group("raw")), "accepted": int(m.group("accepted"))}
    if m := _ROUND.match(s):
        return {"type": "round", "round": int(m.group("round")), "selected": int(m.group("selected")),
                "remaining_gpu_h": float(m.group("remaining"))}
    if m := _CLOSED.match(s):
        return {"type": "closed", "night": int(m.group("night")), "status": m.group("status")}
    return {"type": "log", "text": s}


def next_night(root: Path, track: str) -> int:
    ledger = root / "research" / "ledger.jsonl"
    last = 0
    if ledger.exists():
        for ev in iter_events(ledger):
            p = ev.payload
            if ev.kind == "audit" and p.get("kind") == "night_start" and (p.get("track") or "lora") == track:
                last = max(last, ev.night)
    return last + 1


class RunManager:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.runs: dict[str, Run] = {}
        self._lock = threading.Lock()

    def _cli(self) -> list[str]:
        override = os.environ.get("PRAVRUDHI_CLI")
        if override:
            return shlex.split(override)
        return [sys.executable, "-m", "pravrudhi"]

    def _command(self, req: RunRequest, night: int) -> list[str]:
        if req.target == "model":
            cmd = self._cli() + ["night", "--night", str(night), "--k", str(req.k), "--policy", req.policy,
                                 "--root", str(self.root)]
            train_parquet = (self.root / ".pravrudhi" / "data" / "gsm8k-train.parquet").resolve()
            if train_parquet.exists():
                cmd += ["--train-parquet", str(train_parquet)]
        else:
            cmd = self._cli() + ["harness-night", "--night", str(night), "--k", str(req.k),
                   "--policy", req.policy, "--root", str(self.root)]
        if req.budget_gpu_h:
            cmd += ["--budget", str(req.budget_gpu_h)]
        if req.proposer_gguf:
            cmd += ["--gguf", req.proposer_gguf]
        if req.proposer_endpoint:
            cmd += ["--proposer-endpoint", req.proposer_endpoint]
        return cmd

    def start(self, req: RunRequest) -> Run:
        with self._lock:
            if any(r.status == "running" for r in self.runs.values()):
                raise HTTPException(status_code=409, detail="a run is already in progress on this engine")
            track = "lora" if req.target == "model" else "harness"
            night = next_night(self.root, track)
            run = Run(id=uuid.uuid4().hex[:12], target=req.target, night=night, request=req.model_dump(),
                      started_at=time.time())
            env = {**os.environ, "PYTHONUNBUFFERED": "1"}
            run.proc = subprocess.Popen(
                self._command(req, night), cwd=self.root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1, env=env, start_new_session=True,
            )
            self.runs[run.id] = run
        threading.Thread(target=self._pump, args=(run,), daemon=True).start()
        return run

    def _pump(self, run: Run) -> None:
        assert run.proc is not None and run.proc.stdout is not None
        for line in run.proc.stdout:
            if "Warning" in line or "warn" in line:
                continue
            ev = parse_line(line)
            ev["t"] = time.time()
            if ev["type"] == "paired":
                run.best_delta = ev["delta"] if run.best_delta is None else max(run.best_delta, ev["delta"])
            if ev["type"] == "promoted":
                run.promoted.append(ev["candidate"])
            run.events.append(ev)
        code = run.proc.wait()
        run.finished_at = time.time()
        if run.status == "stopping":
            run.status = "stopped"
        else:
            run.status = "finished" if code == 0 else "failed"
        run.events.append({"type": "end", "status": run.status, "exit_code": code, "t": time.time()})

    def stop(self, run_id: str) -> Run:
        run = self.get(run_id)
        if run.proc and run.status == "running":
            run.status = "stopping"
            os.killpg(os.getpgid(run.proc.pid), signal.SIGTERM)
        return run

    def get(self, run_id: str) -> Run:
        if run_id not in self.runs:
            raise HTTPException(status_code=404, detail="no such run")
        return self.runs[run_id]

    def stream(self, run_id: str) -> Iterator[str]:
        run = self.get(run_id)
        sent = 0
        while True:
            events = list(run.events)
            for ev in events[sent:]:
                yield f"data: {json.dumps(ev)}\n\n"
            sent = len(events)
            if run.status not in ("running", "stopping") and sent >= len(run.events):
                return
            time.sleep(0.5)


def models_listing(root: Path) -> list[dict[str, Any]]:
    """What the loop produced: each promotion with the external before/after that exists for it."""
    ledger = root / "research" / "ledger.jsonl"
    if not ledger.exists():
        return []
    ext = external_rows(ledger)
    base: dict[str, dict[str, Any]] = {}
    after: dict[str, dict[str, Any]] = {}
    for r in ext:
        cond = str(r.get("condition", ""))
        if cond == "base":
            base[str(r.get("track"))] = r
        elif ":" in cond:
            after[cond.split(":", 1)[1]] = r
    recipes: dict[str, dict[str, Any]] = {}
    out: list[dict[str, Any]] = []
    withdrawn = {int(ev.payload.get("target_seq", -1)) for ev in iter_events(ledger)
                 if ev.kind == "sublate" and ev.payload.get("kind") == "promotion_withdrawn"}
    for ev in iter_events(ledger):
        if ev.kind == "propose" and ev.candidate_id:
            recipes[ev.candidate_id] = ev.payload.get("recipe") or ev.payload.get("harness") or {}
        if ev.kind == "promote" and ev.candidate_id and ev.seq not in withdrawn:
            cid = ev.candidate_id
            track = "H" if ev.surface == "H3.prompt" else "M"
            out.append({
                "id": cid, "track": "harness" if track == "H" else "model", "night": ev.night,
                "recipe": recipes.get(cid, {}), "artefact": ev.payload.get("from_worktree"),
                "external_before": (base.get(track) or {}).get("metrics"),
                "external_after": (after.get(cid) or {}).get("metrics"),
            })
    return out


STALE_RUN_HOURS = 6.0


def _in_flight(root: Path) -> list[dict[str, Any]]:
    """Nights the ledger has opened and not closed: a run happening right now, however it was started.

    The run manager knows only what the app itself launched, so a night started from the command line — which is
    how every night here has been started — was invisible while it ran. Someone watching the app saw nothing
    while the GPU was busy. A `night_start` with no matching `night_end` or `night_abandoned` is a run in flight.
    """
    from pravrudhi_kernel.ledger.verify import iter_events

    ledger = Path(root) / "research" / "ledger.jsonl"
    if not ledger.exists():
        return []
    started: dict[tuple[int, str], dict[str, Any]] = {}
    closed: set[tuple[int, str]] = set()
    last_seen: dict[tuple[int, str], str] = {}
    for ev in iter_events(ledger):
        payload = ev.payload or {}
        kind = payload.get("kind")
        key = (ev.night, str(payload.get("track") or "lora"))
        last_seen[key] = ev.t
        if kind == "night_start":
            started[key] = {"policy": payload.get("selection_policy"), "budget": payload.get("budget_gpu_h"),
                            "at": ev.t, "seq": ev.seq}
        elif kind in ("night_end", "night_abandoned"):
            closed.add(key)
    # A night that died without writing a closing row would otherwise read as running for ever. One whose last
    # event is older than this is not in flight, it was interrupted; the listing shows it as such.
    from datetime import UTC, datetime, timedelta

    stale_after = timedelta(hours=STALE_RUN_HOURS)
    now = datetime.now(UTC)

    out: list[dict[str, Any]] = []
    for (night, track), info in sorted(started.items(), key=lambda kv: -kv[0][0]):
        if (night, track) in closed:
            continue
        seen = last_seen.get((night, track), "")
        try:
            when = datetime.fromisoformat(seen.replace("Z", "+00:00"))
        except ValueError:
            continue
        if when.tzinfo is None:
            when = when.replace(tzinfo=UTC)
        if now - when > stale_after:
            continue
        out.append({
            "id": f"night-{night}-{track}", "target": track, "night": night, "status": "running",
            "request": {"policy": info["policy"]}, "started_at": None, "finished_at": None,
            "best_delta": None, "promoted": [], "events": 0, "historical": False, "in_flight": True,
            "budget_gpu_h": info["budget"],
        })
    return out


def historical_runs(root: Path) -> list[dict[str, Any]]:
    """Closed nights, newest first, in the same shape a live run reports.

    `started_at` and `finished_at` are seconds since the epoch for a live run, and a closed night carries only
    its ledger timestamps, so they are parsed to the same unit rather than left in two formats for the page to
    reconcile.
    """
    from pravrudhi.application.demo_export import _nights

    ledger = Path(root) / "research" / "ledger.jsonl"
    if not ledger.exists():
        return []
    out: list[dict[str, Any]] = []
    for night in sorted(_nights(ledger), key=lambda n: -int(n.get("night") or 0)):
        promoted = list(night.get("promoted") or [])
        out.append({
            "id": f"night-{night['night']}-{night.get('track') or 'lora'}",
            "target": night.get("track") or "lora",
            "night": night.get("night"),
            "status": "finished",
            "request": {"policy": night.get("selection_policy"), "candidates": night.get("candidates")},
            "started_at": None, "finished_at": None,
            "best_delta": None,
            "promoted": promoted,
            "events": night.get("candidates") or 0,
            "historical": True,
            "budget_gpu_h": night.get("spent_gpu_h"),
            "spent_gpu_h": night.get("spent_gpu_h"),
            "pruned": night.get("pruned"),
        })
    return out


def historical_events(root: Path, night: int, track: str) -> list[dict[str, Any]]:
    """A closed night's ledger rows in the shape a live run emits, so one timeline renders both.

    The first attempt returned the ledger's own field names, and the page — written against the live stream —
    showed a column of "Invalid Date" with no content. A historical run and a live one must speak the same
    event language or the page has to know which it is looking at.
    """
    from pravrudhi_kernel.ledger.verify import iter_events

    ledger = Path(root) / "research" / "ledger.jsonl"
    if not ledger.exists():
        return []
    out: list[dict[str, Any]] = []
    for ev in iter_events(ledger):
        if ev.night != night:
            continue
        payload = ev.payload or {}
        kind = str(payload.get("kind") or ev.kind)
        cid = ev.candidate_id
        if ev.kind == "observe" and cid:
            # The measurement lives under `observed`, not at the payload's top level; reading the top level gave
            # a timeline of "evaluated over null problems".
            observed = payload.get("observed") or {}
            score = observed.get("value")
            delta = observed.get("delta_in")
            incumbent = None
            if isinstance(score, int | float) and isinstance(delta, int | float):
                incumbent = round(float(score) - float(delta), 6)
            out.append({
                "type": "paired", "candidate": cid, "seq": ev.seq,
                "candidate_score": score, "delta": delta, "incumbent": incumbent,
                "n": observed.get("n_items"), "decision": payload.get("stage"),
                "seed": (observed.get("seeds") or [None])[0],
            })
        elif kind == "promoted" and cid:
            out.append({"type": "promoted", "candidate": cid, "seq": ev.seq})
        elif kind in ("pruned", "prune") and cid:
            out.append({"type": "pruned", "candidate": cid, "seq": ev.seq})
        elif kind == "propose" or ev.kind == "propose":
            out.append({"type": "proposed_one", "candidate": cid, "seq": ev.seq})
        elif kind == "night_end":
            outcomes = payload.get("outcomes") or {}
            out.append({
                "type": "closed", "seq": ev.seq,
                "text": (f"night {night} closed: {sum(1 for v in outcomes.values() if v == 'promoted')} promoted, "
                         f"{sum(1 for v in outcomes.values() if v == 'pruned')} pruned, "
                         f"{payload.get('spent_gpu_h', 0):.3f} GPU-hours spent"),
            })
        else:
            summary = ", ".join(f"{k} {v}" for k, v in list(payload.items())[:3] if not isinstance(v, dict | list))
            out.append({"type": "log", "seq": ev.seq, "text": f"{kind}: {summary}" if summary else kind})
    return out[-200:]



# One manager per project root, kept because a manager holds the record of what is running in that project and
# two objects for one project would be two partial records.
#
# `RunManager` was always per-project — it passes `--root` to the CLI, resolves the next night from that
# project's ledger, and runs with it as the working directory. It was simply constructed once with the engine's
# own root and closed over by every route, so starting work meant starting it on the operator's project with the
# operator's hardware and keys, whoever asked. That is why these routes were held back from the product.
_MANAGERS: dict[Path, RunManager] = {}


def managers_for_testing() -> dict[Path, RunManager]:
    """The cache, so a test can start from an empty one. Not for use outside tests."""
    return _MANAGERS


def manager_for(user: User | None, workspace: str | None, *, engine_root: Path) -> RunManager:
    """The run manager for whoever is asking, in whichever project the request is about.

    The refusal that governs every other user-facing surface governs this one: an operator with no workspace
    named gets the engine's project, a user must name theirs, and nobody falls back to somebody else's.
    """
    from pravrudhi.api.workspace_root import root_for

    here = root_for(user, workspace, engine_root=engine_root).resolve()
    if here not in _MANAGERS:
        _MANAGERS[here] = RunManager(here)
    return _MANAGERS[here]


def build_router(root: Path) -> APIRouter:
    r = APIRouter(prefix="/api")

    def _mgr(user: User | None, workspace: str | None) -> RunManager:
        from pravrudhi.api.workspace_root import RootError

        try:
            return manager_for(user, workspace, engine_root=root)
        except RootError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    @r.post("/runs", response_model=RunView)
    def start(
        req: RunRequest, workspace: str | None = None, user: User | None = CurrentUserDep
    ) -> dict[str, Any]:
        return _mgr(user, workspace).start(req).view()

    @r.get("/runs", response_model=RunsResponse)
    def list_runs(
        workspace: str | None = None, user: User | None = CurrentUserDep
    ) -> list[dict[str, Any]]:
        """Every run this engine has performed, live ones and the nights already in the ledger.

        The manager only knows runs started through the app in this process, so a workspace with twenty-three
        closed nights behind it answered "no runs yet" — the page told a visitor nothing had ever happened here.
        A closed night is a run that finished; it belongs in the same list, marked as such, and its detail page
        renders from the ledger rather than from a live event stream.
        """
        mgr = _mgr(user, workspace)
        here = mgr.root
        live = [run.view() for run in sorted(mgr.runs.values(), key=lambda x: -x.started_at)]
        seen = {row["id"] for row in live}
        flight = [row for row in _in_flight(here) if row["id"] not in seen]
        seen |= {row["id"] for row in flight}
        return live + flight + [row for row in historical_runs(here) if row["id"] not in seen]

    @r.get("/runs/{run_id}", response_model=RunView)
    def get_run(
        run_id: str, workspace: str | None = None, user: User | None = CurrentUserDep
    ) -> dict[str, Any]:
        """A live run's detail, or a closed night's, at the same address.

        The manager knows only runs started through the app in this process, so every historical night in the
        listing answered 404 when opened. A link to a run must always work, whether the run is happening now or
        finished a week ago.
        """
        mgr = _mgr(user, workspace)
        here = mgr.root
        if run_id.startswith("night-"):
            row = next((x for x in [*_in_flight(here), *historical_runs(here)] if x["id"] == run_id), None)
            if row is not None:
                return {**row, "recent": historical_events(here, row["night"], str(row["target"]))}
        run = mgr.get(run_id)
        return {**run.view(), "recent": list(run.events)[-50:]}

    @r.post("/runs/{run_id}/stop", response_model=RunView)
    def stop_run(
        run_id: str, workspace: str | None = None, user: User | None = CurrentUserDep
    ) -> dict[str, Any]:
        return _mgr(user, workspace).stop(run_id).view()

    @r.get("/runs/{run_id}/events", response_model=RunEventsResponse)
    def events(
        run_id: str, workspace: str | None = None, user: User | None = CurrentUserDep
    ) -> StreamingResponse:
        return StreamingResponse(_mgr(user, workspace).stream(run_id), media_type="text/event-stream")

    @r.get("/models", response_model=PromotedModelsResponse)
    def models(
        workspace: str | None = None, user: User | None = CurrentUserDep
    ) -> list[dict[str, Any]]:
        """What this project's loop produced. A user sees their own promotions, not the operator's."""
        return models_listing(_mgr(user, workspace).root)

    return r
