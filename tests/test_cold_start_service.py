# ruff: noqa: E501
"""#1038 PR-4: ColdStartService end to end over the shared store, with the test acting as the box-side waker and as several
containers (two services on one FakeWakeDb). No job object, no engine-run screening. CPU only; invented ids."""

from __future__ import annotations

import json
from typing import Any

import pytest

from pravrudhi.application import cold_start_service as CS
from pravrudhi.application import wake_store as WS
from pravrudhi.application.judge_wake import WakeConfig
from tests.judge_wake_fakes import box_poll_headers, box_result_request
from tests.wake_store_fakes import FakeWakeDb

SECRET = "test-secret-not-a-real-key"
CFG = WakeConfig(budget_s=300.0, poll_after_s=5, deadline_margin_s=30.0, waker_secret_env="PRAVRUDHI_WAKER_SECRET")


class Clock:
    def __init__(self) -> None:
        self.t = 1_800_000_000.0

    def __call__(self) -> float:
        return self.t


class Nonces:
    def __init__(self) -> None:
        self.i = 0

    def __call__(self) -> str:
        self.i += 1
        return f"{self.i:032x}"


class Rig:
    """Two or three containers on one database, with a settable health reading per judge."""

    def __init__(self, containers: int = 2) -> None:
        self.clock, self.db, self.readings = Clock(), FakeWakeDb(Clock()), {"first": "parked", "second": "parked"}
        self.db.clock = self.clock
        nonces = Nonces()
        self.svc = [
            CS.ColdStartService(CFG, secret=SECRET, store=WS.PgWakeStore(self.db.call, CFG, nonce=nonces),
                                health=lambda role: self.readings[role], wall=self.clock)
            for _ in range(containers)
        ]

    def box_poll(self, i: int = 1) -> list[dict[str, Any]]:
        h = box_poll_headers(SECRET, ts=int(self.clock.t))
        status, body = self.svc[i].requests(h["X-Waker-Timestamp"], h["X-Waker-Nonce"], h["X-Waker-Signature"])
        assert status == 200, body
        return [json.loads(r["body"]) for r in body["requests"]]

    def box_deliver(self, results: list[dict[str, Any]], i: int = 0) -> int:
        body, h = box_result_request(SECRET, results, ts=int(self.clock.t))
        status, out = self.svc[i].deliver(h["X-Waker-Timestamp"], h["X-Waker-Nonce"], h["X-Waker-Signature"], body)
        assert status == 200, out
        return int(out["applied"])


def test_with_every_judge_up_nothing_is_stored_and_a_full_ticket_comes_back() -> None:
    r = Rig()
    r.readings = {"first": "ready", "second": "ready"}
    outcome, body = r.svc[0].start("u-1")
    assert outcome == "ready" and body["wake"]["state"] == "ready" and body["ticket"]["mode"] == "full"
    assert r.db.calls == []  # no wake, no limiter row, no write of any kind
    assert r.svc[1].ticket_mode(body["ticket"]["value"], "u-1") == "full" and r.svc[1].ticket_mode(body["ticket"]["value"], "u-2") is None


def test_a_cold_wake_across_three_containers_ends_ready_with_a_full_ticket() -> None:
    r = Rig(3)
    outcome, body = r.svc[0].start("u-1")  # container A: the user starts it
    assert outcome == "accepted" and body["wake"]["state"] == "waking" and "ticket" not in body
    assert [m["state"] for m in body["wake"]["models"]] == ["waiting", "waiting"]
    asked = {q["label"]: q["nonce"] for q in r.box_poll(1)}  # container B: the box polls
    assert set(asked) == {"first", "second"}
    assert r.box_deliver([{"nonce": n, "state": "granted", "lease_ref": f"lease-{k}"} for k, n in asked.items()], i=2) == 2  # container C
    assert [m["state"] for m in r.svc[0].status("u-1")["wake"]["models"]] == ["starting", "starting"]
    assert r.box_poll(1) == []  # answered requests are no longer listed
    r.readings = {"first": "ready", "second": "warming"}
    assert r.svc[1].status("u-1")["wake"]["state"] == "waking"
    r.clock.t += 40
    r.readings = {"first": "ready", "second": "ready"}
    done = r.svc[2].status("u-1")  # container C reads the end
    assert done["wake"]["state"] == "ready" and done["wake"]["reason"] is None and done["ticket"]["mode"] == "full"
    assert r.svc[0].ticket_mode(done["ticket"]["value"], "u-1") == "full"
    assert r.svc[0].start("u-1")[0] == "ready"  # the judges are up: nothing more to wake


def test_the_partial_path_issues_a_partial_ticket_and_the_reason() -> None:
    r = Rig()
    r.svc[0].start("u-1")
    asked = {q["label"]: q["nonce"] for q in r.box_poll()}
    r.box_deliver([{"nonce": asked["first"], "state": "granted", "lease_ref": "l1"}, {"nonce": asked["second"], "state": "refused", "lease_ref": None}])
    r.readings["first"] = "ready"
    body = r.svc[1].status("u-1")
    assert body["wake"]["state"] == "partial" and body["wake"]["reason"] == "second_judge_unavailable"
    assert [m["state"] for m in body["wake"]["models"]] == ["ready", "failed"] and body["ticket"]["mode"] == "partial"
    assert r.svc[0].ticket_mode(body["ticket"]["value"], "u-1") == "partial"


def test_a_refused_first_judge_fails_with_no_ticket_and_the_user_cools_down() -> None:
    r = Rig()
    r.svc[0].start("u-1")
    asked = {q["label"]: q["nonce"] for q in r.box_poll()}
    r.box_deliver([{"nonce": asked["first"], "state": "refused", "lease_ref": None}, {"nonce": asked["second"], "state": "granted", "lease_ref": "l2"}])
    body = r.svc[1].status("u-1")
    assert body["wake"]["state"] == "failed" and body["wake"]["reason"] == "first_judge_unavailable" and "ticket" not in body
    assert r.svc[0].start("u-1") == ("not_allowed", CS.NOT_ALLOWED)  # the cooldown
    r.clock.t += 121
    assert r.svc[0].start("u-1")[0] == "accepted"


def test_the_budget_ends_a_wake_that_never_became_ready() -> None:
    r = Rig()
    r.svc[0].start("u-1")
    asked = {q["label"]: q["nonce"] for q in r.box_poll()}
    r.box_deliver([{"nonce": n, "state": "granted", "lease_ref": "l"} for n in asked.values()])
    r.clock.t += 299
    assert r.svc[1].status("u-1")["wake"]["state"] == "waking"
    r.clock.t += 2
    body = r.svc[1].status("u-1")
    assert body["wake"]["state"] == "timeout" and body["wake"]["reason"] == "start_timeout" and "ticket" not in body


def test_a_second_judge_that_times_out_alone_ends_partial() -> None:
    r = Rig()
    r.svc[0].start("u-1")
    asked = {q["label"]: q["nonce"] for q in r.box_poll()}
    r.box_deliver([{"nonce": n, "state": "granted", "lease_ref": "l"} for n in asked.values()])
    r.readings["first"] = "ready"
    r.clock.t += 301
    body = r.svc[0].status("u-1")
    assert body["wake"]["state"] == "partial" and body["ticket"]["mode"] == "partial"


def test_a_status_without_a_wake_in_progress_is_a_timeout_never_a_state_oracle() -> None:
    r = Rig()
    body = r.svc[0].status("u-9")  # never started, judges parked
    assert body["wake"]["state"] == "timeout" and "ticket" not in body


def test_two_users_share_one_wake_request_per_judge() -> None:
    r = Rig()
    r.svc[0].start("u-1")
    r.svc[1].start("u-2")
    assert len(r.box_poll()) == 2  # one per judge, not four


def test_the_daily_cap_counts_and_refuses_with_the_one_marker() -> None:
    r = Rig()
    for _ in range(6):
        assert r.svc[0].start("u-1")[0] == "accepted"
        r.db.limits["u-1"]["active"] = None  # the wake ended (the status read releases it)
        r.clock.t += 1
    assert r.svc[1].start("u-1") == ("not_allowed", CS.NOT_ALLOWED)


def test_a_store_that_is_down_never_grants_a_wake() -> None:
    r = Rig()
    r.db.down = True
    assert r.svc[0].start("u-1") == ("not_allowed", CS.NOT_ALLOWED)
    with pytest.raises(ConnectionError):
        r.svc[0].status("u-1")
    h = box_poll_headers(SECRET, ts=int(r.clock.t))
    assert r.svc[0].requests(h["X-Waker-Timestamp"], h["X-Waker-Nonce"], h["X-Waker-Signature"])[0] == 401  # the nonce store is down: refused


def test_a_failure_while_asking_for_the_wake_frees_the_slot_without_a_cooldown() -> None:
    r = Rig()
    real = r.db.wake_ensure

    def boom(*a: Any) -> Any:
        raise ConnectionError("x")

    r.db.wake_ensure = boom  # type: ignore[method-assign]
    assert r.svc[0].start("u-1")[0] == "not_allowed"
    r.db.wake_ensure = real  # type: ignore[method-assign]
    assert r.svc[0].start("u-1")[0] == "accepted"


def test_extend_needs_a_lease_and_is_rate_deduped() -> None:
    r = Rig()
    r.svc[0].extend()
    assert r.box_poll() == [] or all(q["kind"] != "extend" for q in r.box_poll())  # no lease granted yet: nothing to extend
    r.svc[0].start("u-1")
    asked = {q["label"]: q["nonce"] for q in r.box_poll()}
    r.box_deliver([{"nonce": n, "state": "granted", "lease_ref": f"lease-{k}"} for k, n in asked.items()])
    r.clock.t += 40
    r.svc[1].extend()
    r.svc[1].extend()  # inside 30 s on the same container: no second request
    extends = [q for q in r.box_poll(0) if q["kind"] == "extend"]
    assert sorted((q["label"], q["lease_ref"]) for q in extends) == [("first", "lease-first"), ("second", "lease-second")]
    r.svc[0].extend()  # another container: the database dedupes (30 s per judge)
    assert len([q for q in r.box_poll(1) if q["kind"] == "extend"]) == 2


def test_extend_never_raises_when_the_store_is_down() -> None:
    r = Rig()
    r.db.down = True
    r.svc[0].extend()


def test_the_waker_routes_verify_signature_window_and_replay_across_containers() -> None:
    r = Rig()
    h = box_poll_headers(SECRET, ts=int(r.clock.t), nonce="a" * 32)
    args = (h["X-Waker-Timestamp"], h["X-Waker-Nonce"], h["X-Waker-Signature"])
    assert r.svc[0].requests(*args)[0] == 200
    assert r.svc[1].requests(*args)[0] == 401  # a replay on another container
    assert r.svc[0].requests(None, None, None)[0] == 401
    old = box_poll_headers(SECRET, ts=int(r.clock.t) - 120)
    assert r.svc[0].requests(old["X-Waker-Timestamp"], old["X-Waker-Nonce"], old["X-Waker-Signature"])[0] == 401
    forged = box_poll_headers("another-secret", ts=int(r.clock.t))
    assert r.svc[0].requests(forged["X-Waker-Timestamp"], forged["X-Waker-Nonce"], forged["X-Waker-Signature"])[0] == 401
    body, hh = box_result_request(SECRET, [], ts=int(r.clock.t))
    assert r.svc[0].deliver(hh["X-Waker-Timestamp"], hh["X-Waker-Nonce"], hh["X-Waker-Signature"], body + b" ")[0] == 401  # tampered
    ok = box_result_request(SECRET, [], ts=int(r.clock.t))
    assert r.svc[0].deliver(ok[1]["X-Waker-Timestamp"], ok[1]["X-Waker-Nonce"], ok[1]["X-Waker-Signature"], ok[0])[0] == 200
    junk = box_result_request(SECRET, [], ts=int(r.clock.t))
    assert r.svc[0].deliver(junk[1]["X-Waker-Timestamp"], junk[1]["X-Waker-Nonce"], junk[1]["X-Waker-Signature"], b"{}")[0] == 401


def test_second_judge_used_reads_only_established_elements() -> None:
    assert CS.second_judge_used({"contracts": [{"elements": [{"status": "established"}]}]}) is True
    assert CS.second_judge_used({"contracts": [{"elements": [{"status": "not_established"}]}]}) is False
    assert CS.second_judge_used({}) is False


def test_a_fully_successful_wake_is_ended_so_the_next_start_is_not_refused_as_a_dead_wake() -> None:
    """Run B (22 Oct): a status poll that saw every judge ready left the user's slot active; the next start after the 450 s dead-wake
    bound was released as FAILED (not_allowed plus a 120 s cooldown). A full success must end the wake."""
    from pravrudhi.application import judge_wake as jw
    from pravrudhi.application import wake_store as ws

    cfg = jw.parse_cold_start_config({"cold_start_wake": True})
    assert cfg is not None
    t = [1000.0]

    def clock() -> float:
        return t[0]

    store = ws.MemoryWakeStore(cfg, clock=clock)
    health = {"first": "parked", "second": "parked"}
    svc = CS.ColdStartService(cfg, secret="x" * 40, store=store, health=lambda role: health[role], wall=clock)
    assert svc.start("u-1")[0] == "accepted"
    t[0] += 5
    store.deliver([{"nonce": q["nonce"], "state": "granted", "lease_ref": "L" + q["label"]} for q in store.open_requests()])
    t[0] += 40
    health.update(first="ready", second="ready")
    assert svc.status("u-1")["wake"]["state"] == "ready"
    assert store.limit_active("u-1") is None  # the wake was ended by the status poll that saw it succeed
    health.update(first="parked", second="parked")
    t[0] += 1291  # well past the 450 s dead-wake bound
    assert svc.start("u-1")[0] == "accepted"  # not not_allowed
