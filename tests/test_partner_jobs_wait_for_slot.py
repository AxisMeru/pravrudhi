"""#406: an accepted async job (202) waits for a free agent slot instead of failing with agent_at_capacity behind the 202.
Real threads and a real ConcurrencyLimiter (max_concurrent 2) around an agent that blocks until released. Both arms:
wait on (all jobs finish, never more than two in flight), wait 0 (the old behaviour), a bounded wait that runs out."""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from pravrudhi.api import identity
from pravrudhi.api.partner import (
    ConcurrencyLimiter,
    PartnerApiConfig,
    build_partner_router,
    load_partner_api_config,
)
from pravrudhi.application import tenancy
from tests.test_api_partner import _agent, _proof_script
from tests.test_partner_key_metering import ADMIN, FakeClock, _key, _req

H = tenancy.API_KEY_HEADER
JOBS = "/api/v1/analyse-facts/jobs"
REPO = Path(__file__).resolve().parent.parent


class Gate:
    """Wraps the agent's `run`: counts the runs in flight and blocks each until `open()`."""

    def __init__(self) -> None:
        self.event = threading.Event()
        self.lock = threading.Lock()
        self.now = 0
        self.peak = 0

    def wrap(self, run: Any) -> Any:
        def wrapped(*a: Any, **k: Any) -> Any:
            with self.lock:
                self.now += 1
                self.peak = max(self.peak, self.now)
            try:
                self.event.wait(20)
                return run(*a, **k)
            finally:
                with self.lock:
                    self.now -= 1

        return wrapped


def _client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, gate: Gate, wait_s: float) -> tuple[TestClient, str]:
    monkeypatch.setenv("PRAVRUDHI_ADMINS", ADMIN.id)
    agent = _agent(tmp_path, _proof_script())
    agent.run = gate.wrap(agent.run)  # type: ignore[method-assign]
    cfg = PartnerApiConfig(rate_limit_per_minute=1000, max_concurrent=2, trust_proxy_header=False, job_slot_wait_s=wait_s,
                           job_max_unfinished_per_key=8)
    app = FastAPI()
    app.include_router(build_partner_router(tmp_path, agent_factory=lambda _r: agent, config=cfg, rate_clock=FakeClock()))
    app.dependency_overrides[identity.current_user] = lambda: ADMIN
    c = TestClient(app)
    return c, _key(c, "acme")


def _submit(c: TestClient, secret: str, n: int) -> list[str]:
    ids = []
    for _ in range(n):
        r = c.post(JOBS, json=_req(), headers={H: secret})
        assert r.status_code == 202
        ids.append(r.json()["job_id"])
    return ids


def _settle(c: TestClient, secret: str, ids: list[str], timeout: float = 20.0) -> list[dict[str, Any]]:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        jobs = [c.get(f"{JOBS}/{i}", headers={H: secret}).json() for i in ids]
        if all(j["status"] in ("done", "failed") for j in jobs):
            return jobs
        time.sleep(0.05)
    raise AssertionError("jobs did not settle")


def test_six_simultaneous_jobs_all_finish_and_never_more_than_two_run_at_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    gate = Gate()
    c, secret = _client(monkeypatch, tmp_path, gate, wait_s=30.0)
    ids = _submit(c, secret, 6)
    time.sleep(0.5)  # the first two hold the slots; the others are waiting, not failed
    assert gate.now == 2
    gate.event.set()
    jobs = _settle(c, secret, ids)
    assert [j["status"] for j in jobs] == ["done"] * 6
    assert gate.peak == 2


def test_with_the_wait_off_the_old_behaviour_is_kept(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    gate = Gate()
    c, secret = _client(monkeypatch, tmp_path, gate, wait_s=0.0)
    ids = _submit(c, secret, 6)
    time.sleep(0.5)
    gate.event.set()
    jobs = _settle(c, secret, ids)
    failed = [j for j in jobs if j["status"] == "failed"]
    assert len(failed) == 4 and all(j["error"]["status_code"] == 503 for j in failed)
    assert all(j["error"]["body"] == {"error": "agent_at_capacity", "detail": "the nyaya agent is at capacity; retry shortly"}
               or j["error"]["body"].get("error") == "agent_at_capacity" for j in failed)
    assert len([j for j in jobs if j["status"] == "done"]) == 2


def test_a_bounded_wait_that_runs_out_fails_honestly_with_agent_at_capacity(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    gate = Gate()
    c, secret = _client(monkeypatch, tmp_path, gate, wait_s=0.3)
    ids = _submit(c, secret, 4)
    time.sleep(1.0)  # past the wait: the two that never got a slot have failed while the first two still hold theirs
    jobs = [c.get(f"{JOBS}/{i}", headers={H: secret}).json() for i in ids]
    assert sorted(j["status"] for j in jobs).count("failed") == 2
    gate.event.set()
    jobs = _settle(c, secret, ids)
    assert sorted(j["status"] for j in jobs) == ["done", "done", "failed", "failed"]
    assert {j["error"]["body"].get("error") for j in jobs if j["status"] == "failed"} == {"agent_at_capacity"}


def test_the_synchronous_route_never_waits(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    gate = Gate()
    c, secret = _client(monkeypatch, tmp_path, gate, wait_s=30.0)
    holders = [threading.Thread(target=lambda: c.post("/api/v1/analyse-facts", json=_req(), headers={H: secret}), daemon=True)
               for _ in range(2)]
    for t in holders:
        t.start()
    time.sleep(0.5)
    assert gate.now == 2
    t0 = time.monotonic()
    r = c.post("/api/v1/analyse-facts", json=_req(), headers={H: secret})
    assert r.status_code == 503 and time.monotonic() - t0 < 2.0  # at once, not after the 30 s job wait
    gate.event.set()
    for t in holders:
        t.join(10)


def test_the_limiter_waits_only_when_asked() -> None:
    lim = ConcurrencyLimiter(1)
    assert lim.acquire() is True
    t0 = time.monotonic()
    assert lim.acquire() is False and time.monotonic() - t0 < 0.2  # default: non-blocking
    assert lim.acquire(0.2) is False and time.monotonic() - t0 >= 0.2  # bounded wait runs out
    threading.Timer(0.1, lim.release).start()
    assert lim.acquire(5.0) is True  # a release inside the wait hands the slot over


@pytest.mark.parametrize("bad", [-1, float("nan"), float("inf"), True, "10", None])
def test_job_slot_wait_s_must_be_a_finite_number_not_below_zero(tmp_path: Path, bad: Any) -> None:
    (tmp_path / "configs").mkdir()
    import yaml

    body = yaml.safe_load((REPO / "configs" / "partner_api.yaml").read_text())
    body["job_slot_wait_s"] = bad
    (tmp_path / "configs" / "partner_api.yaml").write_text(yaml.safe_dump(body))
    with pytest.raises(ValueError, match="job_slot_wait_s"):
        load_partner_api_config(tmp_path)


def test_the_shipped_config_sets_a_wait_and_zero_is_allowed(tmp_path: Path) -> None:
    import yaml

    assert load_partner_api_config(REPO).job_slot_wait_s == 600.0
    (tmp_path / "configs").mkdir()
    body = yaml.safe_load((REPO / "configs" / "partner_api.yaml").read_text())
    body["job_slot_wait_s"] = 0
    (tmp_path / "configs" / "partner_api.yaml").write_text(yaml.safe_dump(body))
    assert load_partner_api_config(tmp_path).job_slot_wait_s == 0.0
