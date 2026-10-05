"""#148: partner-visible audit log of analyse-facts calls. Constructed inputs only."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from pravrudhi.api import identity
from pravrudhi.api.partner import PartnerApiConfig, build_partner_router
from pravrudhi.application import tenancy
from tests.test_api_partner import BNS69_DENY, BNS69_EL, TOY_FACTS, _agent, _proof_script
from tests.test_partner_key_metering import ADMIN, _key, _req

H = tenancy.API_KEY_HEADER
AUDIT = "/api/v1/audit"
T0 = datetime(2026, 1, 1, tzinfo=UTC)


class Clock:
    def __init__(self) -> None:
        self.t = T0

    def __call__(self) -> datetime:
        return self.t


def _app(tmp_path: Path, clock: Clock, script: Any = None, executor: Callable[[Callable[[], None]], Any] | None = None,
         retention_s: float = 1000.0) -> FastAPI:
    agent = _agent(tmp_path, script if script is not None else _proof_script())
    cfg = PartnerApiConfig(
        rate_limit_per_minute=1000, max_concurrent=100, trust_proxy_header=False, audit_retention_s=retention_s
    )
    app = FastAPI()
    app.include_router(
        build_partner_router(tmp_path, agent_factory=lambda _r: agent, config=cfg, clock=clock, job_executor=executor)
    )
    app.dependency_overrides[identity.current_user] = lambda: ADMIN
    return app


def _rows(c: TestClient, secret: str, **params: Any) -> dict[str, Any]:
    r = c.get(AUDIT, headers={H: secret}, params=params)
    assert r.status_code == 200
    return r.json()


def test_a_call_is_recorded_with_run_contracts_outcomes_and_status_and_no_fact_text(monkeypatch: Any, tmp_path: Path) -> None:
    monkeypatch.setenv("PRAVRUDHI_ADMINS", ADMIN.id)
    c = TestClient(_app(tmp_path, Clock()))
    secret = _key(c, "acme")
    body = c.post("/api/v1/analyse-facts", json=_req(), headers={H: secret}).json()
    row = _rows(c, secret)["rows"][0]
    assert row["status_code"] == 200 and row["mode"] == "sync" and row["run_id"] == body["run_id"]
    assert row["contract_ids"] == ["bns69"]
    assert row["outcomes"] == {x["contract_id"]: x["outcome"] for x in body["contracts"]}
    stored = "".join(p.read_text() for p in (tmp_path / ".pravrudhi" / "audit").glob("*.jsonl"))
    assert stored
    for text in [*TOY_FACTS, _req()["narrative"]]:
        assert text not in stored


def test_only_the_calling_keys_rows_are_returned_and_anonymous_is_refused(monkeypatch: Any, tmp_path: Path) -> None:
    monkeypatch.setenv("PRAVRUDHI_ADMINS", ADMIN.id)
    c = TestClient(_app(tmp_path, Clock()))
    a, b = _key(c, "acme-a"), _key(c, "acme-b")
    c.post("/api/v1/analyse-facts", json=_req(), headers={H: a})
    c.post("/api/v1/analyse-facts", json=_req(), headers={H: a})
    c.post("/api/v1/analyse-facts", json=_req(), headers={H: b})
    assert len(_rows(c, a)["rows"]) == 2 and len(_rows(c, b)["rows"]) == 1
    assert c.get(AUDIT).status_code == 401


def test_paging_is_newest_first_with_a_next_offset(monkeypatch: Any, tmp_path: Path) -> None:
    monkeypatch.setenv("PRAVRUDHI_ADMINS", ADMIN.id)
    clock = Clock()
    c = TestClient(_app(tmp_path, clock))
    secret = _key(c, "acme")
    ids = []
    for _ in range(3):
        clock.t += timedelta(seconds=1)
        ids.append(c.post("/api/v1/analyse-facts", json=_req(), headers={H: secret}).json()["run_id"])
    p1 = _rows(c, secret, limit=2)
    assert [r["run_id"] for r in p1["rows"]] == [ids[2], ids[1]] and p1["next_offset"] == 2
    p2 = _rows(c, secret, limit=2, offset=2)
    assert [r["run_id"] for r in p2["rows"]] == [ids[0]] and p2["next_offset"] is None


def test_retention_is_enforced_at_read_and_pruned_at_write(monkeypatch: Any, tmp_path: Path) -> None:
    monkeypatch.setenv("PRAVRUDHI_ADMINS", ADMIN.id)
    clock = Clock()
    c = TestClient(_app(tmp_path, clock, retention_s=100.0))
    secret = _key(c, "acme")
    c.post("/api/v1/analyse-facts", json=_req(), headers={H: secret})
    clock.t += timedelta(seconds=99)
    assert len(_rows(c, secret)["rows"]) == 1
    clock.t += timedelta(seconds=2)
    assert _rows(c, secret)["rows"] == []
    c.post("/api/v1/analyse-facts", json=_req(), headers={H: secret})
    stored = (tmp_path / ".pravrudhi" / "audit" / "calls.jsonl").read_text().strip().splitlines()
    assert len(stored) == 1


def test_a_failed_judge_is_audited_as_503_with_no_run_id(monkeypatch: Any, tmp_path: Path) -> None:
    monkeypatch.setenv("PRAVRUDHI_ADMINS", ADMIN.id)
    script = {BNS69_EL[0]: [RuntimeError("down")] * 10, BNS69_EL[1]: [RuntimeError("down")] * 10,
              BNS69_DENY: [RuntimeError("down")] * 10}
    c = TestClient(_app(tmp_path, Clock(), script))
    secret = _key(c, "acme")
    assert c.post("/api/v1/analyse-facts", json=_req(), headers={H: secret}).status_code == 503
    row = _rows(c, secret)["rows"][0]
    assert row["status_code"] == 503 and row["run_id"] is None and row["outcomes"] == {}


def test_job_runs_are_audited_too(monkeypatch: Any, tmp_path: Path) -> None:
    monkeypatch.setenv("PRAVRUDHI_ADMINS", ADMIN.id)
    c = TestClient(_app(tmp_path, Clock(), executor=lambda t: t()))
    secret = _key(c, "acme")
    c.post("/api/v1/analyse-facts/jobs", json=_req(), headers={H: secret})
    row = _rows(c, secret)["rows"][0]
    assert row["mode"] == "job" and row["status_code"] == 200 and row["run_id"]


def test_text_smuggled_in_contract_ids_is_not_stored(monkeypatch: Any, tmp_path: Path) -> None:
    monkeypatch.setenv("PRAVRUDHI_ADMINS", ADMIN.id)
    c = TestClient(_app(tmp_path, Clock()))
    secret = _key(c, "acme")
    smuggled = "the accused told me he hid the knife in the barn"
    r = c.post("/api/v1/analyse-facts", json={**_req(), "contract_ids": [smuggled]}, headers={H: secret})
    assert r.status_code == 422
    row = _rows(c, secret)["rows"][0]
    assert row["contract_ids"] == ["invalid-id"] and row["status_code"] == 422
    assert smuggled not in (tmp_path / ".pravrudhi" / "audit" / "calls.jsonl").read_text()
