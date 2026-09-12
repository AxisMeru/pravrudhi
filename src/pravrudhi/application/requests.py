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
import fcntl
import json
import re
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field, replace
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
    """One checkable part of an ask. `source` records who wrote it: the operator, or the engine's reading of him.

    `mode` determines dispatch policy: "proposal" (default) sends to a scratch directory with read-only sandbox,
    or "build" (when paths are named and files are code) dispatches with selfbuild policy and integration on MET.

    `declined` is a criterion-level "no": unlike `drop_criterion` (which erases a malformed criterion so the
    completion gate can redraft a good one in its place) this keeps the criterion, its text, and the reason it
    will not be pursued, all visible in `show` - the record is corrected without anything disappearing from it.
    `unmet()` and `next_unmet()` both treat a declined criterion as settled, so the loop stops paying to
    dispatch it without anyone marking it (falsely) met."""

    text: str
    source: Literal["operator", "engine"] = "engine"
    met: bool = False
    declined: bool = False
    evidence: list[Evidence] = field(default_factory=list)
    mode: Literal["proposal", "build"] = "proposal"

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text, "source": self.source, "met": self.met, "declined": self.declined,
            "evidence": [e.to_dict() for e in self.evidence], "mode": self.mode,
        }

    @staticmethod
    def from_dict(d: dict[str, Any]) -> Criterion:
        mode = d.get("mode", "proposal")
        return Criterion(
            text=str(d.get("text", "")),
            source="operator" if d.get("source") == "operator" else "engine",
            met=bool(d.get("met", False)),
            declined=bool(d.get("declined", False)),
            evidence=[Evidence(str(e.get("kind", "")), str(e.get("ref", "")), str(e.get("note", "")))
                      for e in (d.get("evidence") or [])],
            mode="build" if mode == "build" else "proposal",
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
        return [c for c in self.criteria if not c.met and not c.declined]

    def progress(self) -> tuple[int, int]:
        return sum(1 for c in self.criteria if c.met), len(self.criteria)


def store_path(root: Path) -> Path:
    return Path(root) / ".pravrudhi" / "requests.json"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


@contextlib.contextmanager
def locked(root: Path) -> Iterator[None]:
    """Exclusive access to this project's request store for one whole read-modify-write.

    Every public mutator below holds this for its entire body, not just around `save`: a function that reads
    the current request, decides a new value from it, and only locks the final write can still overwrite a
    change a second caller made in between. The CLI (`pravrudhi requests set-mode`/`mark-met`/`decline`) and
    the heartbeat's own beat both call these same functions, and an operator flipping a criterion by hand while
    the loop is mid-beat is exactly the race this closes - the operator asked for a tool because a hand-edit
    with no lock at all had already cost a beat once.

    Blocking (not the non-blocking `LOCK_NB` `loom_run.py` uses for a long-running pipeline record) because a
    CLI edit and a beat's write are each individually fast: either can simply wait its turn, and refusing one
    outright would just relocate the race to "try again," which is what editing the file by hand already was.
    """
    path = store_path(root).with_suffix(".json.lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


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
    with locked(root):
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
    with locked(root):
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
                    f"{request_id} still has {len(missing)} unmet criterion(s): "
                    + "; ".join(c.text for c in missing[:3])
                )
            if not any(c.met for c in req.criteria):
                # Every criterion is settled (`unmet()` is empty) but none is actually met - the request's
                # criteria were all declined. Declining everything is a "no", not a delivery.
                raise RequestError(
                    f"{request_id} has no criterion actually marked met (every one is declined); "
                    "decline the request itself instead of calling this delivered"
                )
        req.state = state
        req.notes.append({"at": _now(), "note": note or f"-> {state}"})
        return _replace(root, req)


def note(root: Path, request_id: str, text: str) -> Request:
    """Record something learned about a request without moving its state.

    `advance` carries a note, which made every note a state change: the only way to write down why an attempt
    fell short was to pretend the request had moved.
    """
    with locked(root):
        req = get(root, request_id)
        if req is None:
            raise RequestError(f"no request {request_id}")
        return _replace(root, replace(req, notes=[*req.notes, {"at": _now(), "note": text}]))


def add_criteria(root: Path, request_id: str, criteria: list[Criterion]) -> Request:
    with locked(root):
        req = get(root, request_id)
        if req is None:
            raise RequestError(f"no request {request_id}")
        req.criteria.extend(criteria)
        return _replace(root, req)


def meet(
    root: Path, request_id: str, index: int, evidence: list[Evidence], *,
    why: str = "", actor: str = "agent-for-operator",
) -> Request:
    """Mark one criterion met. Refuses without evidence: an assertion is not a fact.

    `why`/`actor` are for a caller marking this by hand (`pravrudhi requests mark-met`): given a reason, it
    lands as a dated note alongside the state change, naming who did it and why. The heartbeat's own automatic
    path calls this with neither, exactly as it always has - a note only appears when there is a human decision
    behind it worth recording.
    """
    with locked(root):
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
        if req.criteria[index].declined:
            raise RequestError(f"{request_id}[{index}] was declined; nothing declined can be marked met")
        req.criteria[index].met = True
        req.criteria[index].evidence.extend(evidence)
        if why.strip():
            refs = ", ".join(f"{e.kind}:{e.ref}" for e in evidence)
            req.notes.append({
                "at": _now(), "note": f"{actor}: criterion[{index}] met ({refs}) ({why.strip()})",
            })
        # A criterion that moved has not stalled, so its attempt budget is spent honestly and returned. Clearing
        # here rather than in the heartbeat means any route to "met" resets it, not only the one the loop takes.
        with contextlib.suppress(Exception):
            from pravrudhi.application.heartbeat import clear_attempts

            clear_attempts(root, request_id, index)
        return _replace(root, req)


def set_mode(
    root: Path, request_id: str, index: int, mode: Literal["proposal", "build"], *,
    why: str, actor: str = "agent-for-operator",
) -> Request:
    """Flip one criterion's dispatch mode by hand, with the reason kept on the request.

    `_drafted_mode` decides this once, at draft time, and the heartbeat trusts what it stored from then on -
    which means a criterion drafted before the text-shape detector existed, or one whose text simply evades it,
    stays stuck in the wrong mode until a person corrects the record directly. Before this, that correction was
    a hand-edit of `.pravrudhi/requests.json` with no note explaining why and no lock against the heartbeat
    writing the same file at the same time; this is that correction, done properly.
    """
    if mode not in ("proposal", "build"):
        raise RequestError(f"unknown mode {mode!r}; expected proposal or build")
    if not why.strip():
        raise RequestError("set-mode requires a reason; that is the whole point of keeping the note")
    with locked(root):
        req = get(root, request_id)
        if req is None:
            raise RequestError(f"no request {request_id}")
        if not 0 <= index < len(req.criteria):
            raise RequestError(f"{request_id} has no criterion {index}")
        criterion = req.criteria[index]
        old = criterion.mode
        if old == mode:
            return req
        criterion.mode = mode
        req.notes.append({
            "at": _now(), "note": f"{actor}: criterion[{index}] mode {old} -> {mode} ({why.strip()})",
        })
        return _replace(root, req)


def decline_criterion(
    root: Path, request_id: str, index: int, *, why: str, actor: str = "agent-for-operator",
) -> Request:
    """Say no to one criterion without touching the rest of the request.

    Unlike `drop_criterion` (which erases a criterion the completion gate wrote wrong, so it can redraft a good
    one in its place) this keeps the criterion on the record, with the reason it will not be pursued. `unmet()`
    and `next_unmet()` both skip a declined criterion, so the loop stops paying to dispatch it, and `advance`
    no longer counts it toward "nothing left to build" - but also refuses to call the request delivered on the
    strength of criteria that were only ever declined, never met.
    """
    if not why.strip():
        raise RequestError("decline requires a reason; that is the whole point of keeping the note")
    with locked(root):
        req = get(root, request_id)
        if req is None:
            raise RequestError(f"no request {request_id}")
        if not 0 <= index < len(req.criteria):
            raise RequestError(f"{request_id} has no criterion {index}")
        criterion = req.criteria[index]
        if criterion.met:
            raise RequestError(f"{request_id}[{index}] is already met; declining it would erase real evidence")
        criterion.declined = True
        with contextlib.suppress(Exception):
            from pravrudhi.application.heartbeat import clear_attempts

            clear_attempts(root, request_id, index)
        req.notes.append({
            "at": _now(), "note": f"{actor}: criterion[{index}] declined ({why.strip()})",
        })
        return _replace(root, req)


def drop_criterion(root: Path, request_id: str, index: int) -> Request:
    """Remove one criterion. For a demand that cannot be answered because it does not state anything.

    The completion gate turns a review's finding into a criterion. An early version of that extraction took the
    first non-heading line, and on a review opening "Summary of the strongest reason:" it wrote a criterion with
    the demand missing. The extraction was fixed; the criterion it had already written stayed, unanswerable, and
    the loop dispatched an agent at it nine times in one day before an attempt budget stopped it.

    This removes the criterion and nothing else, so the gate can re-run and write a well-formed one in its place.
    It is not a way to dismiss a demand that is merely hard: a criterion that states something and has not been
    met should stay unmet. (For that, see `decline_criterion`, which keeps the record instead of erasing it.)
    """
    with locked(root):
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
    with locked(root):
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


def _parked(root: Path, request_id: str, index: int) -> bool:
    """Whether this criterion has spent its attempt budget.

    Lazily imported because `heartbeat` imports this module, and because the budget is a property of dispatching
    rather than of the ledger: a workspace that has never dispatched has parked nothing.
    """
    with contextlib.suppress(Exception):
        from pravrudhi.application.heartbeat import stalled

        return bool(stalled(root, request_id, index))
    return False


def next_unmet(
    root: Path, *, now: datetime | None = None, exclude: frozenset[str] = frozenset(),
) -> tuple[Request, Criterion, int] | None:
    """The oldest open request with an unmet criterion, and which criterion to work on.

    This is what the heartbeat calls. Ordering by staleness rather than by arrival keeps a request that was
    started and abandoned from sitting behind one that was never touched.

    A criterion that has spent its attempt budget is skipped rather than offered. The budget stopped the loop
    paying for the same dispatch hourly, but selection went on naming it, so on 2026-09-09 both engines reported
    the identical choice for six consecutive beats and did nothing else for days while 28 captured asks waited
    behind it. Declining to pay for a thing and declining to look past it are two decisions, and only the first
    had been made. Returning `None` once everything is parked is what lets the beat fall through to another
    drive instead of re-choosing a dead end.

    `exclude` (S6) is request ids to skip entirely, not just their current head criterion - a beat widening
    past one dispatch calls this again for a SECOND, DIFFERENT request, and excluding by id (rather than only
    skipping the one criterion just picked) is what keeps every additional pick on its own request.
    """
    best: tuple[float, Request, Criterion, int] | None = None
    for req in load(root):
        if not req.open or req.id in exclude:
            continue
        for i, c in enumerate(req.criteria):
            if c.met or c.declined:
                continue
            if _parked(root, req.id, i):
                continue  # spent its budget; it must not hide the work behind it, here or on its own request
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
    # Staleness orders the whole backlog, not just its head. Taking `[0]` and stopping meant that when the
    # stalest row was parked, every beat reported it parked and nothing else was ever looked at: measured on
    # 2026-09-10, five consecutive beats chose `r-5795501a` while `r-cad91781` sat with 0 of 13 criteria unmet,
    # one step from its completion gate, 0.1 of a staleness point behind. A selector that returns work it
    # cannot act on, hourly, is not selecting. So a parked row is remembered and the scan continues; it is
    # reported only when nothing anywhere can move.
    parked: dict[str, Any] | None = None
    for ready in sorted(open_rows, key=lambda r: -staleness(r, now=now)):
        found = _obligation_for(ready, root)
        if found["kind"] == "parked_request":
            parked = parked or found
            continue
        return found
    return parked


def _obligation_for(ready: Request, root: Path) -> dict[str, Any]:
    """What one open request is owed: its gate, its parked criteria, or its next step."""
    if ready.state == "delivered":
        # Check if the gate is stalled before offering it
        with contextlib.suppress(Exception):
            from pravrudhi.application.heartbeat import gate_stalled
            if gate_stalled(root, ready.id):
                return {
                    "kind": "parked_request", "request": ready.id, "criterion": None, "text": "",
                    "description": f"the completion gate on {ready.id} has stalled; this is owed to a person",
                }
        return {
            "kind": "verify_request", "request": ready.id, "criterion": None, "text": "",
            "description": f"the completion gate on {ready.id}",
        }
    still_unmet = ready.unmet()
    if still_unmet:
        # `next_unmet` offered nothing, so every unmet criterion here has spent its attempt budget. That is
        # not "nothing left to build" -- it is work owed to a person -- and calling it ready was the defect
        # that took both loops down on 2026-09-10: this function said advance, `advance` refused because the
        # criteria are unmet, and the unhandled RequestError killed the beat every hour. Two definitions of
        # done disagreed; there is now one, and it is `unmet()`, the same one the guard uses.
        return {
            "kind": "parked_request", "request": ready.id, "criterion": None,
            "text": "; ".join(c.text for c in still_unmet[:3]),
            "description": (
                f"{len(still_unmet)} parked criterion(s) on {ready.id}: every attempt budget is spent, so "
                "this is owed to a person rather than to another dispatch"
            ),
        }
    return {
        "kind": "advance_request", "request": ready.id, "criterion": None, "text": "",
        "description": f"the next step on {ready.id}, still {ready.state}",
    }


_CRITERION_CHARS = 1000
"""Where a drafted criterion is cut. It was 300, and every criterion a model drafted on 2026-09-11 came out
exactly 300 characters long, one of them ending in `docs/usage.` with its closing backtick gone, so the build
scope never included docs and three dispatches were refused for writing the file the criterion named. A criterion
is a sentence the judge must read whole; 1000 is room for one, and `_clip` cuts at a boundary, never inside a
backticked name."""


def _clip(text: str, limit: int = _CRITERION_CHARS) -> str:
    """`text` within `limit`, cut at the last sentence end or whitespace before it, and never leaving a
    backtick unpaired: a half name is worse than no name, because the scope parser would read it as prose."""
    text = text.strip()
    if len(text) <= limit:
        return text
    head = text[:limit]
    cut = max(head.rfind(". "), head.rfind("; "), head.rfind(", "), head.rfind(" "))
    if cut > limit // 2:
        head = head[:cut].rstrip(" ,;")
    if head.count("`") % 2:
        head = head[: head.rfind("`")].rstrip(" ,;:")
    return head.rstrip(".") + "..." if not head.endswith(".") else head

# A digit, then ")" or ". ", at the start of the ask or after whitespace. The operator enumerates constantly
# ("1)do local schedule task 2)you have to unblock studio"), sometimes without a space after the bracket. A "."
# marker requires trailing whitespace so that "3.5" is a number rather than a third item.
_ENUMERATED = re.compile(r"(?:^|(?<=\s))\d{1,2}(?:\)\s*|\.\s+)")


def _drafted_mode(text: str) -> Literal["proposal", "build"]:
    """Whether a freshly-drafted criterion is build-shaped, decided once at draft time rather than left for
    every later beat to re-derive from a fixed (and possibly clipped) text.

    r-9c8646fc's survey of the open backlog found 231 of 497 unmet `proposal`-mode criteria already dispatch as
    build anyway, because `heartbeat.dispatch_mode` re-runs this same detection on any criterion whose `mode`
    was never set to `"build"` explicitly - which is every drafted criterion, since neither `draft_criteria` nor
    `decompose_ask` ever wrote one. That the guess and the later re-guess happen to agree most of the time is
    not the same as deciding once: the persisted field should say what the loop will actually do with it, so a
    reader of the requests store (a human, or `heartbeat.build_paths_for` operating on the record instead of
    the live text) is not left to simulate dispatch to find out.
    """
    with contextlib.suppress(Exception):
        from pravrudhi.application.heartbeat import dispatch_mode

        return "build" if dispatch_mode(Criterion(text=text)) == "build" else "proposal"
    return "proposal"


# Three shapes r-9c8646fc's survey found among criteria `draft_criteria`'s verbatim fallback had accepted with no
# filter at all (unlike `decompose_ask`, whose `_names_something` gate only ever applies to a MODEL's answer):
# a re-pasted cross-session handoff (r-e08d27fa, r-4fa9b6c7, r-bc88c187 - an entire forwarded document, not an
# ask about this engine), an instruction to communicate rather than to build anything (r-3772a0d8: "Reply to me
# ... with the key lessons"), and a bare question. None of these can be delivered as evidence against; three
# agents were paid to guess at each anyway.
_PASTED_TRANSCRIPT = re.compile(r"<cross-session-message", re.I)
_COMMUNICATION_ONLY = re.compile(
    r"^\s*(reply to me|tell me|let me know|send_message|message me|write back|get back to me)\b", re.I,
)
_BARE_QUESTION = re.compile(r"\?\s*$")


def _is_actionable_ask(text: str) -> bool:
    """Whether a drafted criterion names something the engine could attempt, rather than a forwarded transcript,
    an instruction to communicate, or a bare question - the discipline `decompose_ask` already applies to a
    model's own answer (`_names_something`), extended to the text `draft_criteria` accepts with no model at all.

    This is deliberately narrow: it catches the recognisable shapes above, not every unclear ask. A criterion
    that names nothing but is not one of these shapes still stands, exactly as before - a human sharpening it
    remains the fallback this cannot replace.
    """
    t = text.strip()
    if not t:
        return False
    if _PASTED_TRANSCRIPT.search(t):
        return False
    if _COMMUNICATION_ONLY.match(t):
        return False
    return not _BARE_QUESTION.search(t)


def draft_criteria(text: str) -> list[Criterion]:
    """Read an ask and write the acceptance criteria for it.

    The operator delegated drafting, so these are `source="engine"` and carry no evidence: drafting decides what
    to attempt, never that anything was done. The completion gate still reviews delivered work against the
    operator's verbatim `Request.text`, so a criterion the engine wrote for itself cannot lower the bar.

    An enumerated ask is split on its own numbering, which is the operator's structure rather than the engine's
    reading of it. Prose becomes one criterion carrying the ask verbatim. Nothing is paraphrased and nothing is
    inferred: a review that split prose on guesswork produced criteria naming nothing, and the three agents that
    picked them up could only guess in turn (see `heartbeat._names_something`). `mode` is decided once here
    (`_drafted_mode`); actionability (`_is_actionable_ask`) is judged by `triage`, which is where a non-actionable
    ask has somewhere to go (`declined`) - this function's contract stays "the criteria this text implies", not
    "the criteria worth keeping".
    """
    ask = (text or "").strip()
    if not ask:
        return []
    if len(_ENUMERATED.findall(ask)) >= 2:  # one marker is a sentence that happens to start with "1)"
        items = [part.strip() for part in _ENUMERATED.split(ask)]
        drafted = [Criterion(text=(c := _clip(i)), source="engine", mode=_drafted_mode(c)) for i in items if i]
        if drafted:
            return drafted
    clipped = _clip(ask)
    return [Criterion(text=clipped, source="engine", mode=_drafted_mode(clipped))]


def untriaged(root: Path, *, now: datetime | None = None) -> list[Request]:
    """Open asks carrying no criteria, oldest first — the ones the loop cannot see.

    `next_obligation` filters on `r.open and r.criteria`, so an ask with none is invisible to every drive. That
    is not a small gap: twenty-eight of thirty-five requests were in this state while the loop reported nothing
    was owed.
    """
    pending = [r for r in load(root) if r.open and not r.criteria]
    return sorted(pending, key=lambda r: -staleness(r, now=now))


_MAX_DRAFTED = 6
"""Enough to cover an ask, few enough that no single beat can bury the backlog under one request."""

CRITERIA_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "criteria": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": _MAX_DRAFTED},
    },
    "required": ["criteria"],
}
"""Constrained decoding rather than prose parsing: llama.cpp compiles this to a grammar, so the array closes."""


def _decompose_prompt(ask: str) -> str:
    """The operator's words reach the model unrewritten; only the instruction around them is ours."""
    return (
        "An operator asked for the following, verbatim:\n\n"
        f"{ask}\n\n"
        "Write the acceptance criteria that would settle whether this ask has been delivered. Each criterion "
        "must name the file, module, command or asset that has to change or hold, in backticks where it is an "
        "identifier — a criterion that only says something should be better cannot be built against or judged. "
        "State what must be true, not how to achieve it. Do not restate the ask. Do not invent measurements, "
        "numbers or results. The engine may only change files under src/pravrudhi/, tests/, app/frontend/src/, "
        "scripts/, configs/ and plugin/. It may NOT change pravrudhi_kernel/ (a kernel change is an ADR the "
        "operator's delegate accepts, not a build), research/ (the ledger and pre-registrations are evidence, "
        "written only by the kernel), gates/, .pravrudhi/, or local-only documents under docs/blueprint/ and "
        "docs/superpowers/. A criterion that can only be met in those places is not a criterion for this engine: "
        "either name the engine-side change that would make it true, or leave it out. Return at most "
        f"{_MAX_DRAFTED} criteria as JSON: "
        '{"criteria": ["...", "..."]}'
    )


def _names_something(line: str) -> bool:
    """The reviewer's test for an actionable finding, applied to the engine's own drafting.

    Lazily imported from `heartbeat`, which owns the definition and the history behind it: a criterion naming
    nothing was handed to three agents that could only guess.
    """
    with contextlib.suppress(Exception):
        from pravrudhi.application.heartbeat import _names_something as names

        return bool(names(line))
    return True  # without the reviewer's rule, trust the draft rather than discard every criterion


def decompose_ask(text: str, *, complete: Callable[[str], str]) -> list[Criterion] | None:
    """Ask a model what would satisfy a prose ask, and keep only the criteria that name something.

    `complete` takes a prompt and returns the model's raw text, which is why this is testable without an
    endpoint. Every failure — an unreachable model, an answer that is not JSON, an answer whose criteria all
    name nothing — returns `[]` and hands the decision back to the caller. A beat must not raise, and a bad
    answer must not become a criterion the loop then pays three times to attempt.
    """
    ask = (text or "").strip()
    if not ask:
        return []
    # `None` and `[]` are different answers. `None`: no model reading exists (unreachable, or an answer that is
    # not the JSON asked for), so the caller keeps its deterministic fallback. `[]`: a model read the ask and
    # named nothing to build against, which the caller may act on. Collapsing the two is how an unreachable
    # endpoint made every ask look non-actionable, and how a non-actionable ask looked like a model outage.
    try:
        answer = complete(_decompose_prompt(ask))
    except Exception:  # noqa: BLE001 - an unreachable model is a fallback, not a failed beat
        return None
    try:
        payload = json.loads(answer or "")
    except (ValueError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None
    items = payload.get("criteria")
    if not isinstance(items, list):
        return None
    drafted: list[Criterion] = []
    for item in items[:_MAX_DRAFTED]:
        line = str(item or "").strip()
        if not line or not _names_something(line):
            continue  # a criterion nobody can build against is worse than one fewer criterion
        clipped = _clip(line)
        drafted.append(Criterion(text=clipped, source="engine", mode=_drafted_mode(clipped)))
    return drafted


def triage(
    root: Path, request_id: str, *, complete: Callable[[str], str] | None = None,
) -> Request | None:
    """Give one captured ask the criteria that let the loop pick it up, or `None` if there is nothing to do.

    An ask that states its own parts is split on them and costs nothing. Prose is put to `complete` when one is
    supplied, because a single criterion repeating a vague ask is a criterion that names nothing. If that
    returns nothing usable the verbatim criterion stands: one criterion the operator can sharpen beats leaving
    the ask invisible to every drive.

    Returning `None` for an ask that already has criteria is what keeps an hourly loop from burying a request
    under its own restatements — the same reason `_criterion_from_finding` allows one open review criterion at a
    time.
    """
    request = get(root, request_id)
    if request is None or request.criteria:
        return None
    drafted = draft_criteria(request.text)
    if not drafted:
        return None
    if complete is not None and len(_ENUMERATED.findall(request.text.strip())) < 2:
        decomposed = decompose_ask(request.text, complete=complete)
        if decomposed is None:
            decomposed = drafted  # no model reading exists; the verbatim criterion is the floor
        elif not decomposed:
            # A model read the ask and could name nothing to build against. The verbatim fallback used to stand
            # here, and it is how a pasted bot-provisioning reply, a forwarded Telegram echo and "done...continue"
            # each became a criterion three agents were paid to attempt. Declining records the reading; the
            # operator can move it back to `captured` with a sharper sentence, and the loop moves on.
            return advance(
                root, request_id, "declined",
                note="triage: the ask names nothing the engine can build against; sharpen it to revive it",
            )
        drafted = decomposed
    # The model path above already declines an unbuildable ask on ITS OWN reading; the verbatim/enumerated
    # fallback (used whenever no model is reachable, or an enumerated ask never goes to one) got no such filter
    # at all until r-9c8646fc's survey found a forwarded handoff document, an instruction to reply rather than
    # build, and other non-deliverables sitting as "criteria" in the open backlog. Filtering per-item rather than
    # declining the whole request keeps whatever in an enumerated ask WAS actionable.
    actionable = [c for c in drafted if _is_actionable_ask(c.text)]
    if not actionable:
        return advance(
            root, request_id, "declined",
            note="triage: the drafted criteria are not something the engine can attempt (a forwarded transcript, "
                 "an instruction to communicate, or a bare question) rather than a deliverable; sharpen it to revive it",
        )
    return add_criteria(root, request_id, actionable)


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
    "add_criteria", "advance", "backlog", "capture", "decline_criterion", "draft_criteria", "drop_criterion",
    "get", "load", "locked", "meet",
    "next_obligation", "set_mode", "triage", "untriaged", "decompose_ask", "CRITERIA_SCHEMA",
    "next_unmet",
    "retract_evidence", "save",
    "staleness", "store_path",
]
