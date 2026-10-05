"""#146: the in-memory job store behind POST /api/v1/analyse-facts/jobs. Constructed inputs only."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from pravrudhi.application.jobs import JobStore


class Clock:
    def __init__(self) -> None:
        self.t = datetime(2026, 1, 1, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.t


def test_lifecycle_pending_running_done() -> None:
    s = JobStore(retention_s=60, clock=Clock())
    j = s.create("k1")
    assert s.get("k1", j).status == "pending"
    s.start(j)
    assert s.get("k1", j).status == "running"
    s.finish(j, result={"a": 1})
    got = s.get("k1", j)
    assert (got.status, got.result, got.error) == ("done", {"a": 1}, None)


def test_failure_records_status_and_body() -> None:
    s = JobStore(retention_s=60, clock=Clock())
    j = s.create("k1")
    s.fail(j, status_code=503, body={"error": "judge_unavailable"})
    got = s.get("k1", j)
    assert (got.status, got.result, got.error) == ("failed", None, {"status_code": 503, "body": {"error": "judge_unavailable"}})


def test_another_key_cannot_see_the_job() -> None:
    s = JobStore(retention_s=60, clock=Clock())
    j = s.create("k1")
    assert s.get("k2", j) is None
    assert s.get("k1", "nope") is None


def test_finished_job_is_deleted_after_retention_unfinished_is_kept() -> None:
    clock = Clock()
    s = JobStore(retention_s=60, clock=clock)
    done, running = s.create("k1"), s.create("k1")
    s.start(running)
    s.finish(done, result={})
    clock.t += timedelta(seconds=59)
    assert s.get("k1", done) is not None
    clock.t += timedelta(seconds=2)
    assert s.get("k1", done) is None
    assert s.get("k1", running) is not None
    assert s.count() == 1


def test_retention_counts_from_completion_not_creation() -> None:
    clock = Clock()
    s = JobStore(retention_s=60, clock=clock)
    j = s.create("k1")
    clock.t += timedelta(seconds=500)
    s.finish(j, result={})
    clock.t += timedelta(seconds=59)
    assert s.get("k1", j) is not None


def test_per_key_unfinished_cap() -> None:
    s = JobStore(retention_s=60, clock=Clock(), max_unfinished_per_key=2)
    a, b = s.create("k1"), s.create("k1")
    assert s.create("k1") is None
    assert s.create("k2") is not None
    s.finish(a, result={})
    assert s.create("k1") is not None
    assert b
