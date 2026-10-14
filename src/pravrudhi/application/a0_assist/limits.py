"""Anti-abuse limits for the assisted route: per user and per org per UTC day, and per client
address per minute, counted IN MEMORY.

These are ANTI-ABUSE, not spend control: the counters reset at a recycle and differ per overlapped worker, so the daily
ceiling in the spend store is the hard stop. A limit is checked BEFORE any reservation or call and
`admit` counts the run only when it admits it. Tables are bounded (least-recently-used eviction). An unreadable clock
value or a missing identity is a refusal, never an admission."""

from __future__ import annotations

import threading
import time
from collections import OrderedDict, deque
from collections.abc import Callable
from dataclasses import dataclass

MAX_KEYS = 10_000
DAY_S = 86_400


@dataclass(frozen=True, slots=True)
class Refusal:
    reason: str  # "per_user_day" | "per_org_day" | "per_ip_minute" | "no_identity"
    retry_after_s: int


class AssistLimiter:
    def __init__(
        self,
        per_user_day: int,
        per_org_day: int,
        per_ip_minute: int,
        *,
        now: Callable[[], float] = time.time,
        max_keys: int = MAX_KEYS,
    ) -> None:
        for name, v in (("per_user_day", per_user_day), ("per_org_day", per_org_day), ("per_ip_minute", per_ip_minute)):
            if isinstance(v, bool) or not isinstance(v, int) or v < 1:
                raise ValueError(f"{name} must be a positive integer")
        self.per_user_day, self.per_org_day, self.per_ip_minute = per_user_day, per_org_day, per_ip_minute
        self._now, self._max_keys = now, max_keys
        self._lock = threading.Lock()
        self._day: OrderedDict[str, tuple[int, int]] = OrderedDict()  # key -> (utc day number, count)
        self._ip: OrderedDict[str, deque[float]] = OrderedDict()

    def _evict(self, table: OrderedDict) -> None:  # type: ignore[type-arg]
        while len(table) > self._max_keys:
            table.popitem(last=False)

    def _day_count(self, key: str, day: int) -> int:
        d, n = self._day.get(key, (day, 0))
        return n if d == day else 0

    def admit(self, user_id: str | None, org_id: str | None, ip: str | None) -> Refusal | None:
        """None = admitted (and counted). Per-IP is checked first (cheapest), then per user, then per org; nothing is
        counted unless all three pass."""
        if not user_id:
            return Refusal("no_identity", 0)  # an anonymous caller never reaches the limiter; no user = no run
        with self._lock:
            t = float(self._now())
            day = int(t // DAY_S)
            q: deque[float] | None = None
            if ip:
                q = self._ip.get(ip)
                if q is None:
                    q = self._ip[ip] = deque()
                while q and t - q[0] >= 60:
                    q.popleft()
                if len(q) >= self.per_ip_minute:
                    return Refusal("per_ip_minute", max(1, int(60 - (t - q[0])) + 1))
            until_midnight = int(DAY_S - (t % DAY_S)) + 1
            ukey = f"u:{user_id}"
            okey = f"o:{org_id}" if org_id else None  # a workspace session has no org: only the per-user and per-IP limits apply
            if self._day_count(ukey, day) >= self.per_user_day:
                return Refusal("per_user_day", until_midnight)
            if okey and self._day_count(okey, day) >= self.per_org_day:
                return Refusal("per_org_day", until_midnight)
            if q is not None and ip:
                q.append(t)
                self._ip.move_to_end(ip)
            for key in (ukey, okey):
                if key is None:
                    continue
                self._day[key] = (day, self._day_count(key, day) + 1)
                self._day.move_to_end(key)
            self._evict(self._ip)
            self._evict(self._day)
            return None
