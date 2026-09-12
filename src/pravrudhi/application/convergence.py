"""ADR-0053 §6: a loop's health is whether it is converging, not whether its timer is firing.

Liveness is necessary and not sufficient. On 2026-09-12 both loops' timers fired on schedule all day while the
Studio loop parked eighteen dispatches on one criterion and the product loop judged the same criterion unmet
every hour since 14:24, and neither was noticed, because the only figure anyone looked at was "is it ticking".
The operator's instruction that day is that every lead status opens with, per loop, the criteria closed and
dispatches accepted in the trailing 24 h, the last beat, and the current failure mode -- read from the records
and never estimated.

This module is that computation, in the engine rather than in whatever script the reader happens to have.
The reason it belongs here is a bug, not tidiness: the ad-hoc script those figures came from read
`result["judged"]` at the top level of each beat, and a beat that dispatches several criteria writes
`{"kind": "batch", "dispatches": [...]}` instead, nesting each criterion's verdict one level down. Every
criterion judged in a two-wide beat was therefore invisible, and Studio's convergence was understated by about
30% for an evening before anyone noticed. A number that decides which work outranks which must be computed by
something with a test around it.

`met` is the figure that matters: a zero on criteria closed outranks every card and every release. So
`failure_mode` exists to say WHY a zero is a zero -- dispatching nothing, dispatching and never being accepted,
or being accepted and judged not met are three different failures with three different remedies, and a report
that only says "0" invites the wrong one.

One property to hold in mind before reading a fall in `met` as a regression: this is a TRAILING window, so it
declines on its own as old beats age out, with nothing having gone wrong. Measured on this repository at
2026-09-12T20:55Z, the same log gave 9 met over 24 h, 10 over 25 h and 11 over 26 h -- the difference being
entirely beats crossing the cutoff, not work. Two readings minutes apart can legitimately differ. Compare like
windows, and treat a drop as a signal only when the window is the same and the beats inside it changed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from pravrudhi.application.heartbeat import log_path

#: Beats older than this are outside the reported window unless a caller asks for another.
DEFAULT_WINDOW_HOURS = 24

_AT_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


@dataclass(frozen=True)
class Convergence:
    """One loop's trailing-window health. `beats` is liveness; everything else is convergence."""

    beats: int
    dispatches: int
    accepted: int
    rejected: int
    met: int
    not_met: int
    batch_beats: int
    last_beat: str | None
    failure_mode: str | None
    window_hours: int

    def line(self, name: str) -> str:
        """The one-line form a status opens with, per the operator's 2026-09-12 instruction."""
        head = (
            f"{name}: {self.met} MET, {self.beats} beats, {self.dispatches} dispatches "
            f"({self.accepted} acc/{self.rejected} rej), last {self.last_beat or 'never'}"
        )
        return head if self.failure_mode is None else f"{head} - {self.failure_mode}"


def _parse_at(value: Any) -> datetime | None:
    try:
        return datetime.strptime(str(value), _AT_FORMAT).replace(tzinfo=UTC)
    except (TypeError, ValueError):
        return None


def _as_moment(now: datetime | str | None) -> datetime:
    if now is None:
        return datetime.now(UTC)
    if isinstance(now, datetime):
        return now if now.tzinfo else now.replace(tzinfo=UTC)
    parsed = _parse_at(now)
    return parsed if parsed is not None else datetime.now(UTC)


def dispatches_of(result: dict[str, Any] | None) -> list[dict[str, Any]]:
    """The per-criterion results of one beat.

    A beat that dispatched several criteria carries them under `result["dispatches"]` with `kind == "batch"`;
    a single-dispatch beat IS its own result; a beat that dispatched nothing (quiet hours, a parked request, a
    rebase conflict) has no result at all and contributes none. Reading only the top level is the bug this
    module exists to prevent, so it is one function with one place to be wrong.
    """
    if not result:
        return []
    if result.get("kind") == "batch":
        return [d for d in (result.get("dispatches") or []) if isinstance(d, dict)]
    return [result]


def _failure_mode(met: int, dispatches: int, accepted: int, not_met: int) -> str | None:
    """Why a zero is a zero. Returns None when the loop closed something -- a converging loop needs no diagnosis."""
    if met > 0:
        return None
    if dispatches == 0:
        return "closed nothing and dispatched nothing: the loop is ticking but not selecting work"
    if accepted == 0:
        return f"closed nothing; {dispatches} dispatches, none accepted (the work never reached the judge)"
    if not_met > 0:
        return f"closed nothing; {accepted} accepted but {not_met} judged not met (the judge is refusing the work)"
    return f"closed nothing; {accepted} accepted, none judged (dispatches are not reaching a verdict)"


def convergence(
    root: Path, *, hours: int = DEFAULT_WINDOW_HOURS, now: datetime | str | None = None
) -> Convergence:
    """This root's loop health over the trailing `hours`, read from `.pravrudhi/heartbeat.jsonl`.

    A corrupt line is skipped rather than fatal, the same tolerance `heartbeat.history` already has: a truncated
    write must not blind the health metric, which is precisely when it would be most wanted.
    """
    cutoff = _as_moment(now) - timedelta(hours=hours)
    path = log_path(Path(root))
    beats = 0
    batch_beats = 0
    last_beat: str | None = None
    per: list[dict[str, Any]] = []

    if path.exists():
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(record, dict):
                continue
            at = _parse_at(record.get("at"))
            if at is None or at < cutoff:
                continue
            beats += 1
            last_beat = str(record.get("at"))
            result = record.get("result")
            if isinstance(result, dict) and result.get("kind") == "batch":
                batch_beats += 1
            per.extend(dispatches_of(result if isinstance(result, dict) else None))

    accepted = sum(1 for d in per if d.get("accepted") is True)
    met = sum(1 for d in per if d.get("judged") == "met")
    not_met = sum(1 for d in per if d.get("judged") == "not met")
    return Convergence(
        beats=beats,
        dispatches=len(per),
        accepted=accepted,
        rejected=len(per) - accepted,
        met=met,
        not_met=not_met,
        batch_beats=batch_beats,
        last_beat=last_beat,
        failure_mode=_failure_mode(met, len(per), accepted, not_met),
        window_hours=hours,
    )
