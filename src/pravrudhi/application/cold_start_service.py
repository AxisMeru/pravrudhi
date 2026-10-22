"""#1038 cold start with progress, PR-4: the service behind the wake routes, with NO job object and no engine-run screening.

Production serves from several containers, so nothing here lives in process memory: the coordination state (judge-wake requests,
the waker's answers, replay nonces, per-user limit counters) is behind the `WakeStore` interface, shared by every container
(`wake_store.py`). A status read derives each judge's state from the shared request rows and the endpoint's own /health, so
any container can answer any poll, and the box-side waker's poll can land anywhere. The CLIENT submits the screening itself
once the status is `ready` or `partial`; a `partial` status carries a short-lived ticket (`wake_ticket.py`) that lets that one
screening skip only the second judge's offline refusal and run with the second judge refused (every established element is
referred). Nothing about a case ever reaches this module.

* the user side: `start` (an allowlisted user asks for a wake; the identical `NOT_ALLOWED` marker for every refusal),
  `status` (the coarse state, and a ticket once the wake has ended ready or partial);
* the waker side (contract v2, pull): `requests` and `deliver` (HMAC-SHA256, 60 s window, nonce once across containers);
* `extend`: lease extensions for the judges, asked only with a valid ticket and rate-deduped.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping
from typing import Any, Literal

from pravrudhi.application import wake_ticket
from pravrudhi.application.judge_wake import (
    HARD_MAX_BUDGET_S,
    ROLES,
    WakeConfig,
    reason_of,
    sign,
    state_of,
    verify_http,
)
from pravrudhi.application.wake_store import WakeStore

NOT_ALLOWED: dict[str, str] = {"error": "judges_offline", "wake": "not_allowed"}  # the ONE marker, whatever the cause
POLL_PATH = "/internal/wake-requests"
RESULT_PATH = "/internal/wake-results"
EXTEND_EVERY_S = 30.0  # per judge, per container (the database dedupes across containers as well)


def _uid(user_id: str) -> str:
    """One spelling of a user id for the limiter and the ticket (ids are compared case-insensitively)."""
    return user_id.strip().lower()


def second_judge_used(result: Mapping[str, Any]) -> bool:
    """The second judge read an element iff some element is ESTABLISHED (an element is established only when BOTH judges
    established it; an element the first judge rejected never reaches the second)."""
    for c in result.get("contracts") or []:
        for e in (c.get("elements") if isinstance(c, Mapping) else None) or []:
            if isinstance(e, Mapping) and e.get("status") == "established":
                return True
    return False


class _SharedNonces:
    """The `nonces` argument of `verify_http`, backed by the shared store: once across every container."""

    def __init__(self, store: WakeStore) -> None:
        self._store = store

    def first_use(self, nonce: str) -> bool:
        return self._store.nonce_first_use(nonce)


class ColdStartService:
    def __init__(
        self,
        cfg: WakeConfig,
        *,
        secret: str,
        store: WakeStore,
        health: Callable[[str], str],
        wall: Callable[[], float] = time.time,
    ) -> None:
        self._cfg, self._secret, self._store, self._health, self._wall = cfg, secret, store, health, wall
        self._nonces = _SharedNonces(store)
        self._last_extend: dict[str, float] = {}
        self._budget = min(cfg.budget_s, HARD_MAX_BUDGET_S)

    # ---- the user side -------------------------------------------------------------------------------------------------

    def start(self, user_id: str) -> tuple[Literal["ready", "accepted", "not_allowed"], dict[str, Any]]:
        """An allowlisted user asks for a wake. `ready` = every judge is already up; `accepted` = a wake is in progress (new or
        the user's existing one); `not_allowed` = the one identical marker (limit, cooldown, store unreachable: same answer)."""
        uid = _uid(user_id)
        readings = {role: self._health(role) for role in ROLES}
        if all(r == "ready" for r in readings.values()):
            return "ready", self._evaluate(uid, readings, closing=False)  # a start with every judge up stores and reads nothing
        try:
            begun, stamp = self._store.limit_begin(uid)
            if begun not in ("new", "existing"):
                return "not_allowed", dict(NOT_ALLOWED)
            try:
                for role in ROLES:
                    if readings[role] != "ready":
                        self._store.ensure_wake(role, self._budget)
            except Exception:  # noqa: BLE001 - nothing was started: free the slot without a cooldown
                if begun == "new":
                    self._store.limit_end(uid, ok=True, stamp=stamp)
                raise
        except Exception:  # noqa: BLE001 - a store that cannot answer never grants a wake
            return "not_allowed", dict(NOT_ALLOWED)
        return "accepted", self._evaluate(uid, readings)

    def status(self, user_id: str) -> dict[str, Any]:
        """The user's coarse wake state (and a ticket once it ended ready or partial). Raises when the store is unreachable."""
        uid = _uid(user_id)
        return self._evaluate(uid, {role: self._health(role) for role in ROLES})

    def _evaluate(self, uid: str, readings: Mapping[str, str], *, closing: bool = True) -> dict[str, Any]:
        # A STATUS poll that sees every judge ready must still END the user's wake (a stamped, idempotent end). Without it a
        # FULL success leaves the slot active, and the next start after the dead-wake bound is released as FAILED (a spurious
        # refusal plus a failed-wake cooldown). Only the start() fast path (every judge already up, no wake begun) skips the
        # store entirely.
        all_ready = all(r == "ready" for r in readings.values())
        active = None if (not closing and all_ready) else self._store.limit_active(uid)
        stamp, elapsed = active if active is not None else (None, 0.0)
        models: dict[str, str] = {}
        timed_out: set[str] = set()
        for role in ROLES:
            if readings[role] == "ready":
                models[role] = "ready"
                continue
            seen = self._store.wake_state(role) if active is not None else None
            if seen is None:  # no wake of this user in progress (or it is long dead), or the request is gone: expired
                models[role] = "failed"
                timed_out.add(role)
            elif seen[0] in ("refused", "failed"):
                models[role] = "failed"
            elif elapsed >= self._budget:
                models[role] = "failed"
                timed_out.add(role)
            else:
                models[role] = "starting" if seen[0] in ("granted", "already_awake") else "waiting"
        state = state_of(models, first_timed_out="first" in timed_out)
        if state != "waking" and stamp is not None:
            self._store.limit_end(uid, ok=state in ("ready", "partial"), stamp=stamp)  # idempotent: a stamped end
        body: dict[str, Any] = {
            "wake": {
                "state": state,
                "models": [{"role": r, "state": models[r], "progress": None} for r in ROLES],
                "reason": reason_of(state),
                "deadline_s": int(self._budget + self._cfg.deadline_margin_s),
                "poll_after_s": self._cfg.poll_after_s,
            }
        }
        if state in ("ready", "partial"):
            mode: wake_ticket.Mode = "full" if state == "ready" else "partial"
            body["ticket"] = {
                "value": wake_ticket.issue(self._secret, uid, mode, self._cfg.ticket_ttl_s, clock=self._wall),
                "mode": mode,
                "expires_in_s": int(self._cfg.ticket_ttl_s),
            }
        return body

    # ---- the ticket (no I/O) ---------------------------------------------------------------------------------------------

    def ticket_mode(self, value: str | None, user_id: str | None) -> wake_ticket.Mode | None:
        return wake_ticket.verify(self._secret, value, user_id, clock=self._wall)

    def extend(self) -> None:
        """Ask the waker to extend each judge's lease. Best effort, never raises; the caller calls it only for a VALID ticket."""
        now = self._wall()
        for role in ROLES:
            if now - self._last_extend.get(role, 0.0) < EXTEND_EVERY_S:
                continue
            self._last_extend[role] = now
            try:
                lease = self._store.lease_for(role)
                if lease is not None:
                    self._store.request_extend(role, lease)
            except Exception:  # noqa: BLE001 - an extension is a convenience; the screening never waits on it
                continue

    # ---- the waker side (contract v2) -----------------------------------------------------------------------------------

    def requests(self, ts: str | None, nonce: str | None, sig: str | None) -> tuple[int, dict[str, Any]]:
        nonces: Any = self._nonces
        if not verify_http(self._secret, "GET", POLL_PATH, ts, nonce, sig, None, nonces=nonces, clock=self._wall):
            return 401, {}
        try:
            rows = self._store.open_requests()
        except Exception:  # noqa: BLE001 - nothing is listed while the store is down
            return 503, {}
        now = int(self._wall())
        out: list[dict[str, str]] = []
        for r in rows:
            try:
                payload: dict[str, Any] = {"v": 1, "kind": r["kind"], "label": r["label"], "ts": now, "nonce": r["nonce"]}
                lease = r.get("lease_ref")
                if lease is not None:
                    payload["lease_ref"] = lease
                signature = sign(self._secret, now, r["nonce"], r["label"], r["kind"], lease)
            except (KeyError, ValueError):
                continue  # a malformed row is never listed
            out.append({"body": json.dumps(payload, separators=(",", ":")), "sig": "v1=" + signature})
        return 200, {"v": 1, "requests": out}

    def deliver(self, ts: str | None, nonce: str | None, sig: str | None, body: bytes) -> tuple[int, dict[str, Any]]:
        nonces: Any = self._nonces
        if not verify_http(self._secret, "POST", RESULT_PATH, ts, nonce, sig, body, nonces=nonces, clock=self._wall):
            return 401, {}
        try:
            data = json.loads(body.decode("utf-8"))
        except ValueError:
            return 400, {"error": "bad_request"}
        if not isinstance(data, dict) or data.get("v") != 1:
            return 400, {"error": "bad_request"}
        try:
            return 200, {"v": 1, "applied": self._store.deliver(data.get("results"))}
        except Exception:  # noqa: BLE001
            return 503, {}
