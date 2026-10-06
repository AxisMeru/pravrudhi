"""#144: per-key persistent metering and per-key rate limit on analyse-facts. Constructed inputs only."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from pravrudhi.api import identity
from pravrudhi.api.identity import User
from pravrudhi.api.partner import PartnerApiConfig, build_partner_router
from pravrudhi.application import tenancy
from pravrudhi.application.nyaya_judges import ElementJudgment
from tests.test_api_partner import BNS69_DENY, BNS69_EL, TOY_FACTS, _agent, _est, _not, _proof_script  # noqa: F401

ADMIN = User(id="op-1", email="op@example.com", role="authenticated")
H = tenancy.API_KEY_HEADER


class FakeClock:
    """The per-key limiter's clock (#303): starts mid-window so a slow runner can never roll the window over between two
    requests; a test that wants the window to roll calls `advance`, nothing sleeps."""

    def __init__(self, start: float = 1_000_030.0) -> None:
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


def _app(tmp_path: Path, script: dict[str, list[ElementJudgment | Exception]] | None = None,
         ip_limit: int = 1000, clock: FakeClock | None = None) -> FastAPI:
    agent = _agent(tmp_path, script if script is not None else _proof_script())
    cfg = PartnerApiConfig(rate_limit_per_minute=ip_limit, max_concurrent=100, trust_proxy_header=False)
    app = FastAPI()
    router = build_partner_router(tmp_path, agent_factory=lambda _r: agent, config=cfg, rate_clock=clock or FakeClock())
    app.include_router(router)
    app.dependency_overrides[identity.current_user] = lambda: ADMIN
    return app


def _key(c: TestClient, org: str, limit: int = 60) -> str:
    c.post("/api/v1/orgs", json={"org_id": org, "name": org})
    return str(c.post(f"/api/v1/orgs/{org}/keys", json={"rate_limit_per_minute": limit}).json()["secret"])


def _req() -> dict[str, Any]:
    return {"facts": TOY_FACTS, "narrative": "TOY narrative.", "contract_ids": ["bns69"]}


def _usage(c: TestClient, org: str, secret: str) -> dict[str, Any]:
    return c.get(f"/api/v1/orgs/{org}/usage", headers={H: secret}).json()


def test_calls_are_counted_per_key_and_survive_an_app_restart(monkeypatch: Any, tmp_path: Path) -> None:
    monkeypatch.setenv("PRAVRUDHI_ADMINS", ADMIN.id)
    c = TestClient(_app(tmp_path))
    secret = _key(c, "acme")
    for _ in range(3):
        assert c.post("/api/v1/analyse-facts", json=_req(), headers={H: secret}).status_code == 200
    restarted = TestClient(_app(tmp_path))
    u = _usage(restarted, "acme", secret)
    assert (u["calls"], u["failed"]) == (3, 0)
    assert u["calls_since_process_start"] <= 1


def test_a_key_over_its_limit_gets_429_and_other_keys_are_unaffected(monkeypatch: Any, tmp_path: Path) -> None:
    monkeypatch.setenv("PRAVRUDHI_ADMINS", ADMIN.id)
    clock = FakeClock()
    c = TestClient(_app(tmp_path, clock=clock))
    low, other = _key(c, "low", limit=2), _key(c, "other", limit=50)
    codes = [c.post("/api/v1/analyse-facts", json=_req(), headers={H: low}).status_code for _ in range(3)]
    assert codes == [200, 200, 429]
    r = c.post("/api/v1/analyse-facts", json=_req(), headers={H: low})
    assert r.status_code == 429 and "Retry-After" in r.headers
    assert c.post("/api/v1/analyse-facts", json=_req(), headers={H: other}).status_code == 200
    assert tenancy.usage_counts(tmp_path, next(k.key_id for k in tenancy.keys_for_org(tmp_path, "low")))[0] == 2
    # The window rolling over is driven by the fake clock, not by waiting: after it, the key is admitted again.
    clock.advance(60)
    assert c.post("/api/v1/analyse-facts", json=_req(), headers={H: low}).status_code == 200


def test_503s_are_counted_separately_within_calls(monkeypatch: Any, tmp_path: Path) -> None:
    monkeypatch.setenv("PRAVRUDHI_ADMINS", ADMIN.id)
    script = {BNS69_EL[0]: [RuntimeError("down")] * 10, BNS69_EL[1]: [RuntimeError("down")] * 10,
              BNS69_DENY: [RuntimeError("down")] * 10}
    c = TestClient(_app(tmp_path, script))
    secret = _key(c, "acme")
    assert c.post("/api/v1/analyse-facts", json=_req(), headers={H: secret}).status_code == 503
    u = TestClient(_app(tmp_path)).get("/api/v1/orgs/acme/usage", headers={H: secret}).json()
    assert (u["calls"], u["failed"]) == (1, 1)


def test_anonymous_calls_keep_the_ip_limit_and_are_not_metered(monkeypatch: Any, tmp_path: Path) -> None:
    monkeypatch.setenv("PRAVRUDHI_ADMINS", ADMIN.id)
    c = TestClient(_app(tmp_path, ip_limit=1))
    assert c.post("/api/v1/analyse-facts", json=_req()).status_code == 200
    assert c.post("/api/v1/analyse-facts", json=_req()).status_code == 429
    assert not (tenancy.tenancy_dir(tmp_path) / "usage.json").exists()
