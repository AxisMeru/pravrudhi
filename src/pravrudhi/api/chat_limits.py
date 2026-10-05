"""Per-user spend limits for `/api/chat`.

Every chat turn calls the engine's model endpoint with the operator's own key (`PRAVRUDHI_CHAT_API_KEY`), so any
signed-in account that reaches the engine spends it. This is a per-user, in-memory limiter: a small per-minute window
(a burst limit) and a daily cap (UTC day), both counted per authenticated user id, so one account cannot starve or
exhaust the others. Configurable by environment, with a refusal (HTTP 429 and `Retry-After`) on breach.

* `PRAVRUDHI_CHAT_RATE_PER_MINUTE` (default 10) and `PRAVRUDHI_CHAT_DAILY_CAP` (default 200) turns per user.
  `0` switches that limit off; an unreadable or negative value falls back to the default (never to "unlimited").
* An administrator (`PRAVRUDHI_ADMINS`) is not limited, and neither is a caller with no identity on an engine with
  authentication switched off (the single local operator); an anonymous caller on an engine that does
  authenticate (optional mode) is limited per client address.
* The table is bounded (10,000 keys): at the bound the least recently used key is evicted to make room for a new user,
  so a new user is never locked out. The state is per process: a restart resets the counters, which a daily cost cap that
  must survive restarts would have to persist instead.
"""

from __future__ import annotations

import os
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass

PER_MINUTE_ENV = "PRAVRUDHI_CHAT_RATE_PER_MINUTE"
DAILY_CAP_ENV = "PRAVRUDHI_CHAT_DAILY_CAP"
DEFAULT_PER_MINUTE = 10
DEFAULT_DAILY_CAP = 200
MAX_KEYS = 10_000


@dataclass(frozen=True, slots=True)
class Refusal:
    reason: str  # "per_minute" or "daily"
    retry_after_s: int


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw.strip())
    except ValueError:
        return default
    return value if value >= 0 else default


class ChatLimiter:
    """Thread-safe per-key limiter. `check` counts the turn when it admits it."""

    def __init__(
        self,
        per_minute: int | None = None,
        daily_cap: int | None = None,
        *,
        now: Callable[[], float] = time.time,
        max_keys: int = MAX_KEYS,
    ) -> None:
        self.per_minute = _env_int(PER_MINUTE_ENV, DEFAULT_PER_MINUTE) if per_minute is None else per_minute
        self.daily_cap = _env_int(DAILY_CAP_ENV, DEFAULT_DAILY_CAP) if daily_cap is None else daily_cap
        self._now = now
        self._max_keys = max_keys
        self._lock = threading.Lock()
        # key -> (minute_window, minute_count, day, day_count)
        self._state: OrderedDict[str, tuple[int, int, int, int]] = OrderedDict()

    def check(self, key: str) -> Refusal | None:
        t = self._now()
        minute, day = int(t // 60), int(t // 86400)
        with self._lock:
            self._evict_stale_locked(day)
            w, wc, d, dc = self._state.get(key, (minute, 0, day, 0))
            if w != minute:
                w, wc = minute, 0
            if d != day:
                d, dc = day, 0
            if self.daily_cap and dc >= self.daily_cap:
                return Refusal("daily", int((day + 1) * 86400 - t) + 1)
            if self.per_minute and wc >= self.per_minute:
                return Refusal("per_minute", int((minute + 1) * 60 - t) + 1)
            self._state[key] = (w, wc + 1, d, dc + 1)
            self._state.move_to_end(key)
            while len(self._state) > self._max_keys:
                self._state.popitem(last=False)  # at the bound the least recently used key makes room for the new one
            return None

    def _evict_stale_locked(self, day: int) -> None:
        while self._state:
            oldest, (_w, _wc, d, _dc) = next(iter(self._state.items()))
            if d == day:
                break
            del self._state[oldest]
