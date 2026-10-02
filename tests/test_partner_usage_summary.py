"""#147 (backend half): per-day usage buckets and the admin-only org usage summary. Constructed inputs only."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from pravrudhi.api import identity
from pravrudhi.api.partner import PartnerApiConfig, build_partner_router
from pravrudhi.application import tenancy
from tests.test_api_partner import _agent, _proof_script
from tests.test_partner_key_metering import _req

SECRET = "s" * tenancy.MIN_PROVISION_SECRET_LENGTH
ADM = {tenancy.TENANCY_PROVISION_HEADER: SECRET}
H = tenancy.API_KEY_HEADER


class Clock:
    def __init__(self, t: datetime) -> None:
        self.t = t

    def __call__(self) -> datetime:
        return self.t


def _client(tmp_path: Path, clock: Clock, monkeypatch: Any) -> TestClient:
    monkeypatch.setenv(tenancy.TENANCY_PROVISION_SECRET_ENV, SECRET)
    agent = _agent(tmp_path, _proof_script())
    cfg = PartnerApiConfig(rate_limit_per_minute=1000, max_concurrent=100, trust_proxy_header=False)
    app = FastAPI()
    app.include_router(build_partner_router(tmp_path, agent_factory=lambda _r: agent, config=cfg, clock=clock))
    app.dependency_overrides[identity.current_user] = lambda: None
    return TestClient(app)


def _org_key(c: TestClient, org: str) -> tuple[str, str]:
    assert c.post("/api/v1/orgs", json={"org_id": org, "name": org}, headers=ADM).status_code == 200
    r = c.post(f"/api/v1/orgs/{org}/keys", json={}, headers=ADM).json()
    return r["key_id"], r["secret"]


def test_calls_are_bucketed_by_utc_day_across_the_boundary(monkeypatch: Any, tmp_path: Path) -> None:
    clock = Clock(datetime(2026, 3, 1, 23, 59, 59, tzinfo=UTC))
    c = _client(tmp_path, clock, monkeypatch)
    kid, secret = _org_key(c, "acme")
    c.post("/api/v1/analyse-facts", json=_req(), headers={H: secret})
    clock.t += timedelta(seconds=1)
    c.post("/api/v1/analyse-facts", json=_req(), headers={H: secret})
    c.post("/api/v1/analyse-facts", json=_req(), headers={H: secret})
    body = c.get("/api/v1/orgs/acme/usage/summary", headers=ADM).json()
    (k,) = body["keys"]
    assert k["key_id"] == kid
    assert k["days"] == [{"day": "2026-03-01", "calls": 1, "failed": 0}, {"day": "2026-03-02", "calls": 2, "failed": 0}]


def test_window_drops_old_days_and_includes_today(monkeypatch: Any, tmp_path: Path) -> None:
    clock = Clock(datetime(2026, 3, 1, 12, tzinfo=UTC))
    c = _client(tmp_path, clock, monkeypatch)
    _, secret = _org_key(c, "acme")
    c.post("/api/v1/analyse-facts", json=_req(), headers={H: secret})
    clock.t += timedelta(days=2)
    c.post("/api/v1/analyse-facts", json=_req(), headers={H: secret})
    days = c.get("/api/v1/orgs/acme/usage/summary?days=2", headers=ADM).json()["keys"][0]["days"]
    assert [d["day"] for d in days] == ["2026-03-03"]
    days = c.get("/api/v1/orgs/acme/usage/summary?days=3", headers=ADM).json()["keys"][0]["days"]
    assert [d["day"] for d in days] == ["2026-03-01", "2026-03-03"]


def test_no_credential_or_a_partner_key_is_401_and_a_wrong_secret_is_refused(monkeypatch: Any, tmp_path: Path) -> None:
    c = _client(tmp_path, Clock(datetime(2026, 3, 1, tzinfo=UTC)), monkeypatch)
    _, secret = _org_key(c, "acme")
    url = "/api/v1/orgs/acme/usage/summary"
    assert c.get(url).status_code == 401
    assert c.get(url, headers={H: secret}).status_code == 401
    assert c.get(url, headers={tenancy.TENANCY_PROVISION_HEADER: "x" * len(SECRET)}).status_code == 401


def test_a_summary_never_contains_another_orgs_keys_and_unknown_org_is_404(monkeypatch: Any, tmp_path: Path) -> None:
    c = _client(tmp_path, Clock(datetime(2026, 3, 1, tzinfo=UTC)), monkeypatch)
    ka, _ = _org_key(c, "acme-a")
    kb, sb = _org_key(c, "acme-b")
    c.post("/api/v1/analyse-facts", json=_req(), headers={H: sb})
    a = c.get("/api/v1/orgs/acme-a/usage/summary", headers=ADM).json()
    assert [k["key_id"] for k in a["keys"]] == [ka] and a["keys"][0]["days"] == []
    assert kb not in str(a)
    assert c.get("/api/v1/orgs/nope-org/usage/summary", headers=ADM).status_code == 404


def test_a_revoked_key_still_shows_its_history_flagged_revoked(monkeypatch: Any, tmp_path: Path) -> None:
    c = _client(tmp_path, Clock(datetime(2026, 3, 1, tzinfo=UTC)), monkeypatch)
    kid, secret = _org_key(c, "acme")
    c.post("/api/v1/analyse-facts", json=_req(), headers={H: secret})
    assert c.post(f"/api/v1/orgs/acme/keys/{kid}/revoke", headers=ADM).status_code == 200
    (k,) = c.get("/api/v1/orgs/acme/usage/summary", headers=ADM).json()["keys"]
    assert k["revoked"] is True and k["days"][0]["calls"] == 1
    assert "secret" not in str(k) and "hash" not in str(k)
