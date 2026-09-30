"""The hours the hosted engine is open, read from config so partner hours can change without a code edit."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class ServiceWindow:
    tz: str
    open: time
    close: time

    def __post_init__(self) -> None:
        ZoneInfo(self.tz)
        if self.open == self.close:
            raise ValueError("service window open and close must differ")

    @classmethod
    def from_config(cls, body: dict[str, Any]) -> ServiceWindow:
        return cls(
            tz=str(body["timezone"]), open=time.fromisoformat(str(body["open"])), close=time.fromisoformat(str(body["close"]))
        )

    def _is_open_local(self, t: time) -> bool:
        if self.open < self.close:
            return self.open <= t < self.close
        return t >= self.open or t < self.close

    def is_open(self, now: datetime) -> bool:
        return self._is_open_local(now.astimezone(ZoneInfo(self.tz)).time())

    def next_open(self, now: datetime) -> datetime:
        """The next instant (UTC) the window opens; `now` itself when already open. Walks local calendar
        days so a DST change moves the instant with the wall clock, not by a fixed 24 h."""
        zone = ZoneInfo(self.tz)
        local = now.astimezone(zone)
        if self._is_open_local(local.time()):
            return now.astimezone(UTC)
        for days in range(0, 3):
            candidate = datetime.combine(local.date() + timedelta(days=days), self.open, tzinfo=zone)
            if candidate > local:
                return candidate.astimezone(UTC)
        raise AssertionError("unreachable: an open time occurs within two local days")

    def retry_after_seconds(self, now: datetime) -> int:
        return max(1, int((self.next_open(now) - now.astimezone(UTC)).total_seconds()))
