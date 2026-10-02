"""Usage gates as code: refuse a codex or claude call over the seat's limits, on a stale or unverified reading.

`gate_reading(kind, root)` returns the reading that justified letting the call through (recorded on the call's
`Answer.usage_gate`) or raises `UsageGateRefused`. Fail-closed throughout: a missing config key, file, field,
percentage or timestamp, an unverified reading, an old reading, or a window that reset since the reading is a refusal,
never an assumed zero. Thresholds and the maximum age come from `configs/usage_gate.yaml`.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

import yaml

from pravrudhi.application.config_files import config_file

GATED_KINDS = ("codex", "claude")


class UsageGateRefused(RuntimeError):
    """The usage gate is closed. Aborts a run; it is not a per-prompt gap."""


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def _ts(v: Any) -> dt.datetime | None:
    try:
        t = dt.datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=dt.UTC)


def _pct(v: Any) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _load(root: Path) -> dict:
    try:
        cfg = yaml.safe_load(config_file(root, "usage_gate.yaml").read_text())
    except (OSError, FileNotFoundError, yaml.YAMLError) as exc:
        raise UsageGateRefused(f"refusing: cannot load configs/usage_gate.yaml ({exc})") from exc
    if not isinstance(cfg, dict):
        raise UsageGateRefused("refusing: configs/usage_gate.yaml is not a mapping")
    return cfg


def _need(cfg: dict, *path: str) -> Any:
    node: Any = cfg
    for k in path:
        if not isinstance(node, dict) or k not in node:
            raise UsageGateRefused(f"refusing: configs/usage_gate.yaml has no {'.'.join(path)}")
        node = node[k]
    return node


def _read_codex(cfg: dict, now: dt.datetime) -> dict:
    from pravrudhi.application.codex_usage import read_codex_usage

    return read_codex_usage(since=(now - dt.timedelta(days=2)).date(), now=now)


def _read_claude(cfg: dict) -> dict:
    path = Path(str(_need(cfg, "claude", "usage_file"))).expanduser()
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise UsageGateRefused(f"refusing: cannot read claude usage file {path} ({type(exc).__name__})") from exc
    return data if isinstance(data, dict) else {}


class StaleReading(UsageGateRefused):
    """The reading is refused only because it is old or its window reset, and it is far enough under the limits that
    one bounded refresh call may be tried (codex only). Carries the reading that was judged."""

    def __init__(self, message: str, before: dict, reason: str):
        super().__init__(message)
        self.before, self.reason = before, reason


def _judge(
    kind: str, cfg: dict, now: dt.datetime, *, verified: Any, observed: Any, weekly: Any, five: Any, weekly_resets: Any = None
) -> dict:
    max_age = _need(cfg, "max_age_min", kind)
    wmax, fmax = _need(cfg, kind, "weekly_max_pct"), _need(cfg, kind, "five_hour_max_pct")
    if verified is not True:
        raise UsageGateRefused(f"refusing: {kind} usage reading is unverified")
    seen = _ts(observed)
    if seen is None:
        raise UsageGateRefused(f"refusing: {kind} usage reading has no usable observed_at")
    age = (now - seen).total_seconds() / 60
    if age < -1:
        raise UsageGateRefused(f"refusing: {kind} usage reading is dated in the future ({observed})")
    w, f = _pct(weekly), _pct(five)
    if w is None or f is None:
        raise UsageGateRefused(f"refusing: {kind} usage reading is missing a weekly or five-hour percentage")
    reset = _ts(weekly_resets)
    reset_since = reset is not None and reset <= now
    stale = age > max_age
    if stale or reset_since:
        why = (
            f"{kind} usage reading is older than {max_age} min (age {age:.0f} min)"
            if stale
            else f"{kind} weekly window reset at {weekly_resets} after this reading"
        )
        before = {"weekly_used_pct": w, "five_hour_used_pct": f, "observed_at": str(observed), "age_min": round(age, 2)}
        if kind == "codex":
            margin = _need(cfg, "codex", "refresh_margin_pp")
            if reset_since or (w <= wmax - margin and f <= fmax - margin):
                raise StaleReading(
                    f"refusing: {why}",
                    before,
                    "weekly window reset"
                    if reset_since and not stale
                    else ("stale and window reset" if reset_since else "stale"),
                )
        raise UsageGateRefused(f"refusing: {why}")
    if w >= wmax:
        raise UsageGateRefused(f"refusing: {kind} weekly used {w:g}% is at or above {wmax}%")
    if f >= fmax:
        raise UsageGateRefused(f"refusing: {kind} five-hour used {f:g}% is at or above {fmax}%")
    return {
        "vendor_kind": kind,
        "passed": True,
        "weekly_used_pct": w,
        "five_hour_used_pct": f,
        "observed_at": str(observed),
        "age_min": round(age, 2),
        "checked_at": now.isoformat().replace("+00:00", "Z"),
        "thresholds": {"weekly_max_pct": wmax, "five_hour_max_pct": fmax, "max_age_min": max_age},
    }


def _judge_codex(cfg: dict, now: dt.datetime) -> dict:
    r = _read_codex(cfg, now)
    lim = r.get("latest_rate_limits")
    if not isinstance(lim, dict):
        raise UsageGateRefused("refusing: no rate-limit reading in the codex rollout files")
    g = _judge(
        "codex",
        cfg,
        now,
        verified=r.get("verified"),
        observed=lim.get("observed_at"),
        weekly=lim.get("weekly_used_pct"),
        five=lim.get("five_hour_used_pct"),
        weekly_resets=lim.get("weekly_resets_at"),
    )
    g["source"] = "codex rollout files"
    return g


def _run_refresh(cfg: dict) -> None:
    """One minimal codex call whose answer is discarded; it exists only to advance the rollout's rate-limit reading."""
    from pravrudhi.agents.cli_agents import _run

    code, out, err, _wall = _run(
        ["codex", "exec", "--skip-git-repo-check", "--json"],
        Path.cwd(),
        int(_need(cfg, "codex", "refresh_timeout_s")),
        env={},
        stdin_text=str(_need(cfg, "codex", "refresh_prompt")),
    )
    if code != 0:
        raise RuntimeError((err or out or f"codex exited {code}")[-200:])


def _state_path(cfg: dict) -> Path:
    return Path(str(_need(cfg, "codex", "refresh_state_file"))).expanduser()


def _claim_refresh(cfg: dict, now: dt.datetime) -> None:
    """At most one refresh per `refresh_min_interval_min`; the claim is written BEFORE the call so a failed or hung
    refresh still counts. A corrupt state file refuses (no refresh)."""
    path, interval = _state_path(cfg), _need(cfg, "codex", "refresh_min_interval_min")
    if path.exists():
        try:
            last = _ts(json.loads(path.read_text()).get("last_refresh_at"))
        except (OSError, ValueError, AttributeError) as exc:
            raise UsageGateRefused(f"refusing: refresh state file {path} is unreadable ({type(exc).__name__})") from exc
        if last is None:
            raise UsageGateRefused(f"refusing: refresh state file {path} has no usable last_refresh_at")
        if (now - last).total_seconds() / 60 < interval:
            raise UsageGateRefused(
                f"refusing: codex reading is stale and a refresh already ran at {last.isoformat()} "
                f"(at most one per {interval} min)"
            )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"last_refresh_at": now.isoformat().replace("+00:00", "Z")}))


def _refreshed(cfg: dict, now: dt.datetime, stale: StaleReading) -> dict:
    _claim_refresh(cfg, now)
    try:
        _run_refresh(cfg)
    except Exception as exc:  # noqa: BLE001 - any failure of the refresh call is a refusal
        raise UsageGateRefused(f"refusing: codex refresh call failed ({str(exc)[:120]})") from exc
    after_now = _now_after(now)
    try:
        g = _judge_codex(cfg, after_now)
    except StaleReading as exc:
        raise UsageGateRefused(f"refusing: codex reading still stale after the refresh call ({exc.reason})") from exc
    g["refresh"] = {
        "reason": stale.reason,
        "before": stale.before,
        "after": {k: g[k] for k in ("weekly_used_pct", "five_hour_used_pct", "observed_at")},
        "refreshed_at": now.isoformat().replace("+00:00", "Z"),
    }
    return g


def _now_after(t: dt.datetime) -> dt.datetime:
    """The clock for the re-read: real time, but never earlier than the instant the refresh was claimed."""
    return max(_now(), t)


def gate_reading(kind: str, root: Path, *, now: dt.datetime | None = None) -> dict:
    if kind not in GATED_KINDS:
        raise ValueError(f"no usage gate for {kind!r}")
    now = now or _now()
    cfg = _load(Path(root))
    if kind == "codex":
        try:
            return _judge_codex(cfg, now)
        except StaleReading as stale:
            return _refreshed(cfg, now, stale)
    key = _need(cfg, "claude", "seat_key")
    seat = (_read_claude(cfg).get("seats") or {}).get(key)
    if not isinstance(seat, dict):
        raise UsageGateRefused(f"refusing: claude usage file has no {key!r} seat entry")
    g = _judge(
        "claude",
        cfg,
        now,
        verified=seat.get("verified"),
        observed=seat.get("observed_at"),
        weekly=seat.get("seven_day_used_pct"),
        five=seat.get("five_hour_used_pct"),
    )
    g["source"] = "claude_usage.json"
    g["seat_key"] = key
    return g
