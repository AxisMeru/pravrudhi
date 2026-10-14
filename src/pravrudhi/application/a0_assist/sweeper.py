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
from datetime import UTC, datetime
from typing import Any

import httpx

from pravrudhi.application.a0_assist import guard
from pravrudhi.application.a0_assist.config import AssistConfig
from pravrudhi.application.a0_assist.store import SpendStore, StoreError
from pravrudhi.application.a0_assist.transport import AnthropicTransport, TransportRefused, UpstreamError

DELETED = (200, 204, 404)
UPSTREAM = (UpstreamError, TransportRefused, httpx.HTTPError)
MAX_PER_PASS = 50
PRUNE_EVERY_S = 24 * 3600


def _epoch(v: Any) -> float | None:
    if not isinstance(v, str):
        return None
    try:
        return datetime.fromisoformat(v.replace("Z", "+00:00")).astimezone(UTC).timestamp()
    except ValueError:
        return None


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
        scan_orphans: bool | None = None,
        wall_clock: Callable[[], float] = time.time,
        min_age_min: int = 0,
        canary_enabled: bool | None = None,
        prune_enabled: bool | None = None,
    ) -> None:
        if mode not in ("engine", "backstop"):
            raise ValueError("mode is engine or backstop")
        self._mode = mode
        self._scan_orphans = (mode == "engine") if scan_orphans is None else scan_orphans
        self._wall = wall_clock
        self._min_age = min_age_min
        self._canary_on = (mode == "backstop") if canary_enabled is None else canary_enabled
        self._prune_on = (mode == "backstop") if prune_enabled is None else prune_enabled
        self._delete_canary_ok = False
        self._workspace_ok = False
        self._cfg, self._store, self._tr, self._pauses, self._engine = cfg, sweeper_store, transport, pauses, engine_store
        self._clock = clock
        self._last_canary: float | None = None
        self._last_prune: float | None = None

    def _delete_pending(self, out: dict[str, Any]) -> set[str]:
        assert self._store is not None and self._tr is not None
        pending = self._store.list_pending(self._min_age)
        out["pending"] = len(pending)
        for row in pending[:MAX_PER_PASS]:
            sid = row["anthropic_session_id"]
            try:
                status = self._tr.delete_session(sid)
            except UPSTREAM:
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
                with contextlib.suppress(*UPSTREAM):
                    self._tr.interrupt(sid)
        return {r["anthropic_session_id"] for r in pending}
        return {r["anthropic_session_id"] for r in pending}

    def _orphans(self, out: dict[str, Any], known: set[str]) -> None:
        """Sessions of every configured workflow agent (screening first) that the store does not list as pending and that
        are older than `abandon_after_min` (a crash between creating a session and recording it): delete them too. Each
        listing is filtered by a configured agent id, so a session of any other agent (a research session) can never be
        returned, and a young in-flight session is spared by its age."""
        assert self._tr is not None
        cutoff = self._wall() - self._cfg.abandon_after_min * 60
        out["orphans_deleted"] = 0
        for agent_id in self._cfg.all_agent_ids():  # every workflow's agent: each lists only its own sessions
            try:
                for item in self._tr.list_sessions(agent_id):
                    sid, created = item.get("id"), _epoch(item.get("created_at"))
                    if not isinstance(sid, str) or sid in known or created is None or created > cutoff:
                        continue
                    with contextlib.suppress(*UPSTREAM):
                        if self._tr.delete_session(sid) in DELETED:
                            out["orphans_deleted"] += 1
            except UPSTREAM:
                out["orphan_scan"] = "failed"

    def _workspace_check(self) -> None:
        """The engine checks itself: its key must get a 200 from GET /v1/models with the configured workspace id, or the tier is
        paused (fail closed; no value in any log). Runs on every pass until it succeeds (a key change needs a restart)."""
        assert self._tr is not None
        self._workspace_ok = self._tr.workspace_ok() is True
        self._pauses.set("workspace", None if self._workspace_ok else "workspace_check_failed")

    def _delete_canary(self) -> None:
        """A key that cannot delete would make every delete fail: create a session without events and delete it. Runs on the
        first pass of every process (a key change needs a restart) and again on every pass until it succeeds. A failure
        pauses the tier."""
        assert self._tr is not None
        ok = False
        try:
            sid = self._tr.create_session(
                self._cfg.agent_id or "",
                self._cfg.agent_version or 0,
                self._cfg.environment_id or "",
                self._cfg.session_budget_cents,
            )["id"]
            ok = self._tr.delete_session(sid) in DELETED
        except (*UPSTREAM, KeyError, TypeError):
            ok = False
        self._delete_canary_ok = ok
        self._pauses.set("delete_canary", None if ok else "delete_canary_failed")

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
            if self._canary_on:
                guard.run_canary(None, self._pauses)
            return out
        try:
            if self._mode == "engine" and not self._workspace_ok:
                self._workspace_check()
            if self._mode == "engine" and self._workspace_ok and not self._delete_canary_ok and self._cfg.agent_id:
                self._delete_canary()
            known = self._delete_pending(out)
            if self._scan_orphans and self._cfg.all_agent_ids():
                self._orphans(out, known)
            if self._mode == "engine":
                # the engine role's functions: reservations belong to the engine, and the sweep marker is what its own pause rules
                # read. The sweeper role is NOT granted either, so the backstop never calls them.
                out["abandoned"] = self._store.abandon_stale(self._cfg.abandon_after_min).get("abandoned")
                self._store.sweep_ok()
            self._pauses.set("sweeper", None)
        except StoreError:
            out["error"] = "store"
            self._pauses.set("sweeper", "sweeper_store_error")
        now = self._clock()
        if self._canary_on and (self._last_canary is None or now - self._last_canary >= self._cfg.canary_interval_min * 60):
            out["canary"] = guard.run_canary(self._store, self._pauses)
            self._last_canary = now
        if self._prune_on and (self._last_prune is None or now - self._last_prune >= PRUNE_EVERY_S):
            try:
                out["pruned"] = self._store.prune()
                self._last_prune = now
            except StoreError:
                out["pruned"] = None
        if self._mode == "engine":  # the sweeper role may not read status(); the backstop never evaluates the pause rules
            guard.check_store(self._engine if self._engine is not None else self._store, self._cfg, self._pauses)
        return out

    def run_loop(self, stop: threading.Event) -> None:
        while not stop.is_set():
            self.run_once()
            stop.wait(self._cfg.sweep_interval_min * 60)
