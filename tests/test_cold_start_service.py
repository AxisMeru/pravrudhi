"""#1038 PR-3 (service): the wake job lifecycle with a scripted box-side waker and scripted judge health. No FastAPI, no network:
the task runs inline and `sleep` is the hook that plays the waker's answers and the judges' health between passes."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import pytest

from pravrudhi.application import cold_start_service as CS
from pravrudhi.application import judge_wake as W
from pravrudhi.application.jobs import JobStore
from pravrudhi.application.wake_policy import WakeLimiter
from tests.judge_wake_fakes import box_poll_headers, box_result_request

SECRET = "test-secret-not-a-real-key"
CFG = W.parse_cold_start_config(
    {"cold_start_wake": True, "cold_start_user_wakes_per_day": 2, "cold_start_failed_cooldown_s": 120}
)
assert CFG is not None


class Env:
    def __init__(self, per_day: int = 2, cooldown: float = 120.0) -> None:
        self.mono, self.wall = 0.0, 1761000000.0
        seq = iter(f"{i:032x}" for i in range(1, 10_000))
        self.board = W.WakeBoard(CFG.request_ttl_s, clock=lambda: self.wall, nonce=lambda: next(seq))
        self.limiter = WakeLimiter(per_day, cooldown, clock=lambda: self.mono)
        self.jobs = JobStore(retention_s=3600, max_unfinished_per_key=8)
        self.health_now = {"first": "parked", "second": "parked"}
        self.script: list[Callable[[Env], None]] = []  # one step per sleep
        self.runs: list[str] = []
        self.run_result: tuple[Any, ...] = ("ok", {"run_id": "r1", "contracts": []})
        self.svc = CS.ColdStartService(
            CFG, secret=SECRET, board=self.board, limiter=self.limiter, jobs=self.jobs, health=lambda role: self.health_now[role],
            submit=lambda f: f(), clock=lambda: self.mono, wall=lambda: self.wall, sleep=self._sleep,
        )

    def _sleep(self, s: float) -> None:
        self.mono += s
        self.wall += s
        if not self.script:
            self.mono += 1000  # nothing more is going to happen: let the budget run out
            self.wall += 1000
            return
        self.script.pop(0)(self)

    def run_job(self, kind: str) -> tuple[Any, ...]:
        self.runs.append(kind)
        return self.run_result

    def answer(self, label: str, state: str, lease: str | None = None) -> Callable[[Env], None]:
        def step(env: Env) -> None:
            req = [r for r in json.loads(json.dumps(env.board.signed(SECRET))) if json.loads(r["body"])["label"] == label]
            if req:
                env.board.deliver([{"nonce": json.loads(req[0]["body"])["nonce"], "state": state, "lease_ref": lease}])
        return step


def ready(*roles: str) -> Callable[[Env], None]:
    def step(env: Env) -> None:
        for r in roles:
            env.health_now[r] = "ready"
    return step


def both(*steps: Callable[[Env], None]) -> Callable[[Env], None]:
    def step(env: Env) -> None:
        for s in steps:
            s(env)
    return step


def job_of(env: Env, user: str, job_id: str) -> dict[str, Any]:
    out = env.svc.poll(user, job_id)
    assert out is not None
    return out


# ---- start ----------------------------------------------------------------------------------------------------------------


def test_everything_already_up_runs_now_and_touches_nothing() -> None:
    env = Env()
    env.health_now = {"first": "ready", "second": "ready"}
    assert env.svc.start("u", env.run_job) == ("run_now", None, {})
    assert env.board.pending() == 0 and env.runs == []
    assert env.limiter.begin("u") == "new"  # the limiter was not used


def test_a_full_wake_runs_the_screening_and_extends_at_start_and_end() -> None:
    env = Env()
    env.script = [
        both(env.answer("first", "granted", "lease-first"), env.answer("second", "granted", "lease-second")),
        ready("first", "second"),
    ]
    outcome, job_id, block = env.svc.start("u", env.run_job)
    assert outcome == "accepted" and job_id and block["state"] in ("waking", "ready")  # the task ran inline in this test
    assert env.runs == ["full"]
    out = job_of(env, "u", job_id)
    assert out["status"] == "done" and out["wake"]["state"] == "ready" and out["wake"]["reason"] is None
    assert out["result"]["second_judge_ran"] is True
    assert out["result"]["judges"] == {"first": {"ran": True}, "second": {"ran": True, "used": False}}
    extends = [json.loads(r["body"]) for r in env.board.signed(SECRET)]
    got = sorted((e["label"], e["kind"], e["lease_ref"]) for e in extends)
    assert got == [("first", "extend", "lease-first"), ("second", "extend", "lease-second")]
    assert env.limiter.begin("u") == "new"  # the wake ended well: a later wake is allowed (day cap 2, one used)


def test_the_second_judge_is_marked_used_only_when_an_element_is_established() -> None:
    env = Env()
    env.run_result = ("ok", {"contracts": [{"elements": [{"status": "established"}, {"status": "not_established"}]}]})
    env.script = [both(env.answer("first", "granted"), env.answer("second", "granted")), ready("first", "second")]
    _, job_id, _ = env.svc.start("u", env.run_job)
    assert job_of(env, "u", str(job_id))["result"]["judges"]["second"] == {"ran": True, "used": True}


def test_a_partial_load_runs_with_the_partial_kind_and_says_the_second_did_not_run() -> None:
    env = Env()
    env.script = [both(env.answer("first", "granted"), env.answer("second", "refused")), ready("first")]
    _, job_id, _ = env.svc.start("u", env.run_job)
    assert env.runs == ["partial"]  # the caller scopes a pre-tripped breaker to THIS run only
    out = job_of(env, "u", str(job_id))
    assert out["status"] == "done" and out["wake"]["state"] == "partial" and out["wake"]["reason"] == "second_judge_unavailable"
    assert out["result"]["second_judge_ran"] is False
    assert out["result"]["judges"]["second"] == {"ran": False, "used": False}


def test_a_refused_first_judge_fails_the_job_and_nothing_is_scored() -> None:
    env = Env()
    env.script = [both(env.answer("first", "refused"), env.answer("second", "granted"))]
    _, job_id, _ = env.svc.start("u", env.run_job)
    assert env.runs == []
    out = job_of(env, "u", str(job_id))
    assert out["status"] == "failed" and out["wake"]["state"] == "failed" and out["wake"]["reason"] == "first_judge_unavailable"
    assert out["error"]["status_code"] == 503 and "result" not in out


def test_the_budget_ends_a_wake_that_never_becomes_ready_and_unknown_never_counts() -> None:
    env = Env()
    env.health_now = {"first": "parked", "second": "parked"}
    env.script = [
        both(env.answer("first", "granted"), env.answer("second", "granted")),
        lambda e: e.health_now.update(first="unknown", second="unknown"),
    ]
    _, job_id, _ = env.svc.start("u", env.run_job)
    out = job_of(env, "u", str(job_id))
    assert out["status"] == "failed" and out["wake"]["state"] == "timeout" and out["wake"]["reason"] == "start_timeout"
    assert env.runs == []


def test_no_answer_from_the_waker_expires_as_failed() -> None:
    env = Env()
    env.script = [lambda e: None] * 3  # the waker never answers: the request expires (ttl 120 s) inside the budget loop
    _, job_id, _ = env.svc.start("u", env.run_job)
    out = job_of(env, "u", str(job_id))
    assert out["status"] == "failed" and out["wake"]["state"] in ("failed", "timeout")
    assert env.runs == []


def test_a_failed_run_after_a_good_wake_is_a_failed_job_but_not_a_failed_wake() -> None:
    env = Env()
    env.run_result = ("err", 503, {"error": "judge_unavailable"})
    env.script = [both(env.answer("first", "granted"), env.answer("second", "granted")), ready("first", "second")]
    _, job_id, _ = env.svc.start("u", env.run_job)
    out = job_of(env, "u", str(job_id))
    assert out["status"] == "failed" and out["error"]["status_code"] == 503 and out["wake"]["state"] == "ready"
    assert env.limiter.begin("u") == "new"  # no cooldown: the wake itself worked


def test_a_crashing_run_is_a_failed_job() -> None:
    env = Env()

    def boom(kind: str) -> tuple[Any, ...]:
        raise RuntimeError("secret detail")

    env.script = [both(env.answer("first", "granted"), env.answer("second", "granted")), ready("first", "second")]
    _, job_id, _ = env.svc.start("u", boom)
    out = job_of(env, "u", str(job_id))
    assert out["status"] == "failed" and out["error"]["body"] == {"error": "internal_error"} and "secret" not in json.dumps(out)


# ---- limits and the one marker --------------------------------------------------------------------------------------------


def test_a_second_request_for_the_same_user_is_the_existing_job() -> None:
    env = Env()
    submitted: list[Callable[[], None]] = []
    env.svc._submit = submitted.append  # hold the task: the wake stays in progress
    a = env.svc.start("u", env.run_job)
    b = env.svc.start("U", env.run_job)
    assert a[0] == b[0] == "accepted" and a[1] == b[1] and len(submitted) == 1
    assert env.board.pending() == 2  # one wake request per judge, not four


def test_the_cap_the_cooldown_and_too_many_jobs_all_answer_with_the_identical_marker() -> None:
    env = Env(per_day=1, cooldown=120.0)
    env.script = [both(env.answer("first", "granted"), env.answer("second", "granted")), ready("first", "second")]
    env.svc.start("u", env.run_job)
    env.health_now = {"first": "parked", "second": "parked"}
    capped = env.svc.start("u", env.run_job)  # the day cap (1) is spent
    env.script = [env.answer("first", "refused")]
    env2 = Env(per_day=5, cooldown=120.0)
    env2.script = [both(env2.answer("first", "refused"))]
    env2.svc.start("v", env2.run_job)
    cooling = env2.svc.start("v", env2.run_job)  # a failed wake: cooldown
    assert capped == ("not_allowed", None, CS.NOT_ALLOWED) and cooling == ("not_allowed", None, CS.NOT_ALLOWED)
    assert CS.NOT_ALLOWED == {"error": "judges_offline", "wake": "not_allowed"}


# ---- poll ownership and the coarse block ------------------------------------------------------------------------------------


def test_only_the_owner_can_poll_a_wake_job() -> None:
    env = Env()
    env.script = [both(env.answer("first", "granted"), env.answer("second", "granted")), ready("first", "second")]
    _, job_id, _ = env.svc.start("owner", env.run_job)
    assert env.svc.poll("owner", str(job_id)) is not None
    assert env.svc.poll("someone-else", str(job_id)) is None
    assert env.svc.poll("owner", "no-such-job") is None
    assert env.jobs.get("key-1", str(job_id)) is None  # a key-owned lookup never sees a user job


def test_the_wake_block_never_carries_ids_leases_counts_or_costs() -> None:
    env = Env()
    env.script = [
        both(env.answer("first", "granted", "lease-secret-1"), env.answer("second", "granted", "lease-secret-2")),
        ready("first", "second"),
    ]
    _, job_id, _ = env.svc.start("owner", env.run_job)
    wake = job_of(env, "owner", str(job_id))["wake"]
    assert set(wake) == {"state", "models", "reason", "deadline_s"}
    assert "lease-secret" not in json.dumps(wake) and "owner" not in json.dumps(wake)


# ---- the waker-facing side ------------------------------------------------------------------------------------------------


def test_the_signed_poll_lists_the_requests_and_a_bad_poll_is_401() -> None:
    env = Env()
    submitted: list[Callable[[], None]] = []
    env.svc._submit = submitted.append
    env.svc.start("u", env.run_job)
    h = box_poll_headers(SECRET, ts=int(env.wall), nonce="a" * 32)
    status, body = env.svc.requests(h["X-Waker-Timestamp"], h["X-Waker-Nonce"], h["X-Waker-Signature"])
    labels = sorted(json.loads(r["body"])["label"] for r in body["requests"])
    assert status == 200 and body["v"] == 1 and labels == ["first", "second"]
    assert env.svc.requests(h["X-Waker-Timestamp"], h["X-Waker-Nonce"], h["X-Waker-Signature"]) == (401, {})  # replay
    assert env.svc.requests(None, None, None) == (401, {})
    bad = box_poll_headers("another-secret", ts=int(env.wall), nonce="b" * 32)
    assert env.svc.requests(bad["X-Waker-Timestamp"], bad["X-Waker-Nonce"], bad["X-Waker-Signature"]) == (401, {})


def test_signed_deliveries_apply_independently_and_a_bad_body_is_refused() -> None:
    env = Env()
    submitted: list[Callable[[], None]] = []
    env.svc._submit = submitted.append
    env.svc.start("u", env.run_job)
    nonces = {json.loads(r["body"])["label"]: json.loads(r["body"])["nonce"] for r in env.board.signed(SECRET)}
    for i, label in enumerate(("first", "second")):  # two deliveries in one cycle, each on its own nonce
        item = {"nonce": nonces[label], "state": "granted", "lease_ref": f"l{i}"}
        body, h = box_result_request(SECRET, [item], ts=int(env.wall), nonce=f"{i:032x}")
        got = env.svc.deliver(h["X-Waker-Timestamp"], h["X-Waker-Nonce"], h["X-Waker-Signature"], body)
        assert got == (200, {"v": 1, "applied": 1})
    assert env.board.lease_for("first") == "l0" and env.board.lease_for("second") == "l1"
    body, h = box_result_request(SECRET, [], ts=int(env.wall), nonce="c" * 32)
    assert env.svc.deliver(h["X-Waker-Timestamp"], h["X-Waker-Nonce"], h["X-Waker-Signature"], body + b"x")[0] == 401  # tampered
    junk = b"not json"
    sig = W.sign_http(SECRET, "POST", CS.RESULT_PATH, int(env.wall), "d" * 32, junk)
    assert env.svc.deliver(str(int(env.wall)), "d" * 32, "v1=" + sig, junk) == (400, {"error": "bad_request"})
    wrong = json.dumps({"v": 2, "results": []}).encode()
    sig2 = W.sign_http(SECRET, "POST", CS.RESULT_PATH, int(env.wall), "e" * 32, wrong)
    assert env.svc.deliver(str(int(env.wall)), "e" * 32, "v1=" + sig2, wrong) == (400, {"error": "bad_request"})


@pytest.mark.parametrize("result", [{"contracts": []}, {"contracts": [{"elements": []}]}, {}, {"contracts": [None, "x"]}])
def test_second_judge_used_is_false_without_an_established_element(result: dict[str, Any]) -> None:
    assert CS.second_judge_used(result) is False
