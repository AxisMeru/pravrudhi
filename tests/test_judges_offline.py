"""#311: a parked judge endpoint answers 503 `judges_offline` at once, with no job left queued. Mocked health only."""

from __future__ import annotations

import urllib.error
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from pravrudhi.api.partner import PartnerApiConfig, build_partner_router, load_partner_api_config
from pravrudhi.application import judge_endpoint_state as jes
from tests.test_api_partner import _NO_LIMIT_CONFIG, _agent, _proof_script, _req

URL = "https://api.runpod.ai/v2/ep123/openai/v1"
URL2 = "https://api.runpod.ai/v2/ep456/openai/v1"
ROOT = Path(__file__).resolve().parent.parent


def _health(
    ready: int = 0, idle: int = 0, running: int = 0, initializing: int = 0, queued: int = 0, active: int = 0,
    throttled: int = 0, unhealthy: int = 0,
) -> dict[str, Any]:
    return {
        "jobs": {"inQueue": queued, "inProgress": active, "completed": 7},
        "workers": {
            "ready": ready, "idle": idle, "running": running, "initializing": initializing,
            "throttled": throttled, "unhealthy": unhealthy,
        },
    }


@pytest.mark.parametrize(
    ("health", "want"),
    [
        (_health(), "parked"),
        (_health(initializing=1), "warming"),
        (_health(throttled=1), "warming"),  # waiting for capacity: proceed, not parked
        (_health(unhealthy=1), "warming"),  # being replaced: proceed, not parked
        (_health(queued=2), "warming"),  # work waiting, no worker yet: a warm is under way, not parked
        (_health(active=1), "warming"),
        (_health(ready=1), "ready"),
        (_health(idle=1), "ready"),
        (_health(running=2), "ready"),
        ({"jobs": {}}, "unknown"),
        ({"workers": {"ready": 0}}, "unknown"),
        (None, "unknown"),
        ({"jobs": {"inQueue": "x"}, "workers": {"ready": 0}}, "unknown"),
        ({"jobs": {"inQueue": -1}, "workers": {"ready": 0}}, "unknown"),
    ],
)
def test_classify(health: Any, want: str) -> None:
    assert jes.classify(health).status == want


def test_only_runpod_serverless_urls_are_checked() -> None:
    assert jes.runpod_endpoint_id(URL) == "ep123"
    assert jes.runpod_endpoint_id("https://ep789.api.runpod.ai/v1") == "ep789"  # a load-balancer endpoint
    for u in ("http://127.0.0.1:8110/v1", "https://example.com/v2/x/openai/v1", "", "https://api.runpod.ai/",
              "https://evil.example.com/ep.api.runpod.ai/v1", "https://a.b.api.runpod.ai/v1"):
        assert jes.runpod_endpoint_id(u) is None


def _fetcher(health: Any, calls: list[str] | None = None) -> Any:
    def fetch(url: str, headers: Any) -> dict[str, Any]:
        if calls is not None:
            calls.append(url)
        if isinstance(health, Exception):
            raise health
        return health
    return fetch


def test_only_the_health_url_is_read_with_the_inference_key_and_no_management_call() -> None:
    calls: list[str] = []
    seen: list[Any] = []

    def fetch(url: str, headers: Any) -> dict[str, Any]:
        calls.append(url)
        seen.append(dict(headers))
        return _health()

    assert jes.endpoint_state(URL, "k", fetch=fetch).status == "parked"
    assert calls == ["https://api.runpod.ai/v2/ep123/health"] and seen == [{"Authorization": "Bearer k"}]


@pytest.mark.parametrize("err", [TimeoutError(), urllib.error.URLError("x"), ValueError("bad json"), OSError()])
def test_a_failing_health_call_is_unknown_never_parked(err: Exception) -> None:
    assert jes.endpoint_state(URL, "k", fetch=_fetcher(err)).status == "unknown"


def test_the_state_read_is_cached_for_the_ttl() -> None:
    calls: list[str] = []
    t = [0.0]
    chk = jes.JudgeOfflineCheck(ttl_s=5.0, fetch=_fetcher(_health(), calls=calls), clock=lambda: t[0])
    assert chk.state(URL, "k").status == "parked" and chk.state(URL, "k").status == "parked"
    assert len(calls) == 1
    t[0] = 6.0
    chk.state(URL, "k")
    assert len(calls) == 2


def _client(tmp_path: Path, fetch: Any, *, second: str | None = None, check: bool = True) -> tuple[TestClient, Any]:
    agent = _agent(tmp_path, _proof_script())
    agent.config = replace(agent.config, house_judge={"base_url": URL},
                           second_judge={"base_url": second} if second else None)
    cfg = replace(_NO_LIMIT_CONFIG, judge_health_check=check)
    app = FastAPI()
    app.include_router(build_partner_router(tmp_path, agent_factory=lambda _r: agent, config=cfg, judge_health_fetch=fetch))
    return TestClient(app), agent


def test_a_parked_primary_answers_judges_offline_and_runs_nothing(tmp_path: Path) -> None:
    c, agent = _client(tmp_path, _fetcher(_health()))
    ran: list[int] = []
    agent.run = lambda *a, **k: ran.append(1)  # type: ignore[method-assign]
    r = c.post("/api/v1/analyse-facts", json=_req())
    assert r.status_code == 503 and r.json() == {"error": "judges_offline"}
    assert ran == []


def test_a_parked_second_judge_is_offline_too(tmp_path: Path) -> None:
    def fetch(url: str, headers: Any) -> dict[str, Any]:
        return _health(ready=1) if "ep123" in url else _health()
    c, _ = _client(tmp_path, fetch, second=URL2)
    assert c.post("/api/v1/analyse-facts", json=_req()).json() == {"error": "judges_offline"}


@pytest.mark.parametrize("health", [_health(ready=1), _health(initializing=1), _health(queued=3)])
def test_ready_and_warming_proceed(tmp_path: Path, health: dict[str, Any]) -> None:
    c, _ = _client(tmp_path, _fetcher(health))
    assert c.post("/api/v1/analyse-facts", json=_req()).status_code == 200


def test_a_failing_health_call_proceeds_and_does_not_claim_offline(tmp_path: Path) -> None:
    c, _ = _client(tmp_path, _fetcher(TimeoutError()))
    r = c.post("/api/v1/analyse-facts", json=_req())
    assert r.status_code == 200 and "judges_offline" not in r.text


def test_the_check_is_off_unless_configured(tmp_path: Path) -> None:
    c, _ = _client(tmp_path, _fetcher(_health()), check=False)
    assert c.post("/api/v1/analyse-facts", json=_req()).status_code == 200
    assert PartnerApiConfig.judge_health_check is False
    assert load_partner_api_config(ROOT).judge_health_check is True


def test_a_parked_job_submission_is_refused_and_leaves_no_job(tmp_path: Path) -> None:
    from pravrudhi.api import identity
    from tests.test_partner_jobs import ADMIN, _inline, _key

    agent = _agent(tmp_path, _proof_script())
    agent.config = replace(agent.config, house_judge={"base_url": URL})
    cfg = replace(_NO_LIMIT_CONFIG, judge_health_check=True)
    app = FastAPI()
    app.include_router(build_partner_router(tmp_path, agent_factory=lambda _r: agent, config=cfg, job_executor=_inline,
                                            judge_health_fetch=_fetcher(_health())))
    app.dependency_overrides[identity.current_user] = lambda: ADMIN
    import os
    os.environ["PRAVRUDHI_ADMINS"] = ADMIN.id
    try:
        c = TestClient(app)
        secret = _key(c, "acme")
        r = c.post("/api/v1/analyse-facts/jobs", json=_req(), headers={"X-Pravrudhi-Api-Key": secret})
        assert r.status_code == 503 and r.json() == {"error": "judges_offline"}
    finally:
        os.environ.pop("PRAVRUDHI_ADMINS", None)


def test_a_load_balancer_endpoint_reads_the_same_v2_health_url_not_the_container_route() -> None:
    calls: list[str] = []
    assert jes.endpoint_state("https://ep789.api.runpod.ai/v1", "k", fetch=_fetcher(_health(), calls)).status == "parked"
    assert calls == ["https://api.runpod.ai/v2/ep789/health"]
