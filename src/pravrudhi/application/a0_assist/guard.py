"""The assisted tier's pause rules, in one place.

`evaluate(status, cfg)` reads the store's status document and returns None (offerable) or a short code naming why the
tier must be paused. Every unreadable or unexpected field pauses (fail closed): a canary that never ran, is older than
`canary_stale_min`, failed, or errored all pause; so do an uninitialised store, a sweep that has not run recently,
too many undeleted sessions, and an undeleted session older than the retention window.

`Pauses` holds the named pause sources (the store gate, the canary run) and feeds the assistant tier: any active
source makes the tier unavailable with reason "disabled".
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

from pravrudhi.application.a0_assist.config import AssistConfig
from pravrudhi.application.a0_assist.store import SpendStore, StoreError


def _nonneg_int(v: Any) -> int | None:
    return v if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else None


def evaluate(status: Any, cfg: AssistConfig) -> str | None:
    if not isinstance(status, dict):
        return "store_status_unreadable"
    if status.get("initialised") is not True:
        return "store_not_initialised"
    if status.get("canary_failed") is not False:
        return "canary_failed"
    age = _nonneg_int(status.get("canary_age_s"))
    if age is None:
        return "canary_never_run"
    if age > cfg.canary_stale_min * 60:
        return "canary_stale"
    sweep = _nonneg_int(status.get("sweep_last_ok_age_s"))
    if sweep is None or sweep > cfg.sweep_interval_min * 60 * 3:
        return "sweep_stale"
    pending, oldest = _nonneg_int(status.get("pending_deletes")), _nonneg_int(status.get("oldest_pending_delete_age_s"))
    if pending is None or oldest is None:
        return "store_status_unreadable"
    if pending > cfg.max_pending_deletes:
        return "pending_deletes_count"
    if oldest > cfg.retention_max_hours * 3600:
        return "pending_deletes_age"
    return None


class Pauses:
    """Named pause sources. `reason()` is the first active code (None when nothing pauses the tier)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._active: dict[str, str] = {}

    def set(self, source: str, code: str | None) -> None:
        with self._lock:
            if code is None:
                self._active.pop(source, None)
            else:
                self._active[source] = code

    def reason(self) -> str | None:
        with self._lock:
            return next(iter(sorted(self._active.values())), None)

    def snapshot(self) -> dict[str, str]:
        with self._lock:
            return dict(self._active)


def check_store(store: SpendStore | None, cfg: AssistConfig, pauses: Pauses) -> str | None:
    """Read the store status and set the "store" pause source. An absent store or a failing read pauses."""
    if store is None:
        code: str | None = "store_not_configured"
    else:
        try:
            code = evaluate(store.status(), cfg)
        except StoreError:
            code = "store_unreachable"
    pauses.set("store", code)
    return code


def run_canary(sweeper_store: SpendStore | None, pauses: Pauses) -> dict[str, Any]:
    """Run the standing canary through the sweeper role. A failed finding or an erroring canary pauses the tier at once
    (an erroring canary is never read as clean); a clean run clears the "canary" source."""
    if sweeper_store is None:
        pauses.set("canary", "canary_error")
        return {"ok": False, "error": "no_sweeper_store"}
    try:
        res = sweeper_store.canary()
    except StoreError:
        pauses.set("canary", "canary_error")
        return {"ok": False, "error": "store"}
    ok = isinstance(res, dict) and res.get("ok") is True
    pauses.set("canary", None if ok else "canary_failed")
    return {"ok": ok, "error": None, "bad_functions": res.get("bad_functions") if isinstance(res, dict) else None}


Clock = Callable[[], float]
