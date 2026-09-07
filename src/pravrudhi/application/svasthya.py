"""Svāsthya: the survival contract, made a measurable health state instead of an assumption.

Design doc §5.1 (docs/superpowers/specs/2026-09-06-appetite-and-intake-design.md) defines "survive" as retaining
the operator-authorized ability to work, and names exactly what that requires: health endpoints answer, request
capture works, the scheduler is fresh, disk is available, owned processes are reaped, the ledger chain verifies
and replay agrees, a known-good release can be restored, and at least one qualified route exists with a tested
independent fallback. It also names what survival explicitly does not authorize: rechaining the ledger to
"repair" integrity, buying credits, creating accounts, replicating to another host, relaunching after an operator
stop, hiding a process, or changing credentials.

This module is `kshudha`'s promised source for `sthiti` (continuity) health/resource inputs (design §5.1: "the
`sthiti` continuity drive reads what you build") — wiring that read is a separate task, same as `kshudha.py`'s own
docstring defers wiring itself into `heartbeat.py`. `assess()` is a pure-ish reader: it never mutates the ledger,
never dispatches work, and only ever writes its own small control file under `.pravrudhi/svasthya/`. `repair()`
is the one place that changes health state on purpose, and only by leasing a bounded attempt at ONE named
failing check and rechecking that exact predicate afterward — never by trusting a repair runner's own claim of
success, which is what "a leased repair enters recovering and returns to ready ONLY after the failed predicate is
rechecked" (§5.1) rules out.

Five control states, exactly as designed: `ready`, `degraded` (a failed noncritical check), `recovering`
(a repair lease in progress), `integrity_halt` (chain or replay failure — read-only diagnosis only, no new
work), and `paused` (operator stop; absorbing until an explicit `resume()`, surviving a process restart because
it is read from a file, never held only in memory).
"""

from __future__ import annotations

import importlib
import json
import os
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any, NoReturn

import yaml

from pravrudhi.agents import registry as agents_registry
from pravrudhi.application import availability, heartbeat, requests
from pravrudhi_kernel.ledger import replay as kernel_replay
from pravrudhi_kernel.ledger import verify as kernel_verify
from pravrudhi_kernel.ledger.replay import state_bytes
from pravrudhi_kernel.ledger.verify import iter_events

PACKAGED_CONFIG = Path(__file__).resolve().parents[1] / "assets" / "configs" / "survival.yaml"


class HealthState(StrEnum):
    READY = "ready"
    DEGRADED = "degraded"
    RECOVERING = "recovering"
    INTEGRITY_HALT = "integrity_halt"
    PAUSED = "paused"


class SurvivalPolicyError(RuntimeError):
    """Raised by an action survival must never take, and by guards that refuse to proceed under this state."""


def _iso(moment: datetime) -> str:
    aware = moment if moment.tzinfo else moment.replace(tzinfo=UTC)
    return aware.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(text: str) -> datetime:
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


# --------------------------------------------------------------------------------------------------------------
# Configuration


@dataclass(frozen=True)
class SurvivalConfig:
    policy_version: str = "1"
    min_free_disk_bytes: int = 536_870_912
    scheduler_max_stale_s: float = 7200.0
    process_max_lifetime_s: float = 3600.0
    repair_backoff_base_s: float = 60.0
    repair_backoff_max_s: float = 3600.0
    min_independent_routes: int = 2
    independent_route_names: tuple[str, ...] = field(default_factory=tuple)


def load_config(path: Path | None = None) -> SurvivalConfig:
    raw: dict[str, Any] = yaml.safe_load((path or PACKAGED_CONFIG).read_text(encoding="utf-8")) or {}
    return SurvivalConfig(
        policy_version=str(raw.get("policy_version") or "1"),
        min_free_disk_bytes=int(raw.get("min_free_disk_bytes", 536_870_912)),
        scheduler_max_stale_s=float(raw.get("scheduler_max_stale_s", 7200.0)),
        process_max_lifetime_s=float(raw.get("process_max_lifetime_s", 3600.0)),
        repair_backoff_base_s=float(raw.get("repair_backoff_base_s", 60.0)),
        repair_backoff_max_s=float(raw.get("repair_backoff_max_s", 3600.0)),
        min_independent_routes=int(raw.get("min_independent_routes", 2)),
        independent_route_names=tuple(str(x) for x in (raw.get("independent_route_names") or ())),
    )


# --------------------------------------------------------------------------------------------------------------
# Health and control state


@dataclass(frozen=True)
class CheckResult:
    name: str
    ok: bool
    detail: str
    integrity: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "ok": self.ok, "detail": self.detail, "integrity": self.integrity}


@dataclass(frozen=True)
class Health:
    state: HealthState
    as_of: str
    checks: tuple[CheckResult, ...]
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state.value, "as_of": self.as_of,
            "checks": [c.to_dict() for c in self.checks], "reason": self.reason,
        }


@dataclass(frozen=True)
class RepairResult:
    check: str
    fixed: bool
    health: Health
    detail: str
    backoff_until: str | None


@dataclass(frozen=True)
class RepairBackoff:
    attempts: int
    backoff_until: str


@dataclass(frozen=True)
class ControlState:
    state: HealthState
    since: str
    reason: str
    open_repairs: dict[str, RepairBackoff]
    paused_reason: str | None


def _default_control() -> ControlState:
    return ControlState(state=HealthState.READY, since="", reason="", open_repairs={}, paused_reason=None)


def _control_path(root: Path) -> Path:
    return Path(root) / ".pravrudhi" / "svasthya" / "control.json"


def _load_control(root: Path) -> ControlState:
    """The persisted control state, or the fresh default. A corrupt file starts over, like every other JSON
    store in this codebase (`kshudha.load_state`, `requests.load`) — never crash the caller over a bad file."""
    path = _control_path(root)
    if not path.exists():
        return _default_control()
    try:
        raw = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return _default_control()
    if not isinstance(raw, dict):
        return _default_control()
    try:
        state = HealthState(str(raw.get("state", "ready")))
    except ValueError:
        state = HealthState.READY
    open_repairs = {
        str(k): RepairBackoff(attempts=int(v.get("attempts", 0)), backoff_until=str(v.get("backoff_until", "")))
        for k, v in (raw.get("open_repairs") or {}).items() if isinstance(v, dict)
    }
    return ControlState(
        state=state, since=str(raw.get("since", "")), reason=str(raw.get("reason", "")),
        open_repairs=open_repairs, paused_reason=raw.get("paused_reason"),
    )


def _save_control(root: Path, control: ControlState) -> None:
    path = _control_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "state": control.state.value, "since": control.since, "reason": control.reason,
        "open_repairs": {
            k: {"attempts": v.attempts, "backoff_until": v.backoff_until} for k, v in control.open_repairs.items()
        },
        "paused_reason": control.paused_reason,
    }
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True))
    tmp.replace(path)


# --------------------------------------------------------------------------------------------------------------
# Owned-process registry, for the "owned processes are reaped" check.


def _owned_pids_path(root: Path) -> Path:
    return Path(root) / ".pravrudhi" / "svasthya" / "owned_pids.json"


def _load_owned_pids(root: Path) -> dict[str, dict[str, str]]:
    path = _owned_pids_path(root)
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _save_owned_pids(root: Path, registry: dict[str, dict[str, str]]) -> None:
    path = _owned_pids_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(registry, indent=2, sort_keys=True))
    tmp.replace(path)


def track_process(root: Path, pid: int, *, label: str = "", now: datetime | None = None) -> None:
    """Record a process this engine owns, so `assess()` can tell a live worker from an unreaped orphan."""
    moment = now or datetime.now(UTC)
    registry = _load_owned_pids(root)
    registry[str(pid)] = {"since": _iso(moment), "label": label}
    _save_owned_pids(root, registry)


def release_process(root: Path, pid: int) -> None:
    """Forget a tracked process, e.g. once its owner has confirmed it exited."""
    registry = _load_owned_pids(root)
    if registry.pop(str(pid), None) is not None:
        _save_owned_pids(root, registry)


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


# --------------------------------------------------------------------------------------------------------------
# Individual survival checks. Each is a small, independently monkeypatchable function so a test can control one
# predicate without faking every other input `assess()` reads.


def _import_app_module() -> Any:
    return importlib.import_module("pravrudhi.api.server")


def _check_health_endpoints() -> CheckResult:
    name = "health_endpoints"
    try:
        module = _import_app_module()
    except Exception as exc:  # noqa: BLE001 -- an unimportable app surface is a failing check, not a crash
        return CheckResult(name, False, f"pravrudhi.api.server failed to import: {exc}")
    if not callable(getattr(module, "create_app", None)):
        return CheckResult(name, False, "pravrudhi.api.server has no `create_app` factory")
    return CheckResult(name, True, "pravrudhi.api.server imports and exposes `create_app`")


def _check_request_capture(root: Path) -> CheckResult:
    name = "request_capture"
    try:
        requests.backlog(root)
    except Exception as exc:  # noqa: BLE001 -- capture must degrade honestly, not crash the assessment
        return CheckResult(name, False, f"the request backlog could not be read: {exc}")
    return CheckResult(name, True, "the request backlog is readable")


def _check_scheduler_fresh(root: Path, cfg: SurvivalConfig, now: datetime) -> CheckResult:
    name = "scheduler_fresh"
    try:
        recent = heartbeat.history(root, n=1)
    except Exception as exc:  # noqa: BLE001 -- an unreadable log is a failing check, not a crash
        return CheckResult(name, False, f"heartbeat history could not be read: {exc}")
    if not recent:
        return CheckResult(name, False, "no heartbeat has been recorded yet")
    try:
        last = _parse_iso(recent[-1].at)
    except ValueError:
        return CheckResult(name, False, f"heartbeat record has an unparsable timestamp: {recent[-1].at!r}")
    age_s = (now - last).total_seconds()
    if age_s > cfg.scheduler_max_stale_s:
        return CheckResult(name, False, f"last heartbeat was {age_s:.0f}s ago (max {cfg.scheduler_max_stale_s:.0f}s)")
    return CheckResult(name, True, f"last heartbeat was {age_s:.0f}s ago")


def _disk_free_bytes(root: Path) -> int:
    return shutil.disk_usage(root).free


def _check_disk(root: Path, cfg: SurvivalConfig) -> CheckResult:
    name = "disk_available"
    try:
        free = _disk_free_bytes(root)
    except OSError as exc:
        return CheckResult(name, False, f"disk usage could not be read: {exc}")
    if free < cfg.min_free_disk_bytes:
        return CheckResult(name, False, f"only {free} bytes free (need {cfg.min_free_disk_bytes})")
    return CheckResult(name, True, f"{free} bytes free")


def _check_processes_reaped(root: Path, cfg: SurvivalConfig, now: datetime) -> CheckResult:
    name = "processes_reaped"
    try:
        registry = _load_owned_pids(root)
    except OSError as exc:
        return CheckResult(name, False, f"the owned-process registry could not be read: {exc}")
    stale: list[str] = []
    for pid_str, info in registry.items():
        try:
            pid = int(pid_str)
            since = _parse_iso(str(info.get("since", "")))
        except ValueError:
            continue
        if _pid_alive(pid) and (now - since).total_seconds() > cfg.process_max_lifetime_s:
            stale.append(pid_str)
    if stale:
        return CheckResult(name, False, f"owned process(es) still running past their lifetime: {', '.join(stale)}")
    return CheckResult(name, True, f"{len(registry)} owned process(es) tracked; none overdue for reaping")


def _hash_at(ledger: Path, seq: int) -> str | None:
    for ev in iter_events(ledger):
        if ev.seq == seq:
            return ev.this_hash
    return None


def _check_ledger_integrity(root: Path) -> CheckResult:
    """Chain verification plus replay agreement (design §5.1), read-only: never writes `research/state.json`,
    unlike `application/replay.py::replay_command`, because assessing health must never mutate state — only a
    STALE (older, still-consistent) `state.json` counts as agreeing; a DIVERGED one is an integrity failure."""
    name = "ledger_integrity"
    ledger = root / "research" / "ledger.jsonl"
    if not ledger.exists():
        return CheckResult(name, False, "no ledger present at research/ledger.jsonl")
    try:
        result = kernel_verify(ledger)
    except (OSError, ValueError) as exc:
        return CheckResult(name, False, f"the ledger could not be read: {exc}", integrity=True)
    if not result.ok:
        detail = f"ledger chain broken at seq {result.first_bad_seq}: {result.reason}"
        return CheckResult(name, False, detail, integrity=True)
    try:
        state = kernel_replay(ledger)
    except (OSError, ValueError) as exc:
        return CheckResult(name, False, f"replay failed: {exc}", integrity=True)
    state_path = root / "research" / "state.json"
    if not state_path.is_file():
        return CheckResult(name, True, "ledger chain verifies; no state.json to cross-check yet")
    try:
        saved = json.loads(state_path.read_text())
        current_text = state_path.read_text()
    except (OSError, ValueError):
        return CheckResult(name, False, "state.json is not valid JSON", integrity=True)
    if current_text == state_bytes(state):
        return CheckResult(name, True, f"ledger chain verifies; state.json matches replay at seq {state.seq}")
    saved_seq, saved_head = saved.get("seq"), saved.get("ledger_head")
    if isinstance(saved_seq, int) and saved_seq < state.seq and _hash_at(ledger, saved_seq) == saved_head:
        detail = f"ledger chain verifies; state.json is stale at seq {saved_seq}, not diverged"
        return CheckResult(name, True, detail)
    return CheckResult(name, False, f"state.json diverges from replay at seq {saved_seq}", integrity=True)


def _check_release_restorable(root: Path) -> CheckResult:
    name = "release_restorable"
    current = root / ".pravrudhi" / "releases" / "current"
    if not current.is_symlink():
        return CheckResult(name, False, "no known-good release symlink at .pravrudhi/releases/current")
    try:
        target = current.resolve()
    except OSError as exc:
        return CheckResult(name, False, f"the release symlink could not be resolved: {exc}")
    if not target.is_dir():
        return CheckResult(name, False, f"the release symlink target is missing: {target}")
    return CheckResult(name, True, f"a known-good release is installed at {target.name}")


def _check_route_fallback(root: Path, cfg: SurvivalConfig) -> CheckResult:
    """At least `min_independent_routes` routes that are both actually tested (design §5.1: "an installed
    binary is not a tested route") and currently available and not cooling — a single account being merely
    installed, or several models sharing one unavailable account, never satisfies this (§5.1)."""
    name = "route_fallback"
    try:
        statuses = agents_registry.survey(root)
    except OSError as exc:
        return CheckResult(name, False, f"the agent survey failed: {exc}")
    try:
        cooling_ids = set(availability.cooling(root).keys())
    except OSError as exc:
        return CheckResult(name, False, f"cooldown state could not be read: {exc}")
    tested = sorted(
        s.name for s in statuses
        if s.available and s.name in cfg.independent_route_names and s.name not in cooling_ids
    )
    if len(tested) >= cfg.min_independent_routes:
        return CheckResult(name, True, f"tested independent routes available: {tested}")
    detail = f"only {len(tested)} tested independent route(s) available (need {cfg.min_independent_routes}): {tested}"
    return CheckResult(name, False, detail)


# --------------------------------------------------------------------------------------------------------------
# assess()


def assess(root: Path, *, now: datetime | None = None) -> Health:
    """Every survival check the design requires (§5.1), or `paused` outright if the operator has stopped this
    engine: paused is absorbing, so it is returned without re-running a single check, exactly as designed
    ("only explicit resume reevaluates health"). Persists the resulting control state; never mutates anything
    else — no ledger write, no dispatch, no process action."""
    root = Path(root)
    moment = now or datetime.now(UTC)
    control = _load_control(root)
    if control.state is HealthState.PAUSED:
        reason = control.reason or "operator stop is in effect; call resume() to reevaluate"
        return Health(state=HealthState.PAUSED, as_of=_iso(moment), checks=(), reason=reason)

    cfg = load_config()
    checks = (
        _check_health_endpoints(),
        _check_request_capture(root),
        _check_scheduler_fresh(root, cfg, moment),
        _check_disk(root, cfg),
        _check_processes_reaped(root, cfg, moment),
        _check_ledger_integrity(root),
        _check_release_restorable(root),
        _check_route_fallback(root, cfg),
    )
    integrity_failed = tuple(c for c in checks if not c.ok and c.integrity)
    other_failed = tuple(c for c in checks if not c.ok and not c.integrity)
    if integrity_failed:
        state = HealthState.INTEGRITY_HALT
        reason = "; ".join(f"{c.name}: {c.detail}" for c in integrity_failed)
    elif other_failed:
        state = HealthState.DEGRADED
        reason = "; ".join(f"{c.name}: {c.detail}" for c in other_failed)
    else:
        state = HealthState.READY
        reason = "all survival checks pass"

    _save_control(root, ControlState(
        state=state, since=_iso(moment), reason=reason, open_repairs=control.open_repairs, paused_reason=None,
    ))
    return Health(state=state, as_of=_iso(moment), checks=checks, reason=reason)


def can_dispatch_new_work(health: Health) -> bool:
    """Whether this health permits starting new discretionary work (design §5.1): never under `integrity_halt`
    ("permits capture and read-only diagnosis ONLY") and never under `paused`."""
    return health.state in (HealthState.READY, HealthState.DEGRADED)


# --------------------------------------------------------------------------------------------------------------
# repair(), resume(), stop()


def repair(
    root: Path, check: str, runner: Callable[[], bool], *, now: datetime | None = None, authorized: bool = False,
) -> RepairResult:
    """Lease a bounded repair attempt for exactly one named failing check.

    Enters `recovering` for the duration of `runner()`, then rechecks the same predicate fresh — `runner`'s own
    return value is never trusted as proof of a fix (design §5.1: "returns to ready ONLY after the failed
    predicate is rechecked"). A repair that does not fix its predicate returns to `degraded` (or whatever
    `assess()` honestly finds) with an exponential backoff on that one check; nothing else is touched.
    """
    root = Path(root)
    moment = now or datetime.now(UTC)
    control = _load_control(root)
    if control.state is HealthState.PAUSED:
        raise SurvivalPolicyError(f"cannot repair {check!r}: the engine is paused; call resume() first")

    current = assess(root, now=moment)
    if current.state is HealthState.INTEGRITY_HALT and not authorized:
        raise SurvivalPolicyError(
            f"cannot repair {check!r}: integrity_halt permits only read-only diagnosis without authorization"
        )

    by_name = {c.name: c for c in current.checks}
    target = by_name.get(check)
    if target is None:
        raise ValueError(f"unknown survival check: {check!r}")

    control = _load_control(root)
    pending = control.open_repairs.get(check)
    if pending is not None and pending.backoff_until and _parse_iso(pending.backoff_until) > moment:
        detail = f"backing off {check!r} until {pending.backoff_until}"
        return RepairResult(check=check, fixed=False, health=current, detail=detail, backoff_until=pending.backoff_until)

    if target.ok:
        return RepairResult(check=check, fixed=True, health=current, detail=f"{check!r} already passes", backoff_until=None)

    _save_control(root, ControlState(
        state=HealthState.RECOVERING, since=_iso(moment), reason=f"repairing {check!r}",
        open_repairs=control.open_repairs, paused_reason=None,
    ))

    try:
        ok_runner = bool(runner())
    except Exception as exc:  # noqa: BLE001 -- a raising repair must degrade honestly, never crash the caller
        run_detail = f"repair runner raised: {exc}"
    else:
        run_detail = "repair runner reported success" if ok_runner else "repair runner reported failure"

    rechecked = assess(root, now=moment)
    fixed_check = next((c for c in rechecked.checks if c.name == check), None)

    if fixed_check is not None and fixed_check.ok:
        cleared = {k: v for k, v in control.open_repairs.items() if k != check}
        _save_control(root, ControlState(
            state=rechecked.state, since=_iso(moment), reason=rechecked.reason,
            open_repairs=cleared, paused_reason=None,
        ))
        return RepairResult(check=check, fixed=True, health=rechecked, detail=run_detail, backoff_until=None)

    cfg = load_config()
    attempts = (pending.attempts if pending is not None else 0) + 1
    backoff_s = min(cfg.repair_backoff_base_s * (2 ** (attempts - 1)), cfg.repair_backoff_max_s)
    backoff_until = _iso(moment + timedelta(seconds=backoff_s))
    new_open = dict(control.open_repairs)
    new_open[check] = RepairBackoff(attempts=attempts, backoff_until=backoff_until)
    _save_control(root, ControlState(
        state=rechecked.state, since=_iso(moment), reason=f"repair of {check!r} did not fix its predicate",
        open_repairs=new_open, paused_reason=None,
    ))
    detail = f"{run_detail}; predicate still fails"
    return RepairResult(check=check, fixed=False, health=rechecked, detail=detail, backoff_until=backoff_until)


def resume(root: Path, *, now: datetime | None = None) -> Health:
    """Explicit operator resume: the only way out of `paused` (design §5.1). Reevaluates health immediately
    rather than assuming `ready`, so a stop that outlasted a real failure surfaces that failure right away."""
    root = Path(root)
    moment = now or datetime.now(UTC)
    control = _load_control(root)
    if control.state is not HealthState.PAUSED:
        return assess(root, now=moment)
    _save_control(root, ControlState(
        state=HealthState.READY, since=_iso(moment), reason="resumed by operator",
        open_repairs=control.open_repairs, paused_reason=None,
    ))
    return assess(root, now=moment)


def stop(root: Path, *, reason: str = "operator stop", now: datetime | None = None) -> Health:
    """Enter `paused`: absorbing until `resume()`, and persisted to disk so it survives a process restart."""
    root = Path(root)
    moment = now or datetime.now(UTC)
    control = _load_control(root)
    _save_control(root, ControlState(
        state=HealthState.PAUSED, since=_iso(moment), reason=reason,
        open_repairs=control.open_repairs, paused_reason=reason,
    ))
    return Health(state=HealthState.PAUSED, as_of=_iso(moment), checks=(), reason=reason)


# --------------------------------------------------------------------------------------------------------------
# What survival explicitly does not authorize (design §5.1). Each of these always refuses; nothing in this
# module calls any of them, and they exist so that code elsewhere cannot reach for one and call it a repair.


def rechain_ledger(*_args: Any, **_kwargs: Any) -> NoReturn:
    raise SurvivalPolicyError("rechain_ledger: svasthya must never rechain the ledger to repair integrity")


def buy_credits(*_args: Any, **_kwargs: Any) -> NoReturn:
    raise SurvivalPolicyError("buy_credits: svasthya must never spend money to survive")


def create_account(*_args: Any, **_kwargs: Any) -> NoReturn:
    raise SurvivalPolicyError("create_account: svasthya must never create an account to survive")


def replicate_to_host(*_args: Any, **_kwargs: Any) -> NoReturn:
    raise SurvivalPolicyError("replicate_to_host: svasthya must never replicate itself to another host")


def relaunch_after_stop(*_args: Any, **_kwargs: Any) -> NoReturn:
    raise SurvivalPolicyError("relaunch_after_stop: svasthya must never relaunch after an operator stop")


def hide_process(*_args: Any, **_kwargs: Any) -> NoReturn:
    raise SurvivalPolicyError("hide_process: svasthya must never hide a process from the operator")


def change_credentials(*_args: Any, **_kwargs: Any) -> NoReturn:
    raise SurvivalPolicyError("change_credentials: svasthya must never change credentials")


__all__ = [
    "CheckResult", "ControlState", "Health", "HealthState", "RepairBackoff", "RepairResult", "SurvivalConfig",
    "SurvivalPolicyError", "assess", "buy_credits", "can_dispatch_new_work", "change_credentials",
    "create_account", "hide_process", "load_config", "rechain_ledger", "relaunch_after_stop", "release_process",
    "replicate_to_host", "repair", "resume", "stop", "track_process",
]
