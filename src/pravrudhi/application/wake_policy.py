"""#1038 cold start, PR-2: the engine-side wake POLICY (pure; unreachable while `cold_start_wake` is off).

Three things, none of them touching the network or the route:

* who may wake: a VERIFIED, non-None user, authentication ON, and a NEW allowlist `PRAVRUDHI_WAKERS` (comma-separated user
  IDS only; empty or unset = nobody). It deliberately does not use `roles.role_of`/`is_admin` (role_of(None) is ADMIN when
  authentication is disabled) and `PRAVRUDHI_ADMINS` does not confer it. `user.role` and anything in a request body are ignored.
* how often: `WakeLimiter`, per user: a rolling-day cap that counts FAILED wakes too, a cooldown after a failed wake, and one
  active wake at a time (a second request for the same user is the existing one: idempotent).
* the partial run: `partial_run_breaker()`, a REQUEST-SCOPED, already-tripped `SecondJudgeCircuitBreaker` handed to that one run
  only, so every element the first judge establishes fails closed at once (refer, never a proof) without touching the
  process-wide breaker the other requests share."""

from __future__ import annotations

import os
import threading
import time
from collections import deque
from collections.abc import Callable, Mapping
from typing import Any

from pravrudhi.application.nyaya_judges import SecondJudgeCircuitBreaker

WAKERS_ENV = "PRAVRUDHI_WAKERS"
DAY_S = 24 * 3600.0


def waker_ids(env: Mapping[str, str] | None = None) -> frozenset[str]:
    """The allowlist of user ids, lowercased and stripped, blank entries dropped. Unset or empty means nobody may wake."""
    raw = (os.environ if env is None else env).get(WAKERS_ENV, "")
    return frozenset(part.strip().lower() for part in raw.split(",") if part.strip())


def may_wake(user: Any, *, auth_enabled: bool, wakers: frozenset[str]) -> bool:
    """True only for a verified user (non-None, non-blank id) whose USER ID is on the allowlist while auth is on. Never raises.
    Ids only (R2): the identity record has no email-verified field, so an email is never a credential here."""
    try:
        if user is None or not auth_enabled or not wakers:
            return False
        uid = str(user.id).strip().lower()
        return bool(uid) and uid in wakers
    except Exception:  # noqa: BLE001 - an odd user object is simply not allowed
        return False


class WakeLimiter:
    """Per-user wake limits with an injected clock. Thread-safe. `begin` is the single entry: it answers whether this user may
    start a wake now ("new"), already has one running ("existing"), or is refused ("limit" or "cooldown"). The caller reports
    the outcome with `end(user, ok=...)`; a failed wake counts toward the day cap and starts the cooldown."""

    def __init__(
        self, per_day: int, failed_cooldown_s: float, *, clock: Callable[[], float] = time.monotonic, active_ttl_s: float = 450.0
    ) -> None:
        self._per_day, self._cooldown, self._clock, self._active_ttl = per_day, failed_cooldown_s, clock, active_ttl_s
        self._starts: dict[str, deque[float]] = {}
        self._active: dict[str, float] = {}  # user -> when the active wake began; a lost `end` cannot pin a user forever
        self._cool_until: dict[str, float] = {}
        self._lock = threading.Lock()

    def begin(self, user: str) -> str:
        key = user.strip().lower()
        with self._lock:
            now = self._clock()
            began = self._active.get(key)
            if began is not None and now - began > self._active_ttl:
                del self._active[key]  # an `end` that never came (an error, a client gone, a swap): release it as a failed wake
                self._cool_until[key] = now + self._cooldown
                began = None
            if began is not None:
                return "existing"
            if self._cool_until.get(key, 0.0) > now:
                return "cooldown"
            starts = self._starts.setdefault(key, deque())
            while starts and now - starts[0] >= DAY_S:
                starts.popleft()
            if len(starts) >= self._per_day:
                return "limit"
            starts.append(now)
            self._active[key] = now
            return "new"

    def end(self, user: str, *, ok: bool) -> None:
        key = user.strip().lower()
        with self._lock:
            self._active.pop(key, None)
            if not ok and self._cooldown > 0:
                self._cool_until[key] = self._clock() + self._cooldown


def partial_run_breaker(ttl_s: float = 3600.0) -> SecondJudgeCircuitBreaker:
    """A fresh, ALREADY-TRIPPED breaker for ONE partial run (the first judge up, the second not). Handed to that run only:
    the process-wide breaker is never touched, so concurrent screenings are unaffected."""
    breaker = SecondJudgeCircuitBreaker(ttl_s=ttl_s)
    breaker.trip()
    return breaker
