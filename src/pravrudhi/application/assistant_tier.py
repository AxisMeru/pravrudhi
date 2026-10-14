"""The assistant tier (#122, engine half): is the Anthropic-backed assistant offerable right now?

`status(root)` returns {"state": "available" | "unavailable", "reason": None | "no_key" | "upstream_error" | "quota" |
"budget_floor" | "disabled"}. Unavailable when the config forces it or cannot be read (disabled), no configured key
environment variable is set (no_key), the last Anthropic-backed call within `failure_ttl_s` got a 4xx/5xx/quota answer
(upstream_error / quota), or the optional budget file's `remaining_usd` is under `budget_floor_usd` (budget_floor; a
configured budget file that is missing, unreadable or malformed counts as under the floor: fail closed). No budget file
configured skips that check. Call sites report with `record_outcome(http_status)`. Thresholds come from
`configs/assistant_tier.yaml`; nothing has a default in code.

BINDING CONTRACT for the first Anthropic-backed (CMA/A0) call site, and every one after it (#122):
  (a) the call takes `limits(root)["call_timeout_s"]` and falls back to the core result on timeout;
  (b) its per-session budget is drawn from `limits(root)["session_budget_usd"]`;
  (c) it reports `record_outcome(http_status)` (402/429 = quota, any other 4xx/5xx = upstream_error).
No call site exists in the engine or the app today, so none is wired here.
"""

from __future__ import annotations

import json
import math
import os
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml

from pravrudhi.application.config_files import config_file

CONFIG_NAME = "assistant_tier.yaml"
QUOTA_STATUSES = (402, 429)

_lock = threading.Lock()
_last: dict[str, Any] = {"status": None, "at": None}


def _unavailable(reason: str) -> dict[str, Any]:
    return {"state": "unavailable", "reason": reason}


def _num(v: Any) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    x = float(v)
    return x if math.isfinite(x) else None


def _load(root: Path) -> dict[str, Any] | None:
    try:
        cfg = yaml.safe_load(config_file(root, CONFIG_NAME).read_text())
    except (OSError, ValueError, RecursionError, yaml.YAMLError):
        return None
    return cfg if isinstance(cfg, dict) else None


def record_outcome(http_status: int, *, now: Callable[[], float] = time.monotonic) -> None:
    """Called from every Anthropic-backed call site with the HTTP status it got (any non-HTTP failure: pass 599)."""
    with _lock:
        _last["status"], _last["at"] = int(http_status), now()


def reset() -> None:
    with _lock:
        _last["status"], _last["at"] = None, None


def _failure_reason(ttl: float, now: float) -> str | None:
    with _lock:
        status, at = _last["status"], _last["at"]
    if status is None or at is None or now - at > ttl or status < 400:
        return None
    return "quota" if status in QUOTA_STATUSES else "upstream_error"


def _budget_ok(path: Any, floor: float) -> bool:
    try:
        remaining = _num(json.loads(Path(str(path)).read_text()).get("remaining_usd"))
    except (OSError, ValueError, AttributeError, RecursionError):
        return False
    return remaining is not None and remaining >= floor


def status(root: Path, *, env: Any = None, now: Callable[[], float] = time.monotonic) -> dict[str, Any]:
    env = os.environ if env is None else env
    cfg = _load(Path(root))
    if cfg is None or cfg.get("enabled") is not True or cfg.get("force_unavailable") is not False:
        return _unavailable("disabled")
    names = cfg.get("key_env_names")
    if not isinstance(names, list) or not names or not any(isinstance(n, str) and env.get(n) for n in names):
        return _unavailable("no_key")
    ttl, floor = _num(cfg.get("failure_ttl_s")), _num(cfg.get("budget_floor_usd"))
    if ttl is None or floor is None or ttl < 0 or floor < 0:
        return _unavailable("disabled")
    reason = _failure_reason(ttl, now())
    if reason:
        return _unavailable(reason)
    if cfg.get("budget_file") is not None and not _budget_ok(cfg["budget_file"], floor):
        return _unavailable("budget_floor")
    return {"state": "available", "reason": None}


def limits(root: Path) -> dict[str, float] | None:
    """Per-call timeout (s) and per-session budget (USD) for a CMA/A0 call site; None when the config is unreadable."""
    cfg = _load(Path(root))
    timeout, budget = (_num(cfg.get("call_timeout_s")), _num(cfg.get("session_budget_usd"))) if cfg else (None, None)
    if timeout is None or budget is None or timeout <= 0 or budget <= 0:
        return None
    return {"call_timeout_s": timeout, "session_budget_usd": budget}
