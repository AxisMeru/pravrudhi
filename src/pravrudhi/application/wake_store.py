"""#1038 PR-4: the coordination state of cold start, behind one small interface so it can be shared by every container.

Why: production serves from several containers at once, each one process. A wake the user starts on one container is polled by
the box-side waker on another and read by the user on a third; process memory cannot carry that. Only COORDINATION rows are
shared (judge-wake requests and the waker's answers, replay nonces, per-user limit counters). Facts, results, paper text and
screening inputs or outputs never reach this module.

* `MemoryWakeStore`: the in-process implementation (a local engine, tests, `PRAVRUDHI_TENANCY_BACKEND` unset or `file`); it is
  built from the existing `WakeBoard`, `NonceStore` and `WakeLimiter`, so behaviour is exactly that of PR #625/#626.
* `PgWakeStore`: the same interface over the hosted tenancy database, reached ONLY through the SECURITY DEFINER functions
  `tenancy.wake_*` (supabase/wake_migration_v2.sql) as the same narrow login role as the tenancy store. Every duration is
  measured by the database clock, so containers whose clocks differ still agree. Any store failure raises
  `TenancyStoreDown`; the service reads that as a refusal, never as permission.
"""

from __future__ import annotations

import json
import secrets
import time
from collections.abc import Callable
from typing import Any, Protocol

from pravrudhi.application.judge_wake import NonceStore, WakeBoard, WakeConfig
from pravrudhi.application.wake_policy import WakeLimiter

LIMIT_RESULTS = ("new", "existing", "cooldown", "limit")


class WakeStore(Protocol):
    def ensure_wake(self, label: str, budget_s: float) -> None: ...
    def request_extend(self, label: str, lease_ref: str) -> None: ...
    def open_requests(self) -> list[dict[str, Any]]: ...
    def deliver(self, results: Any) -> int: ...
    def wake_state(self, label: str) -> tuple[str | None, float] | None: ...
    def lease_for(self, label: str) -> str | None: ...
    def nonce_first_use(self, nonce: str) -> bool: ...
    def limit_begin(self, user: str) -> tuple[str, float | None]: ...
    def limit_active(self, user: str) -> tuple[float, float] | None: ...
    def limit_end(self, user: str, *, ok: bool, stamp: float | None) -> None: ...


class MemoryWakeStore:
    def __init__(
        self,
        cfg: WakeConfig,
        *,
        clock: Callable[[], float] = time.time,
        nonce: Callable[[], str] = lambda: secrets.token_hex(16),
    ) -> None:
        self._clock = clock
        self._board = WakeBoard(cfg.request_ttl_s, clock=clock, nonce=nonce)
        self._nonces = NonceStore(clock=clock)
        self._limiter = WakeLimiter(cfg.user_wakes_per_day, cfg.failed_cooldown_s, clock=clock)

    def ensure_wake(self, label: str, budget_s: float) -> None:
        latest = self._board.latest(label)
        if latest is not None and latest[1] < budget_s and latest[0] in (None, "granted", "already_awake"):
            return  # a live wake of this judge (shared by whoever needs it): never a second one
        self._board.request(label, "wake")

    def request_extend(self, label: str, lease_ref: str) -> None:
        self._board.request(label, "extend", lease_ref)

    def open_requests(self) -> list[dict[str, Any]]:
        return self._board.open_rows()

    def deliver(self, results: Any) -> int:
        return self._board.deliver(results)

    def wake_state(self, label: str) -> tuple[str | None, float] | None:
        return self._board.latest(label)

    def lease_for(self, label: str) -> str | None:
        return self._board.lease_for(label)

    def nonce_first_use(self, nonce: str) -> bool:
        return self._nonces.first_use(nonce)

    def limit_begin(self, user: str) -> tuple[str, float | None]:
        result = self._limiter.begin(user)
        return result, self._limiter.stamp(user)

    def limit_active(self, user: str) -> tuple[float, float] | None:
        stamp = self._limiter.stamp(user)
        return None if stamp is None else (stamp, max(0.0, self._clock() - stamp))

    def limit_end(self, user: str, *, ok: bool, stamp: float | None) -> None:
        self._limiter.end(user, ok=ok, stamp=stamp)


class PgWakeStore:
    """`call(fn, *args)` is `PgTenancyStore.wake_call`: one `select tenancy.<fn>(...)` returning parsed JSON."""

    def __init__(
        self,
        call: Callable[..., Any],
        cfg: WakeConfig,
        *,
        nonce: Callable[[], str] = lambda: secrets.token_hex(16),
    ) -> None:
        self._call, self._cfg, self._nonce = call, cfg, nonce
        self._req_ttl = float(cfg.request_ttl_s)
        self._row_ttl = float(cfg.row_ttl_s)
        self._active_ttl = 450.0  # the same dead-wake bound as the in-process limiter

    def ensure_wake(self, label: str, budget_s: float) -> None:
        self._call("wake_ensure", label, self._nonce(), float(budget_s), self._req_ttl, self._row_ttl)

    def request_extend(self, label: str, lease_ref: str) -> None:
        self._call("wake_extend", label, self._nonce(), lease_ref, self._req_ttl, self._row_ttl)

    def open_requests(self) -> list[dict[str, Any]]:
        rows = self._call("wake_open", 20.0, self._req_ttl)
        return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []

    def deliver(self, results: Any) -> int:
        if not isinstance(results, list):
            return 0
        out = self._call("wake_deliver", json.dumps(results[:20], separators=(",", ":")), self._req_ttl)
        n = out.get("applied") if isinstance(out, dict) else 0
        return n if isinstance(n, int) and not isinstance(n, bool) else 0

    def wake_state(self, label: str) -> tuple[str | None, float] | None:
        out = self._call("wake_state", label, self._req_ttl)
        if not isinstance(out, dict) or not out.get("found"):
            return None
        answer, age = out.get("answer"), out.get("age_s")
        if not isinstance(age, (int, float)) or isinstance(age, bool):
            return None
        return (answer if isinstance(answer, str) else None), float(age)

    def lease_for(self, label: str) -> str | None:
        out = self._call("wake_lease", label)
        lease = out.get("lease") if isinstance(out, dict) else None
        return lease if isinstance(lease, str) and lease else None

    def nonce_first_use(self, nonce: str) -> bool:
        out = self._call("wake_nonce", nonce, 180.0)
        return bool(out.get("first")) if isinstance(out, dict) else False

    def limit_begin(self, user: str) -> tuple[str, float | None]:
        out = self._call(
            "wake_begin", user.strip().lower(), float(self._cfg.user_wakes_per_day), float(self._cfg.failed_cooldown_s),
            self._active_ttl, 86400.0,
        )
        result = out.get("result") if isinstance(out, dict) else None
        stamp = out.get("stamp") if isinstance(out, dict) else None
        if result not in LIMIT_RESULTS:
            return "cooldown", None  # a malformed answer never grants a wake
        return str(result), (float(stamp) if isinstance(stamp, (int, float)) and not isinstance(stamp, bool) else None)

    def limit_active(self, user: str) -> tuple[float, float] | None:
        out = self._call("wake_active", user.strip().lower(), self._active_ttl)
        stamp = out.get("stamp") if isinstance(out, dict) else None
        age = out.get("age_s") if isinstance(out, dict) else None
        nums = (stamp, age)
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in nums):
            return None
        return float(stamp), float(age)  # type: ignore[arg-type]

    def limit_end(self, user: str, *, ok: bool, stamp: float | None) -> None:
        self._call("wake_end", user.strip().lower(), bool(ok), stamp, float(self._cfg.failed_cooldown_s))
