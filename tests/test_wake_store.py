# ruff: noqa: E501
"""#1038 PR-4: the WakeStore contract, run against BOTH implementations (in-process and the database one over the SQL port in
tests/wake_store_fakes.py), plus the two-container cases that only the shared store can pass. CPU only; invented ids."""

from __future__ import annotations

import json
import threading
from typing import Any

import pytest

from pravrudhi.application import wake_store as WS
from pravrudhi.application.judge_wake import WakeConfig
from tests.wake_store_fakes import FakeWakeDb

CFG = WakeConfig(budget_s=300.0, poll_after_s=5, deadline_margin_s=30.0, waker_secret_env="PRAVRUDHI_WAKER_SECRET")


class Clock:
    def __init__(self) -> None:
        self.t = 1_000_000.0

    def __call__(self) -> float:
        return self.t


class Nonces:
    def __init__(self) -> None:
        self.i = 0

    def __call__(self) -> str:
        self.i += 1
        return f"{self.i:032x}"


def make(kind: str, clock: Clock, db: FakeWakeDb | None = None, nonces: Nonces | None = None) -> WS.WakeStore:
    n = nonces or Nonces()
    if kind == "memory":
        return WS.MemoryWakeStore(CFG, clock=clock, nonce=n)
    return WS.PgWakeStore((db or FakeWakeDb(clock)).call, CFG, nonce=n)


@pytest.fixture(params=["memory", "pg"])
def kind(request: pytest.FixtureRequest) -> str:
    return str(request.param)


def test_a_judge_has_one_live_wake_request_shared_by_everyone(kind: str) -> None:
    c = Clock()
    s = make(kind, c)
    s.ensure_wake("first", 300.0)
    s.ensure_wake("first", 300.0)
    s.ensure_wake("second", 300.0)
    rows = s.open_requests()
    assert sorted(r["label"] for r in rows) == ["first", "second"] and all(r["kind"] == "wake" for r in rows)


def test_the_waker_answers_once_and_the_judge_reads_as_granted(kind: str) -> None:
    c = Clock()
    s = make(kind, c)
    s.ensure_wake("first", 300.0)
    nonce = s.open_requests()[0]["nonce"]
    assert s.wake_state("first") == (None, 0.0)
    assert s.deliver([{"nonce": nonce, "state": "granted", "lease_ref": "lease-1"}]) == 1
    assert s.deliver([{"nonce": nonce, "state": "refused", "lease_ref": None}]) == 0  # an answer is final
    c.t += 7
    assert s.wake_state("first") == ("granted", 7.0) and s.open_requests() == [] and s.lease_for("first") == "lease-1"
    s.ensure_wake("first", 300.0)  # a granted wake inside the budget is reused, not repeated
    assert s.open_requests() == []


def test_bad_answers_are_ignored(kind: str) -> None:
    c = Clock()
    s = make(kind, c)
    s.ensure_wake("first", 300.0)
    nonce = s.open_requests()[0]["nonce"]
    bad: list[Any] = [None, "x", {"nonce": "z" * 32, "state": "granted"}, {"nonce": nonce, "state": "maybe"}, {"nonce": nonce, "state": 1}]
    assert s.deliver(bad) == 0 and s.deliver("not a list") == 0
    assert s.deliver([{"nonce": nonce, "state": "granted", "lease_ref": "a|b"}]) == 1
    assert s.lease_for("first") is None  # a lease reference containing the separator is dropped, the grant stands


def test_an_unanswered_request_expires_into_failed_and_is_no_longer_listed(kind: str) -> None:
    c = Clock()
    s = make(kind, c)
    s.ensure_wake("second", 300.0)
    c.t += 121
    assert s.open_requests() == [] and s.wake_state("second") == ("failed", 121.0)
    assert s.deliver([{"nonce": f"{1:032x}", "state": "granted", "lease_ref": "x"}]) == 0  # too late
    s.ensure_wake("second", 300.0)  # a fresh request is made (the old one failed)
    assert len(s.open_requests()) == 1


def test_an_extend_request_is_listed_with_its_lease_and_deduped(kind: str) -> None:
    c = Clock()
    s = make(kind, c)
    s.request_extend("first", "lease-1")
    rows = s.open_requests()
    assert rows == [{"nonce": rows[0]["nonce"], "label": "first", "kind": "extend", "lease_ref": "lease-1"}]


def test_a_nonce_is_first_used_once_and_forgotten_after_its_ttl(kind: str) -> None:
    c = Clock()
    s = make(kind, c)
    n = "ab" * 16
    assert s.nonce_first_use(n) is True and s.nonce_first_use(n) is False
    c.t += 181
    assert s.nonce_first_use(n) is True


def test_one_wake_at_a_time_a_daily_cap_and_a_cooldown_after_a_failure(kind: str) -> None:
    c = Clock()
    s = make(kind, c)
    r, stamp = s.limit_begin("U-1")
    assert r == "new" and stamp is not None
    assert s.limit_begin("u-1")[0] == "existing"  # the same user, any spelling: one wake
    assert s.limit_active("u-1") == (stamp, 0.0)
    s.limit_end("u-1", ok=False, stamp=stamp)
    assert s.limit_begin("u-1")[0] == "cooldown" and s.limit_active("u-1") is None
    c.t += 121
    for _ in range(5):
        r, stamp = s.limit_begin("u-1")
        assert r == "new"
        s.limit_end("u-1", ok=True, stamp=stamp)
        c.t += 1
    assert s.limit_begin("u-1")[0] == "limit"  # 6 starts in 24 h, the failed one counted
    c.t += 86400
    assert s.limit_begin("u-1")[0] == "new"


def test_a_late_end_from_an_expired_wake_does_not_pop_the_newer_wake(kind: str) -> None:
    c = Clock()
    s = make(kind, c)
    _, old = s.limit_begin("u")
    c.t += 451
    assert s.limit_active("u") is None and s.limit_begin("u")[0] == "cooldown"  # the dead wake was released as failed
    c.t += 121
    _, new = s.limit_begin("u")
    assert new != old
    s.limit_end("u", ok=False, stamp=old)
    assert s.limit_begin("u")[0] == "existing"
    s.limit_end("u", ok=True, stamp=new)
    assert s.limit_begin("u")[0] == "new"


def test_two_containers_share_every_row() -> None:
    c, n = Clock(), Nonces()
    db = FakeWakeDb(c)
    a, b = make("pg", c, db, n), make("pg", c, db, n)
    a.ensure_wake("first", 300.0)
    b.ensure_wake("first", 300.0)  # the same judge: still one request
    assert len(b.open_requests()) == 1
    assert b.deliver([{"nonce": a.open_requests()[0]["nonce"], "state": "granted", "lease_ref": "L"}]) == 1
    assert a.wake_state("first") == ("granted", 0.0) and a.lease_for("first") == "L"
    assert a.nonce_first_use("cd" * 16) is True and b.nonce_first_use("cd" * 16) is False  # a replay on another container
    assert a.limit_begin("u")[0] == "new" and b.limit_begin("u")[0] == "existing"  # one wake per user across containers


def test_concurrent_begins_on_two_containers_start_exactly_one_wake() -> None:
    c = Clock()
    db = FakeWakeDb(c)
    stores = [make("pg", c, db), make("pg", c, db)]
    out: list[str] = []
    ts = [threading.Thread(target=lambda s=s: out.append(s.limit_begin("u")[0])) for s in stores * 8]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert out.count("new") == 1 and out.count("existing") == 15


def test_only_coordination_rows_are_ever_written() -> None:
    c = Clock()
    db = FakeWakeDb(c)
    s = make("pg", c, db)
    s.ensure_wake("first", 300.0)
    s.limit_begin("u")
    s.nonce_first_use("ef" * 16)
    stored = json.dumps([db.requests, db.nonces, db.limits])
    assert set(db.requests[next(iter(db.requests))]) == {
        "nonce", "label", "kind", "lease_ref", "created_at", "expires_at", "answer", "answer_lease", "answered_at"}
    assert set(db.limits["u"]) == {"starts", "active", "cool", "expires_at"} and "facts" not in stored


def test_a_store_that_cannot_answer_raises_and_never_grants() -> None:
    c = Clock()
    db = FakeWakeDb(c)
    s = make("pg", c, db)
    db.down = True
    for f in (lambda: s.limit_begin("u"), lambda: s.ensure_wake("first", 300.0), lambda: s.open_requests(), lambda: s.nonce_first_use("ab" * 16)):
        with pytest.raises(ConnectionError):
            f()


def test_a_malformed_database_answer_is_a_refusal_not_a_grant() -> None:
    s = WS.PgWakeStore(lambda fn, *a: {"result": "granted-for-free", "stamp": 1.0} if fn == "wake_begin" else None, CFG)
    assert s.limit_begin("u") == ("cooldown", None)
    assert s.open_requests() == [] and s.wake_state("first") is None and s.lease_for("first") is None
    assert s.nonce_first_use("ab" * 16) is False and s.limit_active("u") is None and s.deliver([{"nonce": "x"}]) == 0
