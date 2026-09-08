"""What the operator actually asked for, kept where it cannot be quietly forgotten.

An ask arrives in conversation and lives only in a transcript. Work drifts towards what is easy, a later session
inherits no record of what was wanted, and the operator has to notice the omission himself and say it twice. This
module makes the ask a first-class object: captured verbatim with its date, broken into criteria that can each be
checked, and closable only against evidence.

Two rules give it teeth, both chosen by the operator on 2026-09-06.

The first is that a request cannot be marked delivered by assertion. `deliver` refuses unless every acceptance
criterion carries evidence — a commit, a ledger sequence, a file, or a command whose output was seen. Saying a
thing is done is not evidence that it is.

The second is that an unaddressed request gets louder. `staleness` grows with the days a captured request has sat
untouched, and `next_unmet` hands the heartbeat the one that has waited longest, so the loop works the backlog
without being asked to.

Nothing here interprets the operator. The verbatim text is stored unmodified and every criterion records who
wrote it, so a criterion invented by the engine can never be mistaken for something the operator said.
"""

from __future__ import annotations

import contextlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

State = Literal["captured", "clarified", "planned", "in_progress", "delivered", "verified", "declined"]

STATES: tuple[State, ...] = (
    "captured", "clarified", "planned", "in_progress", "delivered", "verified", "declined",
)

# Which moves are legal. A request may be declined from anywhere, because the honest answer to some asks is no
# with a reason; it may never go from captured straight to verified, because that is how work gets skipped.
TRANSITIONS: dict[State, tuple[State, ...]] = {
    "captured": ("clarified", "planned", "in_progress", "declined"),
    "clarified": ("planned", "in_progress", "declined"),
    "planned": ("in_progress", "declined"),
    "in_progress": ("delivered", "planned", "declined"),
    "delivered": ("verified", "in_progress", "declined"),
    "verified": ("in_progress",),
    "declined": ("captured",),
}

EVIDENCE_KINDS = ("commit", "ledger_seq", "file", "command", "screenshot", "url")


class RequestError(RuntimeError):
    """A move that would lose the record's meaning: an illegal transition, or a close without evidence."""


@dataclass(frozen=True)
class Evidence:
    """One fact that supports a criterion. `ref` is a commit hash, a ledger sequence, a path, or a command."""

    kind: str
    ref: str
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "ref": self.ref, "note": self.note}


@dataclass
class Criterion:
    """One checkable part of an ask. `source` records who wrote it: the operator, or the engine's reading of him."""

    text: str
    source: Literal["operator", "engine"] = "engine"
    met: bool = False
    evidence: list[Evidence] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text, "source": self.source, "met": self.met,
            "evidence": [e.to_dict() for e in self.evidence],
        }

    @staticmethod
    def from_dict(d: dict[str, Any]) -> Criterion:
        return Criterion(
            text=str(d.get("text", "")),
            source="operator" if d.get("source") == "operator" else "engine",
            met=bool(d.get("met", False)),
            evidence=[Evidence(str(e.get("kind", "")), str(e.get("ref", "")), str(e.get("note", "")))
                      for e in (d.get("evidence") or [])],
        )


@dataclass
class Request:
    id: str
    asked_at: str
    text: str
    """The operator's words, unmodified. Never rewritten, summarised, or 'cleaned up'."""
    state: State = "captured"
    criteria: list[Criterion] = field(default_factory=list)
    notes: list[dict[str, str]] = field(default_factory=list)
    session: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "asked_at": self.asked_at, "text": self.text, "state": self.state,
            "criteria": [c.to_dict() for c in self.criteria], "notes": self.notes, "session": self.session,
        }

    @staticmethod
    def from_dict(d: dict[str, Any]) -> Request:
        state = d.get("state")
        return Request(
            id=str(d.get("id", "")),
            asked_at=str(d.get("asked_at", "")),
            text=str(d.get("text", "")),
            state=state if state in STATES else "captured",
            criteria=[Criterion.from_dict(c) for c in (d.get("criteria") or [])],
            notes=list(d.get("notes") or []),
            session=str(d.get("session", "")),
        )

    @property
    def open(self) -> bool:
        return self.state not in ("verified", "declined")

    def unmet(self) -> list[Criterion]:
        return [c for c in self.criteria if not c.met]

    def progress(self) -> tuple[int, int]:
        return sum(1 for c in self.criteria if c.met), len(self.criteria)


def store_path(root: Path) -> Path:
    return Path(root) / ".pravrudhi" / "requests.json"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def load(root: Path) -> list[Request]:
    """Every request, oldest first. A corrupt file is an empty backlog, not a crash on start-up."""
    path = store_path(root)
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return []
    rows = raw.get("requests") if isinstance(raw, dict) else raw
    if not isinstance(rows, list):
        return []
    out = [Request.from_dict(r) for r in rows if isinstance(r, dict)]
    return sorted(out, key=lambda r: r.asked_at)


def save(root: Path, requests: list[Request]) -> Path:
    path = store_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"requests": [r.to_dict() for r in requests]}, indent=2, sort_keys=False))
    tmp.replace(path)
    return path


def capture(
    root: Path, text: str, *, asked_at: str | None = None, criteria: list[Criterion] | None = None,
    session: str = "", request_id: str | None = None,
) -> Request:
    """Record an ask. Idempotent on the verbatim text, so re-running a seed does not duplicate the backlog."""
    existing = load(root)
    for r in existing:
        if r.text.strip() == text.strip():
            return r
    req = Request(
        id=request_id or f"r-{uuid.uuid4().hex[:8]}",
        asked_at=asked_at or _now(),
        text=text,
        criteria=criteria or [],
        session=session,
    )
    existing.append(req)
    save(root, existing)
    return req


def get(root: Path, request_id: str) -> Request | None:
    return next((r for r in load(root) if r.id == request_id), None)


def _replace(root: Path, req: Request) -> Request:
    rows = load(root)
    for i, r in enumerate(rows):
        if r.id == req.id:
            rows[i] = req
            break
    else:
        rows.append(req)
    save(root, rows)
    return req


def advance(root: Path, request_id: str, state: State, *, note: str = "") -> Request:
    """Move a request along, refusing a move the state machine does not allow."""
    req = get(root, request_id)
    if req is None:
        raise RequestError(f"no request {request_id}")
    if state == req.state:
        return req
    if state not in TRANSITIONS.get(req.state, ()):
        raise RequestError(
            f"{request_id} cannot go from {req.state} to {state}; allowed: {', '.join(TRANSITIONS[req.state])}"
        )
    if state in ("delivered", "verified"):
        missing = req.unmet()
        if not req.criteria:
            raise RequestError(f"{request_id} has no acceptance criteria, so there is nothing to have delivered")
        if missing:
            raise RequestError(
                f"{request_id} still has {len(missing)} unmet criterion(s): " + "; ".join(c.text for c in missing[:3])
            )
    req.state = state
    req.notes.append({"at": _now(), "note": note or f"-> {state}"})
    return _replace(root, req)


def add_criteria(root: Path, request_id: str, criteria: list[Criterion]) -> Request:
    req = get(root, request_id)
    if req is None:
        raise RequestError(f"no request {request_id}")
    req.criteria.extend(criteria)
    return _replace(root, req)


def meet(root: Path, request_id: str, index: int, evidence: list[Evidence]) -> Request:
    """Mark one criterion met. Refuses without evidence: an assertion is not a fact."""
    req = get(root, request_id)
    if req is None:
        raise RequestError(f"no request {request_id}")
    if not 0 <= index < len(req.criteria):
        raise RequestError(f"{request_id} has no criterion {index}")
    if not evidence:
        raise RequestError("a criterion is met by evidence, not by assertion; supply at least one reference")
    bad = [e for e in evidence if e.kind not in EVIDENCE_KINDS]
    if bad:
        raise RequestError(f"unknown evidence kind(s): {', '.join(sorted({e.kind for e in bad}))}")
    req.criteria[index].met = True
    req.criteria[index].evidence.extend(evidence)
    # A criterion that moved has not stalled, so its attempt budget is spent honestly and returned. Clearing here
    # rather than in the heartbeat means any route to "met" resets it, not only the one the loop happens to take.
    with contextlib.suppress(Exception):
        from pravrudhi.application.heartbeat import clear_attempts

        clear_attempts(root, request_id, index)
    return _replace(root, req)


def drop_criterion(root: Path, request_id: str, index: int) -> Request:
    """Remove one criterion. For a demand that cannot be answered because it does not state anything.

    The completion gate turns a review's finding into a criterion. An early version of that extraction took the
    first non-heading line, and on a review opening "Summary of the strongest reason:" it wrote a criterion with
    the demand missing. The extraction was fixed; the criterion it had already written stayed, unanswerable, and
    the loop dispatched an agent at it nine times in one day before an attempt budget stopped it.

    This removes the criterion and nothing else, so the gate can re-run and write a well-formed one in its place.
    It is not a way to dismiss a demand that is merely hard: a criterion that states something and has not been
    met should stay unmet.
    """
    req = get(root, request_id)
    if req is None:
        raise RequestError(f"no request {request_id}")
    if not 0 <= index < len(req.criteria):
        raise RequestError(f"{request_id} has no criterion {index}")
    with contextlib.suppress(Exception):
        from pravrudhi.application.heartbeat import clear_attempts

        clear_attempts(root, request_id, index)
    del req.criteria[index]
    return _replace(root, req)


def retract_evidence(root: Path, request_id: str, index: int, ref: str) -> Request:
    """Remove a reference that does not verify, and un-meet the criterion if it was the last one.

    A reference can be wrong without the work being undone — a commit hash naming the publish rather than the
    code, a command written as illustrative shorthand, a path to a file since deleted. Correcting it must be
    possible. Making a criterion pass by deleting the evidence that failed must not be, so a criterion left with
    no evidence returns to unmet.
    """
    req = get(root, request_id)
    if req is None:
        raise RequestError(f"no request {request_id}")
    if not 0 <= index < len(req.criteria):
        raise RequestError(f"{request_id} has no criterion {index}")
    criterion = req.criteria[index]
    kept = [e for e in criterion.evidence if e.ref != ref]
    if len(kept) == len(criterion.evidence):
        raise RequestError(f"{request_id}[{index}] carries no evidence with ref {ref!r}")
    criterion.evidence = kept
    if not kept:
        criterion.met = False
    return _replace(root, req)


def staleness(req: Request, *, now: datetime | None = None) -> float:
    """Days an open request has waited. Closed work is never stale; this is what makes drift visible."""
    if not req.open:
        return 0.0
    moment = now or datetime.now(UTC)
    try:
        asked = datetime.fromisoformat(req.asked_at.replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    if asked.tzinfo is None:
        asked = asked.replace(tzinfo=UTC)
    return max(0.0, (moment - asked) / timedelta(days=1))


def next_unmet(root: Path, *, now: datetime | None = None) -> tuple[Request, Criterion, int] | None:
    """The oldest open request with an unmet criterion, and which criterion to work on.

    This is what the heartbeat calls. Ordering by staleness rather than by arrival keeps a request that was
    started and abandoned from sitting behind one that was never touched.
    """
    best: tuple[float, Request, Criterion, int] | None = None
    for req in load(root):
        if not req.open:
            continue
        for i, c in enumerate(req.criteria):
            if c.met:
                continue
            age = staleness(req, now=now)
            if best is None or age > best[0]:
                best = (age, req, c, i)
            break
    return None if best is None else (best[1], best[2], best[3])


def next_obligation(root: Path, *, now: datetime | None = None) -> dict[str, Any] | None:
    """The next thing owed to the operator, and what kind of work it is.

    Counting unmet criteria is not the whole obligation. Once every criterion on a request carries evidence there
    is nothing left to build, and the request is still owed until it has been through the gate. The appetite said
    it was working on "the oldest unmet request criterion" while the loop reported there was none, because each
    was reading a different half of this. One answer now serves both.
    """
    unmet = next_unmet(root, now=now)
    if unmet is not None:
        req, criterion, index = unmet
        return {
            "kind": "meet_criterion", "request": req.id, "criterion": index, "text": criterion.text,
            "description": f"the oldest unmet criterion on {req.id}",
        }
    open_rows = [r for r in load(root) if r.open and r.criteria]
    if not open_rows:
        return None
    ready = sorted(open_rows, key=lambda r: -staleness(r, now=now))[0]
    if ready.state == "delivered":
        return {
            "kind": "verify_request", "request": ready.id, "criterion": None, "text": "",
            "description": f"the completion gate on {ready.id}",
        }
    return {
        "kind": "advance_request", "request": ready.id, "criterion": None, "text": "",
        "description": f"the next step on {ready.id}, still {ready.state}",
    }


def backlog(root: Path, *, now: datetime | None = None) -> dict[str, Any]:
    """What is outstanding, in the shape the interface and the CLI both render."""
    rows = load(root)
    open_rows = [r for r in rows if r.open]
    return {
        "total": len(rows),
        "open": len(open_rows),
        "by_state": {s: sum(1 for r in rows if r.state == s) for s in STATES},
        "oldest_open_days": round(max((staleness(r, now=now) for r in open_rows), default=0.0), 2),
        "requests": [
            {**r.to_dict(), "staleness_days": round(staleness(r, now=now), 2), "progress": list(r.progress())}
            for r in sorted(rows, key=lambda r: (not r.open, -staleness(r, now=now)))
        ],
    }


__all__ = [
    "Criterion", "Evidence", "Request", "RequestError", "STATES", "TRANSITIONS",
    "add_criteria", "advance", "backlog", "capture", "get", "load", "meet", "next_obligation",
    "next_unmet",
    "retract_evidence", "save",
    "staleness", "store_path",
]
