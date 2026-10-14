"""The retention sweeper: deletes every Anthropic session this product created that is still undeleted, keeps the
sweep marker fresh, abandons stale spend reservations, runs the standing canary on its interval and prunes the log.

Two modes, matching the two database roles. "engine" (the narrow engine role): deletes pending sessions and records each
delete with `session_deleted`, abandons stale reservations, marks the sweep and reads the pause rules; it never runs the
canary or prune (those functions are not granted to that role). "backstop" (the sweeper role, in a separate process):
the same deletes recorded with `mark_deleted`, plus the standing canary on its interval and the daily prune.

`Sweeper.run_once()` is one pass and never raises: every store or upstream failure becomes a named entry in the
returned summary and leaves the session pending for the next pass (a delete is only recorded when the API answered
200, 204 or 404). It works only from the store's list of sessions this product created, never from a listing of the
account, so it cannot touch any other session. Run it in the engine (a background thread, `run_loop`) and, as a
backstop, from a separate process that holds only the sweeper role (`python -m pravrudhi.application.a0_assist.sweeper`).
"""

from __future__ import annotations

import contextlib
import threading
import time
from collections.abc import Callable
from typing import Any

import httpx

from pravrudhi.application.a0_assist import guard
from pravrudhi.application.a0_assist.config import AssistConfig
from pravrudhi.application.a0_assist.store import SpendStore, StoreError
from pravrudhi.application.a0_assist.transport import AnthropicTransport, TransportRefused, UpstreamError

DELETED = (200, 204, 404)
MAX_PER_PASS = 50
PRUNE_EVERY_S = 24 * 3600


class Sweeper:
    def __init__(
        self,
        cfg: AssistConfig,
        sweeper_store: SpendStore | None,
        transport: AnthropicTransport | None,
        pauses: guard.Pauses,
        *,
        engine_store: SpendStore | None = None,
        clock: Callable[[], float] = time.monotonic,
        mode: str = "backstop",
    ) -> None:
        if mode not in ("engine", "backstop"):
            raise ValueError("mode is engine or backstop")
        self._mode = mode
        self._cfg, self._store, self._tr, self._pauses, self._engine = cfg, sweeper_store, transport, pauses, engine_store
        self._clock = clock
        self._last_canary: float | None = None
        self._last_prune: float | None = None

    def _delete_pending(self, out: dict[str, Any]) -> None:
        assert self._store is not None and self._tr is not None
        pending = self._store.list_pending(0)
        out["pending"] = len(pending)
        for row in pending[:MAX_PER_PASS]:
            sid = row["anthropic_session_id"]
            try:
                status = self._tr.delete_session(sid)
            except (UpstreamError, TransportRefused, httpx.HTTPError):
                status = 599
            if status in DELETED:
                if self._mode == "engine":
                    self._store.session_deleted(sid, status)
                else:
                    self._store.mark_deleted(sid, status)
                out["deleted"] += 1
            else:
                out["failed"] += 1
                # a session that is still running cannot be deleted: stop it, the next pass deletes it
                with contextlib.suppress(UpstreamError, TransportRefused, httpx.HTTPError):
                    self._tr.interrupt(sid)

    def run_once(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "pending": 0,
            "deleted": 0,
            "failed": 0,
            "abandoned": None,
            "canary": None,
            "pruned": None,
            "error": None,
        }
        if self._store is None or self._tr is None:
            out["error"] = "not_configured"
            self._pauses.set("sweeper", "sweeper_not_configured")
            if self._mode == "backstop":
                guard.run_canary(None, self._pauses)
            return out
        try:
            self._delete_pending(out)
            out["abandoned"] = self._store.abandon_stale(self._cfg.abandon_after_min).get("abandoned")
            self._store.sweep_ok()
            self._pauses.set("sweeper", None)
        except StoreError:
            out["error"] = "store"
            self._pauses.set("sweeper", "sweeper_store_error")
        now = self._clock()
        if self._mode == "backstop" and (
            self._last_canary is None or now - self._last_canary >= self._cfg.canary_interval_min * 60
        ):
            out["canary"] = guard.run_canary(self._store, self._pauses)
            self._last_canary = now
        if self._mode == "backstop" and (self._last_prune is None or now - self._last_prune >= PRUNE_EVERY_S):
            try:
                out["pruned"] = self._store.prune()
                self._last_prune = now
            except StoreError:
                out["pruned"] = None
        guard.check_store(self._engine if self._engine is not None else self._store, self._cfg, self._pauses)
        return out

    def run_loop(self, stop: threading.Event) -> None:
        while not stop.is_set():
            self.run_once()
            stop.wait(self._cfg.sweep_interval_min * 60)
