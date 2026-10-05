"""In-memory job store for the partner API's async analyse-facts mode (#146).

Jobs are scoped to the API key that created them. A finished job is deleted `retention_s` after it finished
and is simply absent afterwards; an unfinished job is never purged. State lives in this process only: a
restart loses every job, which a client sees as a 404 on poll and answers by resubmitting.
"""

from __future__ import annotations

import secrets
import threading
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

JobStatus = Literal["pending", "running", "done", "failed"]


@dataclass
class Job:
    job_id: str
    key_id: str
    created_at: datetime
    status: JobStatus = "pending"
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    finished_at: datetime | None = None


class JobStore:
    def __init__(
        self,
        *,
        retention_s: float,
        clock: Callable[[], datetime] | None = None,
        max_unfinished_per_key: int = 8,
    ) -> None:
        self._retention = timedelta(seconds=retention_s)
        self._clock = clock or (lambda: datetime.now(UTC))
        self._max_unfinished = max_unfinished_per_key
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def _purge(self) -> None:
        now = self._clock()
        for jid in [j.job_id for j in self._jobs.values() if j.finished_at and now - j.finished_at > self._retention]:
            del self._jobs[jid]

    def create(self, key_id: str) -> str | None:
        """A new pending job id, or `None` when this key already has its maximum number of unfinished jobs."""
        with self._lock:
            self._purge()
            if sum(1 for j in self._jobs.values() if j.key_id == key_id and j.finished_at is None) >= self._max_unfinished:
                return None
            jid = secrets.token_urlsafe(16)
            self._jobs[jid] = Job(jid, key_id, self._clock())
            return jid

    def start(self, job_id: str) -> None:
        with self._lock:
            self._jobs[job_id].status = "running"

    def finish(self, job_id: str, *, result: dict[str, Any]) -> None:
        with self._lock:
            j = self._jobs[job_id]
            j.status, j.result, j.finished_at = "done", result, self._clock()

    def fail(self, job_id: str, *, status_code: int, body: Any) -> None:
        with self._lock:
            j = self._jobs[job_id]
            j.status, j.error, j.finished_at = "failed", {"status_code": status_code, "body": body}, self._clock()

    def discard(self, job_id: str) -> None:
        with self._lock:
            self._jobs.pop(job_id, None)

    def get(self, key_id: str, job_id: str) -> Job | None:
        with self._lock:
            self._purge()
            j = self._jobs.get(job_id)
            return replace(j) if j is not None and j.key_id == key_id else None

    def count(self) -> int:
        with self._lock:
            self._purge()
            return len(self._jobs)
