"""Intake routes (pravrudhi#414): POST /api/v1/intake (plain, deterministic, no model) and POST /api/v1/intake/assist (the
assisted run, falling back to the same deterministic bundle). Pasted text only; invented text only; sign-in required."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from pravrudhi.api import identity
from pravrudhi.api.partner import PartnerApiConfig, build_partner_router
from pravrudhi.application import assistant_tier as TIER
from pravrudhi.application.a0_assist import assist as AS
from pravrudhi.application.a0_assist import intake
from pravrudhi.application.a0_assist import limits as L
from pravrudhi.application.a0_assist import runtime as RT
from pravrudhi.application.case_index import CaseRecord, insert_case, open_index
from tests import test_a0_assist_request as RQ
from tests import test_nyaya_agent as T
from tests.test_api_partner import _agent, _proof_script

USER = identity.User("u1", "user@example.test", "user")
CFG = PartnerApiConfig(rate_limit_per_minute=1000, max_concurrent=1, trust_proxy_header=False)
REPO = Path(__file__).resolve().parent.parent
TEXT = (
    "1. The complainant paid the accused a deposit for a flat on 3 March.\n\n"
    "2. The accused did not hand over the flat and did not refund the deposit.\n\n"
    "3. The complainant relies on Narandas Karsondas v. S.A. Kamtam (1977) 3 SCC 247 for the rule on time."
)
PLAIN, ASSIST = "/api/v1/intake", "/api/v1/intake/assist"


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-real")
    monkeypatch.delenv("PRAVRUDHI_CITATION_INDEX", raising=False)
    TIER.reset()
    yield
    RT.reset()
    TIER.reset()


def _index(tmp_path: Path) -> Path:
    p = tmp_path / "idx.sqlite3"
    conn = open_index(p)
    citer = "In Narandas Karsondas v. S.A. Kamtam and Anr. (1977) 3 SCC 247 the Court clarified the rule."
    insert_case(conn, CaseRecord("c1", "Later Case v Other", "Supreme Court", 2005, "sc_pdf", "/x", citer))
    insert_case(conn, CaseRecord("r1", "Narandas Karsondas vs S A Kamtam and Anr", "Supreme Court", 1977, "sc_pdf", "/y", "body"))
    conn.commit()
    conn.close()
    return p


def _client(tmp_path: Path, *, user: Any = USER, index: Path | None = None) -> TestClient:
    app = FastAPI()
    agent = _agent(tmp_path, _proof_script())
    app.include_router(build_partner_router(tmp_path, agent_factory=lambda _r: agent, config=CFG, citation_index_path=index))
    app.dependency_overrides[identity.current_user] = lambda: user
    return TestClient(app)


# ---- the plain deterministic route --------------------------------------------------------------------------------------------
def test_an_anonymous_caller_is_told_intake_is_for_signed_in_users(tmp_path: Path) -> None:
    r = _client(tmp_path, user=None).post(PLAIN, json={"text": TEXT})
    assert r.status_code == 401 and "signed-in" in r.json()["detail"]


def test_a_signed_in_caller_gets_the_bundle_with_machine_tokens_only(tmp_path: Path) -> None:
    r = _client(tmp_path).post(PLAIN, json={"text": TEXT})
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["client_data"] is True and b["run_id"].startswith("nyaya-intake-")
    bundle = b["bundle"]
    assert bundle["clean_status"] in ("cleaned", "served_raw")
    assert bundle["facts"] and all(f["status"] in ("span", "raw_fallback") for f in bundle["facts"])
    assert all(c["verifier_status"] == "NOT_CHECKED" for c in bundle["citations"])  # no index on this deployment
    assert set(bundle["checks"]) >= {"C1", "C2", "C3"}


def test_with_a_citation_index_the_citations_carry_the_verifiers_own_result(tmp_path: Path) -> None:
    r = _client(tmp_path, index=_index(tmp_path)).post(PLAIN, json={"text": TEXT})
    cits = r.json()["bundle"]["citations"]
    assert cits and any(c["verifier_status"] == "IN_INDEX" for c in cits)


@pytest.mark.parametrize("body", [{"text": ""}, {"text": "x" * 200_001}, {}, {"text": 5}])
def test_a_bad_text_is_422_and_nothing_runs(tmp_path: Path, body: dict[str, Any]) -> None:
    assert _client(tmp_path).post(PLAIN, json=body).status_code == 422


def test_text_the_tools_refuse_is_a_422_not_a_crash(tmp_path: Path) -> None:
    assert _client(tmp_path).post(PLAIN, json={"text": "   \n  "}).status_code == 422


def test_intake_does_not_need_the_judges_so_a_parked_judge_does_not_block_it(tmp_path: Path) -> None:
    # the screening admission would answer judges_offline; intake (screening=False) skips that check
    calls: list[str] = []

    def fetch(url: str, headers: Any) -> dict[str, Any]:
        calls.append(url)
        return {"jobs": {"inQueue": 0, "inProgress": 0, "completed": 1},
                "workers": {"ready": 0, "idle": 0, "running": 0, "initializing": 0, "throttled": 0, "unhealthy": 0}}

    cfg = PartnerApiConfig(rate_limit_per_minute=1000, max_concurrent=1, trust_proxy_header=False, judge_health_check=True)
    app = FastAPI()
    agent = _agent(tmp_path, _proof_script())
    app.include_router(build_partner_router(tmp_path, agent_factory=lambda _r: agent, config=cfg, judge_health_fetch=fetch))
    app.dependency_overrides[identity.current_user] = lambda: USER
    assert TestClient(app).post(PLAIN, json={"text": TEXT}).status_code == 200 and calls == []


def test_the_intake_routes_are_not_in_the_public_contract(tmp_path: Path) -> None:
    paths = set(_client(tmp_path).get("/openapi.json").json().get("paths", {}))
    assert PLAIN not in paths and ASSIST not in paths


def test_the_rate_limit_applies_to_intake(tmp_path: Path) -> None:
    cfg = PartnerApiConfig(rate_limit_per_minute=2, max_concurrent=1, trust_proxy_header=False)
    app = FastAPI()
    agent = _agent(tmp_path, _proof_script())
    app.include_router(build_partner_router(tmp_path, agent_factory=lambda _r: agent, config=cfg))
    app.dependency_overrides[identity.current_user] = lambda: USER
    c = TestClient(app)
    codes = [c.post(PLAIN, json={"text": TEXT}).status_code for _ in range(3)]
    assert codes == [200, 200, 429]


# ---- the assisted route -------------------------------------------------------------------------------------------------------
def _intake_cfg() -> Any:
    return RQ.make_cfg(workflows={"intake": {"agent_id": "agent_in", "agent_version": 1, "environment_id": "env_in"}})


def _program(text: str, *, tamper: bool = False) -> Any:
    def program(tr: Any) -> Any:
        info = yield {"id": "e1", "type": "agent.custom_tool_use", "name": "read_input", "input": {}}
        sha = info["raw_sha256"] if not tamper else "0" * 64
        yield {"id": "e2", "type": "agent.custom_tool_use", "name": "clean", "input": {"raw_sha256": sha}}
        yield {"id": "e3", "type": "agent.custom_tool_use", "name": "split_facts", "input": {}}
        yield {"id": "e4", "type": "agent.custom_tool_use", "name": "extract_citations", "input": {"raw_sha256": sha}}
        yield {"id": "e5", "type": "agent.custom_tool_use", "name": "bundle", "input": {"raw_sha256": sha}}
        yield {"id": "m1", "type": "agent.message", "content": [{"type": "text", "text": "SECRET-AGENT-PROSE"}]}
        yield {"id": "z", "type": "session.status_idle", "stop_reason": {"type": "end_turn"}}

    return program


def _assist_client(tmp_path: Path, *, cfg: Any = None, program: Any = None, user: Any = USER) -> TestClient:
    c = _client(tmp_path, user=user)
    shipped = yaml.safe_load((REPO / "configs" / "assistant_tier.yaml").read_text())
    (tmp_path / "configs").mkdir(parents=True, exist_ok=True)
    (tmp_path / "configs" / "assistant_tier.yaml").write_text(yaml.safe_dump(shipped))  # the tier reads this file
    cfg = cfg or _intake_cfg()
    clock = RQ.Clock()
    rt = RT.Runtime(cfg)
    rt.store = RQ.FakeStore()
    rt.transport = RQ.FakeTransport(program or _program(TEXT), clock)
    rt.limiter = L.AssistLimiter(cfg.runs_per_user_per_day, cfg.runs_per_org_per_day, cfg.per_ip_per_minute)
    rt.slots = threading.BoundedSemaphore(cfg.max_concurrent_sessions)
    RT.reset()
    RT._runtime = rt
    c.rt = rt  # type: ignore[attr-defined]
    return c


def test_assist_is_404_when_the_runtime_is_dark(tmp_path: Path) -> None:
    c = _client(tmp_path)
    RT.reset()
    RT._runtime = RT.Runtime(RQ.make_cfg(assist_route_enabled=False))
    assert c.post(ASSIST, json={"text": TEXT, "consent": True}).status_code == 404


def test_assist_needs_consent_and_a_signed_in_caller(tmp_path: Path) -> None:
    c = _assist_client(tmp_path)
    r = c.post(ASSIST, json={"text": TEXT})
    assert r.status_code == 400 and r.json() == {"error": "consent_required"} and c.rt.transport.calls == []  # type: ignore[attr-defined]
    anon = _assist_client(tmp_path / "a", user=None)
    assert anon.post(ASSIST, json={"text": TEXT, "consent": True}).status_code == 401


def test_a_finished_assisted_run_returns_the_driver_bundle_and_never_the_agents_prose(tmp_path: Path) -> None:
    c = _assist_client(tmp_path)
    r = c.post(ASSIST, json={"text": TEXT, "consent": True, "session_id": "cl-1"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["assistant"]["used"] is True and body["assistant"]["session_id"] == "cl-1"
    steps = [s["name"] for s in body["assistant"]["steps"]]
    assert steps == ["read_input", "clean", "split_facts", "extract_citations", "bundle"]
    assert body["bundle"]["bundle_sha256"] and "SECRET-AGENT-PROSE" not in r.text and body["assistant"]["summary"] is None
    assert "retention_notice" not in body
    plain = _client(tmp_path / "p").post(PLAIN, json={"text": TEXT}).json()["bundle"]
    assert body["bundle"] == plain  # the agent adds nothing: the same deterministic bundle


def test_the_first_message_carries_the_pasted_text(tmp_path: Path) -> None:
    c = _assist_client(tmp_path)
    c.post(ASSIST, json={"text": TEXT, "consent": True})
    first = c.rt.transport.posted[0][0]["content"][0]["text"]  # type: ignore[attr-defined]
    assert first == AS.intake_user_message({"text": TEXT}) and "Narandas" in first


def test_a_tampered_tool_input_is_a_safety_refusal_with_the_deterministic_result(tmp_path: Path) -> None:
    c = _assist_client(tmp_path, program=_program(TEXT, tamper=True))
    r = c.post(ASSIST, json={"text": TEXT, "consent": True})
    body = r.json()
    assert r.status_code == 200 and body["assistant"]["used"] is False and body["assistant"]["reason"] == "safety"
    assert body["bundle"]["facts"]  # the ordinary deterministic result, not an error
    assert "delete_session" in c.rt.transport.calls  # type: ignore[attr-defined]


def test_without_an_intake_agent_the_route_answers_with_the_deterministic_bundle_and_not_configured(tmp_path: Path) -> None:
    c = _assist_client(tmp_path, cfg=RQ.make_cfg())  # no workflows.intake
    r = c.post(ASSIST, json={"text": TEXT, "consent": True})
    assert r.status_code == 200 and r.json()["assistant"]["reason"] == "not_configured" and r.json()["bundle"]["facts"]
    assert c.rt.transport.calls == [] and c.rt.store.log == []  # type: ignore[attr-defined]


def test_the_shipped_config_has_no_intake_agent_yet() -> None:
    raw = yaml.safe_load((REPO / "configs" / "assistant_tier.yaml").read_text())
    assert raw["workflows"] == {}


def test_the_intake_message_is_json_of_the_text_only() -> None:
    m = AS.intake_user_message({"text": 'a "quoted" line\n\nnext'})
    body = m[len(AS.INTAKE_USER_MESSAGE_PREFIX):]
    assert m.startswith(AS.INTAKE_USER_MESSAGE_PREFIX) and "contract_ids" not in m
    assert json.loads(body) == {"text": 'a "quoted" line\n\nnext'}  # the text round-trips exactly as JSON
    assert intake.SCAN_LINE not in m and T is not None
