# ruff: noqa: E501
"""#1038 PR-4: a Python port of the `tenancy.wake_*` SQL functions (supabase/wake_migration_v1.sql), used ONLY in tests.

`FakeWakeDb.call(fn, *args)` has the shape of `PgTenancyStore.wake_call`: two `PgWakeStore`s built on the same `FakeWakeDb` are two
containers sharing one database. It mirrors the SQL's rules (database clock, expiry on every read, one live request per judge,
the user-row lock, the unique nonce insert); the SQL itself is read by the structure test and, where docker and a Postgres image
are available, run by tests/test_wake_pg_it.py. Invented ids only; nothing here touches a network or a database."""

from __future__ import annotations

import json
import re
import threading
from collections.abc import Callable
from typing import Any

_NONCE = re.compile(r"[0-9a-f]{32}")
ANSWERS = ("granted", "already_awake", "refused", "failed")


def _lease_ok(v: Any) -> bool:
    return isinstance(v, str) and 1 <= len(v) <= 200 and "|" not in v


class FakeWakeDb:
    def __init__(self, clock: Callable[[], float]) -> None:
        self.clock = clock
        self.requests: dict[str, dict[str, Any]] = {}
        self.nonces: dict[str, float] = {}
        self.limits: dict[str, dict[str, Any]] = {}
        self.calls: list[str] = []
        self.down = False
        self._lock = threading.RLock()

    # the shape of PgTenancyStore.wake_call
    def call(self, fn: str, *args: Any) -> Any:
        self.calls.append(fn)
        if self.down:
            raise ConnectionError("the store is down")
        with self._lock:
            return getattr(self, fn)(*args)

    def _purge(self) -> None:
        t = self.clock()
        for table in (self.requests, self.nonces):
            for k in [k for k, v in table.items() if (v["expires_at"] if isinstance(v, dict) else v) < t][:200]:
                del table[k]
        for k in [k for k, v in self.limits.items() if v["expires_at"] < t][:200]:
            del self.limits[k]

    def _latest(self, label: str, kind: str) -> dict[str, Any] | None:
        t = self.clock()
        rows = [r for r in self.requests.values() if r["label"] == label and r["kind"] == kind and r["expires_at"] > t]
        return max(rows, key=lambda r: r["created_at"]) if rows else None

    def wake_ensure(self, label: str, nonce: str, budget: float, req_ttl: float, row_ttl: float) -> dict[str, Any]:
        if label not in ("first", "second") or not _NONCE.fullmatch(nonce) or not 1 <= budget <= 3600 or not 60 <= row_ttl <= 86400:
            raise ValueError("bad wake request")
        t = self.clock()
        self._purge()
        r = self._latest(label, "wake")
        if r is not None and t - r["created_at"] < budget and (
            (r["answer"] is None and t - r["created_at"] <= req_ttl) or r["answer"] in ("granted", "already_awake")
        ):
            return {"reused": True}
        self.requests[nonce] = {"nonce": nonce, "label": label, "kind": "wake", "lease_ref": None, "created_at": t,
                                "expires_at": t + row_ttl, "answer": None, "answer_lease": None, "answered_at": None}
        return {"reused": False}

    def wake_extend(self, label: str, nonce: str, lease: str, req_ttl: float, row_ttl: float) -> dict[str, Any]:
        if label not in ("first", "second") or not _NONCE.fullmatch(nonce) or not _lease_ok(lease):
            raise ValueError("bad extend request")
        t = self.clock()
        self._purge()
        if any(r["label"] == label and r["kind"] == "extend" and r["expires_at"] > t and t - r["created_at"] < 30
               for r in self.requests.values()):
            return {"deduped": True}
        self.requests[nonce] = {"nonce": nonce, "label": label, "kind": "extend", "lease_ref": lease, "created_at": t,
                                "expires_at": t + row_ttl, "answer": None, "answer_lease": None, "answered_at": None}
        return {"deduped": False}

    def wake_open(self, limit: float, req_ttl: float) -> list[dict[str, Any]]:
        t = self.clock()
        rows = sorted((r for r in self.requests.values() if r["answer"] is None and r["expires_at"] > t and t - r["created_at"] <= req_ttl),
                      key=lambda r: r["created_at"])
        return [{k: r[k] for k in ("nonce", "label", "kind", "lease_ref")} for r in rows[: int(limit)]]

    def wake_deliver(self, results: str, req_ttl: float) -> dict[str, Any]:
        t, n = self.clock(), 0
        items = json.loads(results)
        if not isinstance(items, list):
            return {"applied": 0}
        for it in items[:20]:
            if not isinstance(it, dict):
                continue
            nc, st, ls = it.get("nonce"), it.get("state"), it.get("lease_ref")
            if not isinstance(nc, str) or not _NONCE.fullmatch(nc) or st not in ANSWERS:
                continue
            r = self.requests.get(nc)
            if r is None or r["answer"] is not None or r["expires_at"] <= t or t - r["created_at"] > req_ttl:
                continue
            r.update(answer=st, answer_lease=ls if _lease_ok(ls) else None, answered_at=t)
            n += 1
        return {"applied": n}

    def wake_state(self, label: str, req_ttl: float) -> dict[str, Any]:
        t = self.clock()
        r = self._latest(label, "wake")
        if r is None:
            return {"found": False}
        answer = "failed" if r["answer"] is None and t - r["created_at"] > req_ttl else r["answer"]
        return {"found": True, "answer": answer, "age_s": t - r["created_at"]}

    def wake_lease(self, label: str) -> dict[str, Any]:
        t = self.clock()
        rows = [r for r in self.requests.values() if r["label"] == label and r["kind"] == "wake" and r["answer"] in ("granted", "already_awake")
                and r["answer_lease"] is not None and r["expires_at"] > t]
        return {"lease": max(rows, key=lambda r: r["answered_at"])["answer_lease"] if rows else None}

    def wake_nonce(self, nonce: str, ttl: float) -> dict[str, Any]:
        if not _NONCE.fullmatch(nonce) or not 10 <= ttl <= 3600:
            raise ValueError("bad wake nonce")
        t = self.clock()
        self._purge()
        if nonce in self.nonces and self.nonces[nonce] < t:
            del self.nonces[nonce]
        if nonce in self.nonces:
            return {"first": False}
        self.nonces[nonce] = t + ttl
        return {"first": True}

    def wake_begin(self, user: str, per_day: float, cooldown: float, active_ttl: float, row_ttl: float) -> dict[str, Any]:
        t = self.clock()
        self._purge()
        row = self.limits.setdefault(user, {"starts": [], "active": None, "cool": 0.0, "expires_at": t + row_ttl})
        starts = [s for s in row["starts"] if s > t - 86400]
        act, cool = row["active"], row["cool"]
        if act is not None and t - act > active_ttl:
            act, cool = None, t + cooldown
        if act is not None:
            res, stamp = "existing", act
        elif cool > t:
            res, stamp = "cooldown", None
        elif len(starts) >= per_day:
            res, stamp = "limit", None
        else:
            res, stamp, act = "new", t, t
            starts.append(t)
        row.update(starts=starts, active=act, cool=cool, expires_at=t + row_ttl)
        return {"result": res, "stamp": stamp}

    def wake_active(self, user: str, active_ttl: float) -> dict[str, Any]:
        t = self.clock()
        row = self.limits.get(user)
        act = row["active"] if row is not None and row["expires_at"] > t else None
        if act is not None and t - act > active_ttl:
            act = None
        return {"stamp": act, "age_s": None if act is None else t - act}

    def wake_end(self, user: str, ok: bool, stamp: float | None, cooldown: float) -> dict[str, Any]:
        t = self.clock()
        row = self.limits.get(user)
        if row is None or (stamp is not None and (row["active"] is None or abs(row["active"] - stamp) > 0.001)):
            return {"applied": False}
        row["active"] = None
        if not ok and cooldown > 0:
            row["cool"] = t + cooldown
        return {"applied": True}
