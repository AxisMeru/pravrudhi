"""#236: the serving guards (`serving_api`, `serving_org`) are ContextVars, which a pool thread does not inherit.
Every worker thread that can run under a request carries a copy of the submitting thread's context."""

from __future__ import annotations

import contextvars
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from pravrudhi.api import identity
from pravrudhi.api.partner import PartnerApiConfig, build_partner_router
from pravrudhi.application import tenancy
from pravrudhi.application.credentials import ServingApiMiddleware, serving_api, serving_org
from pravrudhi.application.nyaya_agent import NyayaAgent
from pravrudhi.application.nyaya_judges import ElementJudgment, JudgeRequest
from tests.test_api_partner import _agent, _proof_script, _req
from tests.test_partner_key_metering import ADMIN, _key

H = tenancy.API_KEY_HEADER
JOBS = "/api/v1/analyse-facts/jobs"


@dataclass
class ContextProbe:
    """Wraps a judge and records the serving ContextVars as seen on whatever thread calls it."""

    inner: Any
    seen: list[tuple[bool, str | None]] = field(default_factory=list)
    name: str = "probe"

    def judge(self, request: JudgeRequest) -> ElementJudgment:
        self.seen.append((serving_api.get(), serving_org.get()))
        return self.inner.judge(request)


def test_a_job_worker_thread_sees_the_request_serving_context(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("PRAVRUDHI_ADMINS", ADMIN.id)
    agent = _agent(tmp_path, _proof_script())
    probe = ContextProbe(agent.judge)
    agent = NyayaAgent(probe, agent.registry, agent.config)
    cfg = PartnerApiConfig(rate_limit_per_minute=1000, max_concurrent=100, trust_proxy_header=False)
    app = FastAPI()
    app.add_middleware(ServingApiMiddleware)
    app.include_router(build_partner_router(tmp_path, agent_factory=lambda _r: agent, config=cfg))
    app.dependency_overrides[identity.current_user] = lambda: ADMIN
    c = TestClient(app)
    secret = _key(c, "acme")
    jid = c.post(JOBS, json=_req(), headers={H: secret}).json()["job_id"]
    for _ in range(100):
        if c.get(f"{JOBS}/{jid}", headers={H: secret}).json()["status"] in ("done", "failed"):
            break
        time.sleep(0.05)
    assert probe.seen, "the judge never ran"
    assert set(probe.seen) == {(True, "acme")}


def test_an_injected_job_executor_also_runs_the_task_in_the_request_context(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("PRAVRUDHI_ADMINS", ADMIN.id)
    agent = _agent(tmp_path, _proof_script())
    probe = ContextProbe(agent.judge)
    agent = NyayaAgent(probe, agent.registry, agent.config)
    queued: list[Any] = []
    cfg = PartnerApiConfig(rate_limit_per_minute=1000, max_concurrent=100, trust_proxy_header=False)
    app = FastAPI()
    app.add_middleware(ServingApiMiddleware)
    app.include_router(build_partner_router(tmp_path, agent_factory=lambda _r: agent, config=cfg, job_executor=queued.append))
    app.dependency_overrides[identity.current_user] = lambda: ADMIN
    c = TestClient(app)
    secret = _key(c, "acme")
    c.post(JOBS, json=_req(), headers={H: secret})
    ctx_after_request = (serving_api.get(), serving_org.get())
    assert ctx_after_request == (False, None)  # the request's own context is gone by now
    queued.pop()()  # run later, on this thread, as a pool would
    assert set(probe.seen) == {(True, "acme")}


def test_the_agents_concurrent_judge_pool_runs_each_element_in_the_callers_context(tmp_path: Path) -> None:
    agent = _agent(tmp_path, _proof_script())
    probe = ContextProbe(agent.judge)
    config = replace(agent.config, max_concurrency=3)
    agent = NyayaAgent(probe, agent.registry, config)
    token_api, token_org = serving_api.set(True), serving_org.set("acme")
    try:
        agent.run(_req()["facts"], contract_ids=["bns69"])
    finally:
        serving_org.reset(token_org)
        serving_api.reset(token_api)
    assert len(probe.seen) >= 2
    assert set(probe.seen) == {(True, "acme")}


def test_a_plain_thread_pool_would_not_have_carried_it() -> None:
    """The failure this guards against, stated once: a pool thread starts with the defaults."""
    from concurrent.futures import ThreadPoolExecutor

    token = serving_api.set(True)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            assert pool.submit(serving_api.get).result() is False
            assert pool.submit(contextvars.copy_context().run, serving_api.get).result() is True
    finally:
        serving_api.reset(token)
