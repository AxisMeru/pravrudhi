"""Look for the shape of a stall, in a system where everything reports success.

On 2026-09-08 seven separate things reported success while doing nothing: an hourly heartbeat over five hours of
rejected dispatches, six green doctor checks, five green parity rows whose evidence checked nothing, a wave
verdict of "no change produced" while the work sat in the wrong tree, eight hours of "running the remedy" that
ran nothing, a sentence asserting a deficit that did not exist, and a night reporting `closed` after spending
0.00 of 3.0 GPU-hours.

Not one was an error. Each was an accurate green over a mechanism that was correct and idle, which is why none
of them tripped anything: there was no exception to catch and no check to fail. Every one was found by a person
reading a log and thinking "that number should not be the same as last time".

So these checks do not look for errors. They look for the shape those failures share — the same choice made
again, a night that cost nothing, work moving from a cheap seat to a dear one — and they are deliberately few.
A watchdog with thirty checks is one nobody reads, and this exists to be read on a phone.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Six identical choices is roughly six hours of an hourly loop. Long enough that a legitimate multi-beat effort
# (a criterion genuinely worth three attempts) does not trip it, short enough to catch a pin inside a working day.
REPEAT_LIMIT = 6


@dataclass(frozen=True)
class Finding:
    """One thing worth a person's attention, in the terms they would act on."""

    kind: str
    severity: str
    detail: str

    def to_dict(self) -> dict[str, str]:
        return {"kind": self.kind, "severity": self.severity, "detail": self.detail}


def _beats(root: Path, n: int = 12) -> list[dict[str, Any]]:
    path = Path(root) / ".pravrudhi" / "heartbeat.jsonl"
    if not path.is_file():
        return []
    out: list[dict[str, Any]] = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            out.append(row)
    return out[-n:]


def _repeating(root: Path) -> list[Finding]:
    """The same decision, beat after beat, is the signature every pin this project has had shared.

    The `pools` remedy was proposed hourly for eight hours and never run; `freshness` replaced it and was chosen
    hourly after that; one request criterion was dispatched nine times in a day and refused every time. In each
    case the loop was working exactly as written and producing nothing, and the only visible tell was that the
    choice never changed.
    """
    beats = _beats(root, REPEAT_LIMIT)
    if len(beats) < REPEAT_LIMIT:
        return []
    keys = {json.dumps(b.get("chose"), sort_keys=True) for b in beats}
    if len(keys) > 1:
        return []
    chose = beats[-1].get("chose")
    if not chose:
        return []
    return [Finding(
        kind="loop_repeating",
        severity="high",
        detail=(
            f"the loop has chosen {json.dumps(chose, sort_keys=True)} on each of the last {REPEAT_LIMIT} beats "
            f"without the choice changing — last reason: {str(beats[-1].get('reason') or '')[:180]}"
        ),
    )]


def _empty_night(root: Path) -> list[Finding]:
    """A night that closed having spent nothing.

    Night 17 reported `status: closed` with 0.00 of 3.0 GPU-hours and no outcomes, which is indistinguishable
    from a good night in every summary that only reads the status. The proposer had been truncated for days.
    """
    ledger = Path(root) / "research" / "ledger.jsonl"
    if not ledger.is_file():
        return []
    last: dict[str, Any] | None = None
    for line in ledger.read_text().splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        payload = row.get("payload") or {}
        if isinstance(payload, dict) and payload.get("kind") == "night_end":
            last = {"night": row.get("night"), **payload}
    if last is None:
        return []
    spent = float(last.get("spent_gpu_h") or 0.0)
    outcomes = last.get("outcomes") or {}
    if spent > 0.0 or outcomes:
        return []
    return [Finding(
        kind="night_spent_nothing",
        severity="high",
        detail=(
            f"night {last.get('night')} closed having spent {spent} of {last.get('budget_gpu_h')} GPU-h with no "
            f"outcomes — it reports as closed, but nothing was measured"
        ),
    )]


def _cheap_seat_down(root: Path) -> list[Finding]:
    """A cheap route sitting out a limit while a dearer one absorbs its work.

    Not a fault — the fallback doing its job — but it is the most expensive thing the engine can fail to mention,
    and it is invisible unless something says it out loud.
    """
    try:
        from pravrudhi.application.roster import roster

        seats = list(roster(Path(root)))
    except Exception:  # noqa: BLE001 - a watchdog that raises is worse than one that misses
        return []
    down = [s for s in seats if not s.usable and not s.sentinel]
    up = [s for s in seats if s.usable and not s.sentinel]
    if not down or not up:
        return []
    cheapest_down = min(down, key=lambda s: s.relative_cost)
    cheapest_up = min(up, key=lambda s: s.relative_cost)
    if cheapest_up.relative_cost <= cheapest_down.relative_cost:
        return []
    return [Finding(
        kind="cheap_seat_down",
        severity="medium",
        detail=(
            f"{cheapest_down.id} ({cheapest_down.relative_cost:.2f}) is sitting out a usage limit, so work is "
            f"going to {cheapest_up.id} ({cheapest_up.relative_cost:.2f})"
            + (f" — back at {cheapest_down.returns_at}" if cheapest_down.returns_at else "")
        ),
    )]


def _parked_criteria(root: Path) -> list[Finding]:
    """Criteria that have spent their attempt budget, read from the attempt record rather than inferred.

    A parked criterion used to reveal itself through `_repeating`: selection kept naming it, so the beats
    repeated and the stall check fired. Once selection learned to skip it (`requests.next_unmet`) the loop
    correctly moved on and the unfinishable work went silent — a better loop and a worse record. Reporting it
    from `heartbeat.attempts` says which criterion, on which request, and what it cost to find out.
    """
    try:
        from pravrudhi.application import requests as reqs
        from pravrudhi.application.heartbeat import attempts, stalled

        rows = reqs.load(Path(root))
    except Exception:  # noqa: BLE001 - a watchdog that raises is worse than one that misses
        return []
    findings: list[Finding] = []
    for request in rows:
        if not request.open:
            continue
        for index, criterion in enumerate(request.criteria):
            if criterion.met or not stalled(Path(root), request.id, index):
                continue
            findings.append(Finding(
                kind="parked_criterion",
                severity="high",
                detail=(
                    f"{request.id} criterion {index} is parked after "
                    f"{attempts(Path(root), request.id, index)} attempts, so the loop has moved on and will not "
                    f"retry it: \"{criterion.text[:160]}\""
                ),
            ))
    return findings


def _stale_install(root: Path) -> list[Finding]:
    """An engine running a release older than the safeguards that were written for it.

    This is the one that cost a week's quota. Every guard against runaway spend - the per-criterion attempt cap,
    the token budget, the cheap seat, reading a token count at all - was written in the development checkout and
    none of it was in release 0.4.0, which is what the end-user install actually runs. So the heaviest consumer
    of the plan ran for a day with no brakes, retried one criterion thirteen times at the dearest seat, and
    recorded zero tokens against every one of them because its code could not read a count.

    Nothing said so. The updater reported "already at 0.4.0" every half hour, truthfully, because 0.4.0 was the
    newest release that existed: the version had never been bumped, so the fixes were finished and unshipped.
    The gap between what is written and what is installed is invisible unless something measures it.
    """
    installed = _installed_version(root)
    packaged = _packaged_version()
    if not installed or not packaged or installed == packaged:
        return []
    return [Finding(
        kind="stale_install",
        severity="high",
        detail=(
            f"this install runs {installed} while the source tree is at {packaged}: any safeguard added since "
            f"{installed} is not running here. Cut a release, or it never reaches the engine that spends."
        ),
    )]


def _installed_version(root: Path) -> str:
    """What this workspace actually runs, read from the release it points at rather than from the source."""
    current = Path(root) / ".pravrudhi" / "releases" / "current"
    if not current.exists():
        return ""
    try:
        return current.resolve().name
    except OSError:
        return ""


def _packaged_version() -> str:
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("pravrudhi")
    except PackageNotFoundError:
        return ""


def check(root: Path) -> list[Finding]:
    """Every check, most serious first. A check that raises is dropped rather than allowed to silence the rest."""
    findings: list[Finding] = []
    for probe in (_repeating, _parked_criteria, _empty_night, _cheap_seat_down, _stale_install):
        try:
            findings.extend(probe(Path(root)))
        except Exception:  # noqa: BLE001
            continue
    order = {"high": 0, "medium": 1, "low": 2}
    return sorted(findings, key=lambda f: (order.get(f.severity, 3), f.kind))


def blind(root: Path) -> bool:
    """Whether this workspace has any record to judge at all.

    The heartbeat log and the ledger are both gitignored, so a fresh clone — which is exactly what a cloud
    routine runs in — has neither. Answering "nothing stalled" there would be this module's own failure mode:
    a green result from a check that could not see anything. The distinction between "well" and "unobserved" is
    the entire point of the file.
    """
    root = Path(root)
    return not (root / ".pravrudhi" / "heartbeat.jsonl").is_file() and not (root / "research" / "ledger.jsonl").is_file()


_ANNOUNCED_FILE = ".pravrudhi/watchdog-announced.json"


def _digest(findings: Iterable[Finding]) -> str:
    """What is wrong, ignoring how it is worded. Two runs of the same problem must hash the same.

    The detail text carries a return time that ticks between runs, so hashing it would make every run look like
    a new problem — which is the bug this exists to stop, wearing a different hat.
    """
    return "|".join(sorted(f"{f.kind}:{f.severity}" for f in findings))


def worth_announcing(root: Path, findings: Iterable[Finding]) -> bool:
    """Whether this is news. True the first time a set of problems appears, and once when they clear.

    The watchdog sent the operator an identical line every thirty minutes: one cooling route, unchanged, over
    and over. Nothing had happened between runs, so nothing needed saying. The first message was the alert and
    every one after it was noise teaching the reader to ignore the channel — which is how a watchdog becomes
    worse than no watchdog, because the silence still looks like coverage.

    Recovery is announced exactly once. Going quiet when a problem clears leaves the reader believing it is
    still broken, and a second all-clear says nothing the first did not.
    """
    current = _digest(findings)
    path = Path(root) / _ANNOUNCED_FILE
    try:
        previous = str(json.loads(path.read_text())["digest"])
    except (OSError, ValueError, KeyError, TypeError):
        previous = ""
    if current == previous:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"digest": current}))
    return True


def render(findings: Iterable[Finding], *, root: Path | None = None) -> str:
    """One message, short enough to read on a phone and specific enough to act on."""
    items = list(findings)
    if not items and root is not None and blind(root):
        return (
            "I cannot see this workspace: it has no heartbeat log and no ledger, so there is nothing to judge. "
            "Both are gitignored, so a fresh clone always looks like this — check the published snapshot instead."
        )
    if not items:
        return "Nothing stalled: the loop is changing its mind, the last night cost something, and no cheap seat is down."
    lines = [f"{len(items)} thing(s) worth a look:"]
    lines.extend(f"\n• [{f.severity}] {f.detail}" for f in items)
    return "".join(lines)[:3400]


__all__ = ["Finding", "REPEAT_LIMIT", "blind", "check", "render", "worth_announcing"]
