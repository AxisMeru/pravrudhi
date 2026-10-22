"""#1038 cold start, PR-3 (service): the state behind the wake routes. Plain Python, no FastAPI: `partner.py` wires it to routes
only when `cold_start_wake` is on and the HMAC secret is set. It owns

* the start decision (`start`): everything already up -> run now; a parked judge and a verified waker user within their limits ->
  a WAKE JOB owned by `user:<id>` (the same `JobStore` class as the key-owned jobs, a separate instance, so a key can never read
  a user's job nor the reverse); anything else -> the ONE identical not-allowed answer (no cap oracle);
* the wake task (`_task`): ask the box-side waker through the `WakeBoard`, watch each judge's own /health, apply the budget with
  `tick()` on EVERY pass, then run the screening (both judges ready: normally; first ready only: with a request-scoped pre-tripped
  breaker so every established element REFERs, never a proof) and send `extend` at the start and at the end of a team screening;
* the poll (`poll`): the job plus the coarse `wake` block, only for the job's owner;
* the waker-facing side (`requests`, `deliver`): the signed poll and the signed result delivery (contract v2).

Fail closed everywhere: a wake that fails or times out ends the job `failed`; nothing is ever scored without the first judge, and
nothing is ever ESTABLISHED, PROOF or DENIAL unless both judges read it."""

from __future__ import annotations

import contextlib
import json
import threading
import time
from collections.abc import Callable, Mapping
from typing import Any, Literal

from pravrudhi.application.jobs import JobStore
from pravrudhi.application.judge_wake import ROLES, JudgeWake, NonceStore, WakeBoard, WakeConfig, verify_http
from pravrudhi.application.wake_policy import WakeLimiter

NOT_ALLOWED: dict[str, str] = {"error": "judges_offline", "wake": "not_allowed"}  # the ONE marker, whatever the cause
POLL_PATH = "/internal/wake-requests"
RESULT_PATH = "/internal/wake-results"
CHECK_EVERY_S = 2.0
ACTIVE_TTL_S = 450.0  # a wake never lasts longer than the hard budget (420 s) plus a margin: an older entry is dead

#: What `run_job` returns: ("ok", result dict) or ("err", http status, body dict). `kind` is "full" or "partial".
RunJob = Callable[[str], tuple[Any, ...]]


def _uid(user_id: str) -> str:
    """One spelling of a user id for the owner, the active-wake map and the limiter (ids are compared case-insensitively)."""
    return user_id.strip().lower()


def second_judge_used(result: Mapping[str, Any]) -> bool:
    """The second judge read an element iff some element is ESTABLISHED (an element is established only when BOTH judges
    established it; an element the first judge rejected never reaches the second)."""
    for c in result.get("contracts") or []:
        for e in (c.get("elements") if isinstance(c, Mapping) else None) or []:
            if isinstance(e, Mapping) and e.get("status") == "established":
                return True
    return False


def second_judge_ran(result: Mapping[str, Any], kind: str) -> bool:
    """The second judge read the case iff the run was a full one AND no contract was referred because it was unavailable
    (a failed second judge, or an open shared breaker, still ends a "full" run in REFER `second_judge_unavailable`)."""
    if kind != "full":
        return False
    contracts = result.get("contracts") or []
    return not any(isinstance(c, Mapping) and c.get("reason") == "second_judge_unavailable" for c in contracts)


def decorate_result(result: dict[str, Any], kind: str) -> dict[str, Any]:
    ran = second_judge_ran(result, kind)
    result["second_judge_ran"] = ran
    result["judges"] = {"first": {"ran": True}, "second": {"ran": ran, "used": ran and second_judge_used(result)}}
    return result


class ColdStartService:
    def __init__(
        self,
        cfg: WakeConfig,
        *,
        secret: str,
        board: WakeBoard,
        limiter: WakeLimiter,
        jobs: JobStore,
        health: Callable[[str], str],
        submit: Callable[[Callable[[], None]], Any],
        clock: Callable[[], float] = time.monotonic,
        wall: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._cfg, self._secret, self._board, self._limiter, self._jobs = cfg, secret, board, limiter, jobs
        self._health, self._submit, self._clock, self._wall, self._sleep = health, submit, clock, wall, sleep
        self._nonces = NonceStore(clock=wall)
        self._wakes: dict[str, JudgeWake] = {}
        self._stamps: dict[str, float | None] = {}  # job id -> the limiter's stamp of its wake (for a stamped, idempotent end)
        self._active: dict[str, tuple[str, float]] = {}  # user -> (job id of the wake in progress, when it began)
        self._lock = threading.Lock()

    # ---- the user side -------------------------------------------------------------------------------------------------

    def start(
        self, user_id: str, run_job: RunJob
    ) -> tuple[Literal["run_now", "accepted", "not_allowed"], str | None, dict[str, Any]]:
        """Decide a team user's screening. Returns (outcome, job id, wake block): `run_now` = every judge is already up (the
        caller runs it like the normal route); `accepted` = a wake job exists (new or the user's existing one);
        `not_allowed` = the one identical marker (limit, cooldown, too many unfinished jobs: all the same answer)."""
        user_id = _uid(user_id)
        readings = {role: self._health(role) for role in ROLES}
        if all(r == "ready" for r in readings.values()):
            return "run_now", None, {}
        owner = f"user:{user_id}"
        with self._lock:
            self._forget_finished_locked()
            entry = self._active.get(user_id)
            if entry is not None and self._clock() - entry[1] > ACTIVE_TTL_S:
                del self._active[user_id]  # a task that never finished (a crash, a swap): the user is not pinned to a dead wake
                entry = None
            existing = entry[0] if entry is not None else None
            if existing is not None and existing in self._wakes and self._jobs.get(owner, existing) is not None:
                return "accepted", existing, self._wakes[existing].block()
            begun = self._limiter.begin(user_id)
            if begun == "existing":  # an unfinished wake whose job record is gone: treat as refused, never start a second
                return "not_allowed", None, dict(NOT_ALLOWED)
            if begun != "new":
                return "not_allowed", None, dict(NOT_ALLOWED)
            job_id = self._jobs.create(owner)
            if job_id is None:
                self._limiter.end(user_id, ok=True)  # not the user's failure and not a wake: no cooldown, the slot is freed
                return "not_allowed", None, dict(NOT_ALLOWED)
            wake = JudgeWake(self._cfg, clock=self._clock)
            to_wake = wake.start(readings)
            self._wakes[job_id] = wake
            self._stamps[job_id] = self._limiter.stamp(user_id)
            self._active[user_id] = (job_id, self._clock())
            nonces = {role: self._board.request(role, "wake") for role in to_wake}
        self._submit(lambda: self._task(user_id, job_id, wake, nonces, run_job))
        return "accepted", job_id, wake.block()

    def poll(self, user_id: str, job_id: str) -> dict[str, Any] | None:
        """The job record and the coarse wake block for the job's OWNER only; None (a 404) for anyone else."""
        job = self._jobs.get(f"user:{_uid(user_id)}", job_id)
        if job is None:
            return None
        with self._lock:
            wake = self._wakes.get(job_id)
            block = wake.block() if wake is not None else None
        out: dict[str, Any] = {"job_id": job.job_id, "status": job.status}
        if job.result is not None:
            out["result"] = job.result
        if job.error is not None:
            out["error"] = job.error
        if block is not None:
            out["wake"] = block
        return out

    # ---- the wake task -------------------------------------------------------------------------------------------------

    def _task(self, user_id: str, job_id: str, wake: JudgeWake, nonces: dict[str, str], run_job: RunJob) -> None:
        ok = False
        try:
            self._jobs.start(job_id)
            pending = dict(nonces)
            while True:
                with self._lock:
                    for role in list(pending):
                        answer = self._board.answer(pending[role])
                        if answer is not None:
                            wake.waker_answered(role, answer[0])
                            del pending[role]
                    starting = [m["role"] for m in wake.block()["models"] if m["state"] == "starting"]
                readings = {role: self._health(role) for role in starting}  # network reads, outside the lock
                with self._lock:
                    for role, reading in readings.items():
                        wake.health(role, reading)
                    wake.tick()  # on EVERY pass (R2 advise 3)
                    state = wake.state()
                if state != "waking":
                    break
                self._sleep(CHECK_EVERY_S)
            if state in ("failed", "timeout"):
                self._jobs.fail(job_id, status_code=503, body={"error": "judge_unavailable", "reason": wake.reason()})
                return
            ok = True
            kind = "full" if state == "ready" else "partial"
            self._extend()  # at the START of the team screening
            res = run_job(kind)
            if res[0] == "ok":
                result = decorate_result(dict(res[1]), kind)
                self._jobs.finish(job_id, result=result)
                self._extend()  # at the END of a completed team screening
            else:
                self._jobs.fail(job_id, status_code=int(res[1]), body=res[2])
        except Exception:  # noqa: BLE001 - a crash is a failed job, never a hung one
            self._jobs.fail(job_id, status_code=500, body={"error": "internal_error"})
        finally:
            with self._lock:
                stamp = self._stamps.pop(job_id, None)
            self._limiter.end(user_id, ok=ok, stamp=stamp)
            with self._lock:
                if (self._active.get(user_id) or ('', 0.0))[0] == job_id:
                    del self._active[user_id]

    def _extend(self) -> None:
        for role in ROLES:
            lease = self._board.lease_for(role)
            if lease is not None:
                with contextlib.suppress(ValueError):
                    self._board.request(role, "extend", lease)

    def _forget_finished_locked(self) -> None:
        live = {entry[0] for entry in self._active.values()}
        for job_id in [j for j in self._wakes if j not in live]:
            if self._jobs.get_any(job_id) is None:
                del self._wakes[job_id]

    # ---- the waker side (contract v2) -----------------------------------------------------------------------------------

    def requests(self, ts: str | None, nonce: str | None, sig: str | None) -> tuple[int, dict[str, Any]]:
        if not verify_http(self._secret, "GET", POLL_PATH, ts, nonce, sig, None, nonces=self._nonces, clock=self._wall):
            return 401, {}
        return 200, {"v": 1, "requests": self._board.signed(self._secret)}

    def deliver(self, ts: str | None, nonce: str | None, sig: str | None, body: bytes) -> tuple[int, dict[str, Any]]:
        if not verify_http(self._secret, "POST", RESULT_PATH, ts, nonce, sig, body, nonces=self._nonces, clock=self._wall):
            return 401, {}
        try:
            data = json.loads(body.decode("utf-8"))
        except ValueError:
            return 400, {"error": "bad_request"}
        if not isinstance(data, dict) or data.get("v") != 1:
            return 400, {"error": "bad_request"}
        return 200, {"v": 1, "applied": self._board.deliver(data.get("results"))}
