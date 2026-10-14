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


# ---- #422: every mode the engine writes is readable back ----------------------------------------------------------------------
def test_a_keyed_assisted_call_leaves_a_row_the_audit_page_can_serve(tmp_path: Path, monkeypatch: Any) -> None:
    """Found by Track C: the assisted route wrote mode 'assist', which AuditRowOut did not declare, so GET /audit answered 500 for
    the key's whole page. Through the real route this time (a keyed, consenting call), then the page."""
    from pravrudhi.application import assistant_tier as tier
    from pravrudhi.application.a0_assist import runtime as rt
    from tests.test_a0_assist_route import BODY, URL
    from tests.test_a0_assist_route import client as assist_client

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-real")
    monkeypatch.setenv("PRAVRUDHI_ADMINS", ADMIN.id)
    tier.reset()
    try:
        c = assist_client(tmp_path, user=ADMIN)
        secret = _key(c, "acme")
        done = c.post(URL, json=BODY, headers={H: secret})
        assert done.status_code == 200 and done.json()["assistant"]["used"] is True
        page = c.get(AUDIT, headers={H: secret})
        assert page.status_code == 200
        modes = [row["mode"] for row in page.json()["rows"]]
        assert modes == ["assist"]
    finally:
        rt.reset()
        tier.reset()


def _call_modes(tree: Any) -> set[str]:
    """Every string-constant mode passed to the audit writers in partner.py, found from the syntax tree (not by pattern)."""
    import ast

    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = fn.id if isinstance(fn, ast.Name) else (fn.attr if isinstance(fn, ast.Attribute) else "")
        if name == "_audit" and len(node.args) >= 3 and isinstance(node.args[2], ast.Constant):
            found.add(str(node.args[2].value))
        if name == "_intake_audit" and len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
            found.add(str(node.args[1].value))
        if name == "record":
            for kw in node.keywords:
                if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
                    found.add(str(kw.value.value))
    return found


def test_every_mode_the_engine_writes_is_a_mode_the_audit_page_declares() -> None:
    """A guard against the same drift: every literal mode passed to the audit writers in partner.py is declared in
    AuditRowOut.mode, and the writers it must find are pinned, so a refactor cannot silently make the scan see nothing."""
    import ast
    from typing import get_args

    from pravrudhi.api import partner

    declared = set(get_args(partner.AuditRowOut.model_fields["mode"].annotation))
    written = _call_modes(ast.parse(Path(partner.__file__).read_text()))
    assert {"sync", "job", "assist", "verify", "intake", "intake_assist"} <= written  # the writers that exist today
    assert written <= declared, written - declared
    assert declared == written, "a declared mode nothing writes any more: remove it from the contract deliberately"
