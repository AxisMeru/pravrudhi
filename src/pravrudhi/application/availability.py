"""Telling a vendor's usage limit apart from an ordinary failure, and remembering it for a while.

Today a limit reads exactly like a bug: the agent's CLI exits non-zero (or exits zero having done nothing) and the
router records a loss, the same as it would for a prompt the model genuinely botched. That is wrong twice over. It
punishes a route for something that says nothing about its quality, and it does not stop the engine from dispatching
the very next task to the same account, which is still limited and will fail the same way for hours.

This module gives the difference a name (`classify`) and a place to remember it (a cooldown file beside the routing
log), so a limited account can be skipped for a while and tried again automatically -- rather than either quietly
eating the loss or stopping the loop for a human to notice.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta, tzinfo
from pathlib import Path
from typing import Any, Protocol

import yaml

PACKAGED_LIMITS_CONFIG = Path(__file__).resolve().parents[1] / "assets" / "configs" / "limits.yaml"

DEFAULT_COOLDOWN_MINUTES = 60.0


class RouteLike(Protocol):
    """The one thing `usable_routes` needs from a route: which agent it dispatches through.

    Declared read-only. A bare `agent: str` member requires a settable attribute, which a frozen dataclass such
    as `routing.Route` does not provide, so the real route type failed to satisfy the protocol at all.
    """

    @property
    def agent(self) -> str: ...



def _load_config(path: Path | None = None) -> dict[str, Any]:
    raw = yaml.safe_load((path or PACKAGED_LIMITS_CONFIG).read_text())
    return raw if isinstance(raw, dict) else {}


def _limit_patterns() -> dict[str, list[str]]:
    patterns = _load_config().get("patterns") or {}
    return {str(agent_id): [str(p) for p in (phrases or [])] for agent_id, phrases in patterns.items()}


# Loaded once at import: a small, packaged config, not operator state that changes at runtime.
LIMIT_PATTERNS: dict[str, list[str]] = _limit_patterns()


def _transient_patterns() -> list[str]:
    return [str(p) for p in (_load_config().get("transient") or [])]


TRANSIENT_PATTERNS: list[str] = _transient_patterns()
"""Phrases meaning the transport stumbled rather than the account being spent.

Not keyed by agent: a reset connection belongs to the network, not the vendor. Kept in `limits.yaml` because
constants live in configs in this repository, and because a vendor's new phrasing must be addable without a
release."""


def transient_cooldown_minutes() -> float:
    """How long a merely-stumbling seat is held out. Short on purpose: the account is fine."""
    value = _load_config().get("transient_cooldown_minutes")
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 2.0


def _default_cooldown_minutes(agent_id: str) -> float:
    minutes = _load_config().get("cooldown_minutes") or {}
    if agent_id in minutes:
        return float(minutes[agent_id])
    return float(minutes.get("default", DEFAULT_COOLDOWN_MINUTES))


def classify(agent_id: str, text: str, returncode: int) -> str:
    """"ok", "limited", "transient" or "failed" for one finished agent run.

    `transient` is the class that was missing. Without it a reset connection, a 1800s timeout and a genuinely bad
    answer were one verdict: no fallback, and a loss recorded against a route that may have done nothing wrong.

    A wrong call in either direction only costs a cooldown or a loss recorded slightly late; it never crashes a
    wave, so the match against `LIMIT_PATTERNS` is deliberately a loose, case-insensitive substring test rather than
    a precise parse of a vendor's error format that would need updating every time that format changes.
    """
    haystack = (text or "").lower()
    if any(phrase.lower() in haystack for phrase in LIMIT_PATTERNS.get(agent_id, ())):
        return "limited"
    if returncode != 0 and any(phrase.lower() in haystack for phrase in TRANSIENT_PATTERNS):
        # Checked after `limited` deliberately: a 429 that also mentions a reset connection is a spent account,
        # and calling it a stumble would retry into the same wall on a two-minute cooldown instead of an hour's.
        return "transient"
    return "ok" if returncode == 0 else "failed"


def reprobe_hours() -> float:
    """After how long a still-running cooldown is offered to the router again (`limits.yaml`)."""
    value = _load_config().get("reprobe_hours")
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 6.0


def _cooldown_path(root: Path) -> Path:
    return Path(root) / ".pravrudhi" / "agent_cooldown.json"


_STAMP = "%Y-%m-%dT%H:%M:%SZ"


def _read(root: Path) -> dict[str, dict[str, str]]:
    """Every entry as `{"until": ..., "marked": ...}`.

    The file held bare `until` strings until 2026-09-11; those still read, with no `marked` time, and are
    rewritten in the new shape the next time anything is marked.
    """
    p = _cooldown_path(root)
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return {}  # a corrupt cooldown file must not stop the router; it just forgets the cooldowns
    if not isinstance(data, dict):
        return {}
    out: dict[str, dict[str, str]] = {}
    for k, v in data.items():
        if isinstance(v, dict) and "until" in v:
            out[str(k)] = {"until": str(v["until"]), **({"marked": str(v["marked"])} if "marked" in v else {})}
        else:
            out[str(k)] = {"until": str(v)}
    return out


def _write(root: Path, data: dict[str, dict[str, str]]) -> None:
    p = _cooldown_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, sort_keys=True))


def _parse(stamp: str | None) -> datetime | None:
    if not stamp:
        return None
    try:
        return datetime.strptime(stamp, _STAMP).replace(tzinfo=UTC)
    except ValueError:
        return None


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


_TIME = re.compile(
    r"try again at\s+(\d{1,2}):(\d{2})\s*([AaPp][Mm])?",
)
"""What a vendor prints alongside a usage limit. Anchored on "try again at" rather than matching any clock-like
string, because an error message is full of numbers and a wrong parse here holds a working route out of rotation
or, worse, returns it before the account has."""


# "reset at 09-14 16:14:00 UTC" — a month-day with a clock, which is how a weekly quota states a return that is
# days rather than hours away. Without this the bare-clock rule below read the 16:14 and offered it as today or
# tomorrow, so the engine retried a seat with six days left to run, took another refusal, and cooled again.
_DATED = re.compile(r"(\d{1,2})-(\d{1,2})\s+(\d{1,2}):(\d{2})(?::\d{2})?\s*(UTC|GMT)?", re.IGNORECASE)


def _dated_reset(text: str, now: datetime) -> datetime | None:
    """A month-day-and-time the vendor stated, resolved against the year it must belong to."""
    match = _DATED.search(text or "")
    if not match:
        return None
    month, day, hour, minute = (int(match.group(i)) for i in (1, 2, 3, 4))
    if not (1 <= month <= 12 and 1 <= day <= 31 and hour <= 23 and minute <= 59):
        return None
    try:
        stated = now.replace(year=now.year, month=month, day=day, hour=hour, minute=minute, second=0, microsecond=0)
    except ValueError:
        return None
    # A date already past means the vendor is naming next year's, which happens either side of a new year.
    if stated < now:
        try:
            stated = stated.replace(year=now.year + 1)
        except ValueError:
            return None
    return stated.astimezone(UTC)


def reset_at(text: str, *, now: datetime | None = None, tz: tzinfo | None = None) -> datetime | None:
    """When the vendor says the account comes back, or None if it did not say.

    The engine used to start a fixed sixty-minute timer from the moment of failure, which is a guess made in the
    presence of the answer. On 2026-09-07 that cost half an hour of the strongest model: the limit hit at 15:02,
    the vendor said 15:51, and the route was held until 16:02. The operator noticed the model was back before the
    engine did, which is the wrong way round for something meant to run unattended.

    The stated time is the vendor's local time, so it is read in the machine's zone and returned as UTC. A time
    earlier in the day than the failure means tomorrow: a vendor saying "try again at 9 AM" at 5 PM is not
    offering this morning.
    """
    when_now = _aware(now or datetime.now(UTC))
    dated = _dated_reset(text, when_now)
    if dated is not None:
        return dated

    match = _TIME.search(text or "")
    if not match:
        return None
    hour, minute, meridiem = int(match.group(1)), int(match.group(2)), (match.group(3) or "").lower()
    if meridiem:
        if not 1 <= hour <= 12 or minute > 59:
            return None
        hour = (hour % 12) + (12 if meridiem.startswith("p") else 0)
    elif hour > 23 or minute > 59:
        return None

    when = _aware(now or datetime.now(UTC))
    zone = tz or when.astimezone().tzinfo or UTC
    local = when.astimezone(zone)
    stated = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if stated <= local:
        stated += timedelta(days=1)
    return stated.astimezone(UTC)


def mark_limited(
    root: Path, agent_id: str, *, minutes: float | None = None, now: datetime | None = None,
    until: datetime | None = None,
) -> None:
    """Remember that `agent_id` reported a usage limit, so the router leaves it alone until it is back.

    `until` is the vendor's own stated return time and is preferred when there is one, because a fixed window is
    a guess and the vendor knows. The window remains for the vendors that say nothing.
    """
    when = _aware(now or datetime.now(UTC))
    if until is not None:
        stop = _aware(until)
    else:
        mins = minutes if minutes is not None else _default_cooldown_minutes(agent_id)
        stop = when + timedelta(minutes=mins)
    data = _read(root)
    data[agent_id] = {"until": stop.strftime(_STAMP), "marked": when.strftime(_STAMP)}
    _write(root, data)


def cooling(root: Path, now: datetime | None = None) -> dict[str, str]:
    """Every agent id still held out, mapped to when its window ends.

    A window is held only for `reprobe_hours` at a stretch. Past that, the route is offered again even though
    the vendor's stated time has not come: what the vendor said was true when it said it, and a quota reset
    early on the console is invisible from here except by trying. One ordinary dispatch is the probe; if the
    limit still stands, the failure re-marks the route from the vendor's fresh answer and the next span begins.
    An entry with no `marked` time (written before this rule) is due when more than a span of it remains.
    """
    when = _aware(now or datetime.now(UTC))
    span = timedelta(hours=reprobe_hours())
    out: dict[str, str] = {}
    for agent_id, entry in _read(root).items():
        until = _parse(entry.get("until"))
        if until is None or until <= when:
            continue  # expired, or a hand-edited entry that must not wedge the agent as permanently cooling
        marked = _parse(entry.get("marked"))
        # An entry with no mark predates this rule: it is due when more than a span of it remains.
        due = (until - when >= span) if marked is None else (when - marked >= span)
        if due:
            continue  # offered to the router again; a failing probe re-marks it from the vendor's fresh answer
        out[agent_id] = entry["until"]
    return out


def is_cool(root: Path, agent_id: str, now: datetime | None = None) -> bool:
    return agent_id in cooling(root, now)


def clear(root: Path, agent_id: str) -> None:
    """Forget a cooldown early, e.g. once the operator confirms the account works again."""
    data = _read(root)
    if agent_id in data:
        del data[agent_id]
        _write(root, data)


def usable_routes[R: RouteLike](root: Path, routes: list[R], now: datetime | None = None) -> list[R]:
    """`routes` with every route whose agent is currently cooling down removed.

    Generic in the route type rather than typed to the protocol: a `list` is invariant, so a caller holding
    concrete routes could neither pass its list in nor read concrete attributes off what came back.
    """
    cool = cooling(root, now)
    return [r for r in routes if r.agent not in cool]


__all__ = [
    "LIMIT_PATTERNS",
    "reprobe_hours",
    "classify",
    "mark_limited",
    "cooling",
    "is_cool",
    "clear",
    "usable_routes",
]
