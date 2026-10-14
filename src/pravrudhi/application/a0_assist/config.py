"""Fail-closed reading of the A0 assist settings from `configs/assistant_tier.yaml` (shared with `assistant_tier`, #388).

Nothing here has a default in code: a missing key, a wrong type, a non-finite or out-of-range number, more than two
decimals in a USD amount, or an enabled route without its product agent ids is an `AssistConfigError`
and the route stays OFF. USD amounts become integer cents (`Decimal`, never a float comparison)."""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import yaml

from pravrudhi.application.config_files import config_file

CONFIG_NAME = "assistant_tier.yaml"
MAX_SESSION_CENTS = 100
MAX_CEILING_CENTS = 10_000


class AssistConfigError(ValueError):
    """The assist configuration cannot be trusted: the route stays off and the tier reports not configured."""


@dataclass(frozen=True, slots=True)
class AssistConfig:
    route_enabled: bool
    daily_ceiling_cents: int
    session_budget_cents: int
    call_timeout_s: float
    abandon_after_min: int
    store_timeout_s: float
    max_concurrent_sessions: int
    agent_id: str | None
    agent_version: int | None
    environment_id: str | None
    key_env_names: tuple[str, ...]
    workspace_id_env: str
    store_dsn_env: str
    sweeper_dsn_env: str
    store_ca_file: str | None
    runs_per_user_per_day: int
    runs_per_org_per_day: int
    per_ip_per_minute: int
    delete_after_run: bool
    retention_max_hours: float
    sweep_interval_min: int
    max_pending_deletes: int
    canary_interval_min: int
    canary_stale_min: int


def _need(cfg: dict[str, Any], key: str) -> Any:
    if key not in cfg:
        raise AssistConfigError(f"{CONFIG_NAME}: missing key {key}")
    return cfg[key]


def _bool(cfg: dict[str, Any], key: str) -> bool:
    v = _need(cfg, key)
    if not isinstance(v, bool):
        raise AssistConfigError(f"{CONFIG_NAME}: {key} must be true or false")
    return v


def _int(cfg: dict[str, Any], key: str, lo: int, hi: int) -> int:
    v = _need(cfg, key)
    if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
        raise AssistConfigError(f"{CONFIG_NAME}: {key} must be an integer in [{lo}, {hi}]")
    return v


def _num(cfg: dict[str, Any], key: str, lo: float, hi: float) -> float:
    v = _need(cfg, key)
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(float(v)) or not lo < float(v) <= hi:
        raise AssistConfigError(f"{CONFIG_NAME}: {key} must be a number in ({lo}, {hi}]")
    return float(v)


def usd_to_cents(v: Any, key: str, hi_cents: int) -> int:
    """USD to whole cents. At most two decimals (an amount like 0.255 is a refusal, not a rounding), positive, finite, bounded."""
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(float(v)):
        raise AssistConfigError(f"{CONFIG_NAME}: {key} must be a finite number of USD")
    try:
        d = Decimal(str(v))
    except InvalidOperation as exc:
        raise AssistConfigError(f"{CONFIG_NAME}: {key} is not a number") from exc
    cents = d * 100
    if cents != cents.to_integral_value() or not 0 < cents <= hi_cents:
        raise AssistConfigError(f"{CONFIG_NAME}: {key} must be a positive USD amount in whole cents, at most {hi_cents} cents")
    return int(cents)


def _str_or_none(cfg: dict[str, Any], key: str) -> str | None:
    v = _need(cfg, key)
    if v is None:
        return None
    if not isinstance(v, str) or not v.strip():
        raise AssistConfigError(f"{CONFIG_NAME}: {key} must be a non-empty string or null")
    return v.strip()


def _env_name(cfg: dict[str, Any], key: str) -> str:
    v = _need(cfg, key)
    if not isinstance(v, str) or not v.isidentifier() or not v.isupper():
        raise AssistConfigError(f"{CONFIG_NAME}: {key} must be the NAME of an environment variable (upper case)")
    return v


def parse(cfg: Any) -> AssistConfig:
    if not isinstance(cfg, dict):
        raise AssistConfigError(f"{CONFIG_NAME} is not a mapping")
    names = _need(cfg, "key_env_names")
    if not isinstance(names, list) or not names or not all(isinstance(n, str) and n.isidentifier() for n in names):
        raise AssistConfigError(f"{CONFIG_NAME}: key_env_names must be a non-empty list of environment variable names")
    version = _need(cfg, "agent_version")
    if version is not None and (isinstance(version, bool) or not isinstance(version, int) or version < 1):
        raise AssistConfigError(f"{CONFIG_NAME}: agent_version must be a positive integer or null")
    out = AssistConfig(
        route_enabled=_bool(cfg, "assist_route_enabled"),
        daily_ceiling_cents=usd_to_cents(_need(cfg, "daily_ceiling_usd"), "daily_ceiling_usd", MAX_CEILING_CENTS),
        session_budget_cents=usd_to_cents(_need(cfg, "session_budget_usd"), "session_budget_usd", MAX_SESSION_CENTS),
        call_timeout_s=_num(cfg, "call_timeout_s", 0, 600),
        abandon_after_min=_int(cfg, "abandon_after_min", 1, 24 * 60),
        store_timeout_s=_num(cfg, "store_timeout_s", 0, 30),
        max_concurrent_sessions=_int(cfg, "max_concurrent_sessions", 1, 50),
        agent_id=_str_or_none(cfg, "agent_id"),
        agent_version=version,
        environment_id=_str_or_none(cfg, "environment_id"),
        key_env_names=tuple(names),
        workspace_id_env=_env_name(cfg, "workspace_id_env"),
        store_dsn_env=_env_name(cfg, "store_dsn_env"),
        sweeper_dsn_env=_env_name(cfg, "sweeper_dsn_env"),
        store_ca_file=_str_or_none(cfg, "store_ca_file"),
        runs_per_user_per_day=_int(cfg, "assist_runs_per_user_per_day", 1, 10_000),
        runs_per_org_per_day=_int(cfg, "assist_runs_per_org_per_day", 1, 100_000),
        per_ip_per_minute=_int(cfg, "assist_per_ip_per_minute", 1, 10_000),
        delete_after_run=_bool(cfg, "delete_after_run"),
        retention_max_hours=_num(cfg, "retention_max_hours", 0, 24 * 30),
        sweep_interval_min=_int(cfg, "sweep_interval_min", 1, 24 * 60),
        max_pending_deletes=_int(cfg, "max_pending_deletes", 1, 1000),
        canary_interval_min=_int(cfg, "canary_interval_min", 1, 24 * 60),
        canary_stale_min=_int(cfg, "canary_stale_min", 1, 7 * 24 * 60),
    )
    if out.session_budget_cents > out.daily_ceiling_cents:
        raise AssistConfigError(f"{CONFIG_NAME}: session_budget_usd exceeds daily_ceiling_usd")
    if out.canary_stale_min <= out.canary_interval_min:
        raise AssistConfigError(f"{CONFIG_NAME}: canary_stale_min must exceed canary_interval_min")
    if out.route_enabled:
        if not (out.agent_id and out.agent_version and out.environment_id):
            raise AssistConfigError(
                f"{CONFIG_NAME}: the route is enabled but the product agent_id, agent_version and environment_id are not all set"
            )
        if not out.store_ca_file:
            raise AssistConfigError(f"{CONFIG_NAME}: the route is enabled but store_ca_file is not set (the pinned CA path)")
        if not out.delete_after_run:
            raise AssistConfigError(f"{CONFIG_NAME}: the route refuses to enable with delete_after_run false")
        if out.abandon_after_min * 60 < out.call_timeout_s:
            raise AssistConfigError(f"{CONFIG_NAME}: abandon_after_min must cover call_timeout_s")
    return out


def load(root: Path) -> AssistConfig:
    try:
        raw = yaml.safe_load(config_file(Path(root), CONFIG_NAME).read_text())
    except (OSError, ValueError, RecursionError, yaml.YAMLError) as exc:
        raise AssistConfigError(f"{CONFIG_NAME} cannot be read: {type(exc).__name__}") from exc
    return parse(raw)


def load_or_none(root: Path) -> tuple[AssistConfig | None, str | None]:
    """(config, None) or (None, why). Never raises: callers treat None as not configured."""
    try:
        return load(root), None
    except AssistConfigError as exc:
        return None, str(exc)
