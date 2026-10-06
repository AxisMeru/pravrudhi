"""#146: async job mode for analyse-facts. Constructed inputs only."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from pravrudhi.api import identity
from pravrudhi.api.partner import PartnerApiConfig, build_partner_router
from pravrudhi.application import tenancy
from tests.test_api_partner import _agent, _proof_script
from tests.test_partner_key_metering import ADMIN, FakeClock, _key, _req

H = tenancy.API_KEY_HEADER
JOBS = "/api/v1/analyse-facts/jobs"


def _app(
    tmp_path: Path, executor: Callable[[Callable[[], None]], Any], script: Any = None, clock: Any = None, **cfg_kw: Any
) -> FastAPI:
    agent = _agent(tmp_path, script if script is not None else _proof_script())
    cfg = PartnerApiConfig(rate_limit_per_minute=1000, max_concurrent=100, trust_proxy_header=False, **cfg_kw)
    app = FastAPI()
    app.include_router(
        build_partner_router(
            tmp_path, agent_factory=lambda _r: agent, config=cfg, job_executor=executor, rate_clock=clock or FakeClock()
        )
    )
    app.dependency_overrides[identity.current_user] = lambda: ADMIN
    return app


def _inline(task: Callable[[], None]) -> None:
    task()


def test_job_result_equals_the_synchronous_body(monkeypatch: Any, tmp_path: Path) -> None:
    monkeypatch.setenv("PRAVRUDHI_ADMINS", ADMIN.id)
    c = TestClient(_app(tmp_path, _inline))
    secret = _key(c, "acme")
    sync = c.post("/api/v1/analyse-facts", json=_req(), headers={H: secret})
    r = c.post(JOBS, json=_req(), headers={H: secret})
    assert r.status_code == 202
    polled = c.get(f"{JOBS}/{r.json()['job_id']}", headers={H: secret}).json()
    assert polled["status"] == "done" and "error" not in polled
    drop = lambda b: {k: v for k, v in b.items() if k != "run_id"}  # noqa: E731
    assert drop(polled["result"]) == drop(sync.json())


def test_job_outlives_the_creating_request_and_is_pending_until_run(monkeypatch: Any, tmp_path: Path) -> None:
    monkeypatch.setenv("PRAVRUDHI_ADMINS", ADMIN.id)
    queued: list[Callable[[], None]] = []
    c = TestClient(_app(tmp_path, queued.append))
    secret = _key(c, "acme")
    jid = c.post(JOBS, json=_req(), headers={H: secret}).json()["job_id"]
    assert c.get(f"{JOBS}/{jid}", headers={H: secret}).json()["status"] == "pending"
    queued.pop()()
    assert c.get(f"{JOBS}/{jid}", headers={H: secret}).json()["status"] == "done"


def test_another_key_cannot_read_a_job_and_anonymous_is_refused(monkeypatch: Any, tmp_path: Path) -> None:
    monkeypatch.setenv("PRAVRUDHI_ADMINS", ADMIN.id)
    c = TestClient(_app(tmp_path, _inline))
    a, b = _key(c, "acme-a"), _key(c, "acme-b")
    jid = c.post(JOBS, json=_req(), headers={H: a}).json()["job_id"]
    assert c.get(f"{JOBS}/{jid}", headers={H: b}).status_code == 404
    assert c.get(f"{JOBS}/{jid}").status_code == 401
    assert c.post(JOBS, json=_req()).status_code == 401


def test_jobs_are_metered_and_rate_limited_like_sync_calls(monkeypatch: Any, tmp_path: Path) -> None:
    monkeypatch.setenv("PRAVRUDHI_ADMINS", ADMIN.id)
    clock = FakeClock()
    c = TestClient(_app(tmp_path, _inline, clock=clock))
    secret = _key(c, "lowlim", limit=2)
    codes = [c.post(JOBS, json=_req(), headers={H: secret}).status_code for _ in range(3)]
    assert codes == [202, 202, 429]
    assert tenancy.usage_counts(tmp_path, next(k.key_id for k in tenancy.keys_for_org(tmp_path, "lowlim")))[0] == 2
    clock.advance(60)  # the window rolls over by the fake clock, nothing sleeps
    assert c.post(JOBS, json=_req(), headers={H: secret}).status_code == 202


def test_unfinished_cap_per_key_gives_429(monkeypatch: Any, tmp_path: Path) -> None:
    monkeypatch.setenv("PRAVRUDHI_ADMINS", ADMIN.id)
    queued: list[Callable[[], None]] = []
    c = TestClient(_app(tmp_path, queued.append, job_max_unfinished_per_key=2))
    secret = _key(c, "acme")
    codes = [c.post(JOBS, json=_req(), headers={H: secret}).status_code for _ in range(3)]
    assert codes == [202, 202, 429]


def test_invalid_request_creates_no_job(monkeypatch: Any, tmp_path: Path) -> None:
    monkeypatch.setenv("PRAVRUDHI_ADMINS", ADMIN.id)
    queued: list[Callable[[], None]] = []
    c = TestClient(_app(tmp_path, queued.append, job_max_unfinished_per_key=1))
    secret = _key(c, "acme")
    bad = {"facts": [], "narrative": "x", "contract_ids": ["bns69"]}
    assert c.post(JOBS, json=bad, headers={H: secret}).status_code in (400, 422)
    assert c.post(JOBS, json=_req(), headers={H: secret}).status_code == 202
    assert len(queued) == 1


def test_a_crashing_run_becomes_a_failed_job_not_a_lost_one(monkeypatch: Any, tmp_path: Path) -> None:
    monkeypatch.setenv("PRAVRUDHI_ADMINS", ADMIN.id)
    from pravrudhi.api import partner

    def boom(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("x")

    c = TestClient(_app(tmp_path, _inline))
    secret = _key(c, "acme")
    monkeypatch.setattr(partner.AnalyseFactsResponse, "__init__", boom)
    jid = c.post(JOBS, json=_req(), headers={H: secret}).json()["job_id"]
    j = c.get(f"{JOBS}/{jid}", headers={H: secret}).json()
    assert j["status"] == "failed" and j["error"]["status_code"] == 500
