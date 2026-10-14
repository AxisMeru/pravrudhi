"""Intake routes (pravrudhi#414): POST /api/v1/intake (plain, deterministic, no model) and POST /api/v1/intake/assist (the
assisted run, falling back to the same deterministic bundle). Pasted text only; invented text only; sign-in required."""

from __future__ import annotations

import json
import threading
from dataclasses import replace
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


def _client_rate(tmp_path: Path, user: Any, rate: int) -> TestClient:
    cfg = PartnerApiConfig(rate_limit_per_minute=rate, max_concurrent=1, trust_proxy_header=False)
    app = FastAPI()
    agent = _agent(tmp_path, _proof_script())
    app.include_router(build_partner_router(tmp_path, agent_factory=lambda _r: agent, config=cfg))
    app.dependency_overrides[identity.current_user] = lambda: user
    return TestClient(app)


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
    agent.config = replace(agent.config, house_judge={"base_url": "https://api.runpod.ai/v2/ep123/openai/v1"})
    app.include_router(build_partner_router(tmp_path, agent_factory=lambda _r: agent, config=cfg, judge_health_fetch=fetch))
    app.dependency_overrides[identity.current_user] = lambda: USER
    c = TestClient(app)
    assert c.post(PLAIN, json={"text": TEXT}).status_code == 200 and calls == []  # intake never reads the judge health
    # control: the same app DOES refuse a screening call, so this test would catch intake being gated on the judges
    body = {"facts": ["a fact."], "narrative": "", "contract_ids": ["bns69"]}
    assert c.post("/api/v1/analyse-facts", json=body).json() == {"error": "judges_offline"} and calls


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


def _assist_client(
    tmp_path: Path, *, cfg: Any = None, program: Any = None, user: Any = USER, rate: int | None = None
) -> TestClient:
    c = _client(tmp_path, user=user) if rate is None else _client_rate(tmp_path, user, rate)
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
    assert anon.rt.transport.calls == [] and anon.rt.store.log == []  # type: ignore[attr-defined]


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
    assert body["bundle"]["facts"] and body["bundle"] == plain  # the agent adds nothing: the same deterministic bundle


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


def test_the_intake_message_is_json_of_the_numbered_facts_only() -> None:
    m = AS.intake_user_message({"text": 'a "quoted" line\n\nnext'})
    body = m[len(AS.INTAKE_USER_MESSAGE_PREFIX) :]
    assert m.startswith(AS.INTAKE_USER_MESSAGE_PREFIX) and "contract_ids" not in m
    assert json.loads(body) == {"facts": [{"id": "F1", "text": 'a "quoted" line next'}]}  # facts only, round-tripping as JSON
    assert intake.SCAN_LINE not in m


HEADER_ORDER = (
    "IN THE HIGH COURT OF EXAMPLEPUR\n\nBETWEEN:\n\n"
    "1. ZORVAN QUILLERIE, aged about 49 years, S/o Mr Hendrik Quillerie, resident of 14 Marlowe Lane, Thistleby\n\n"
    "...Appellant (By Sri Oswin Pardue, Advocate)\n\nAND:\n\nThe State of Exampleshire ...Respondent\n\n"
    "CORAM: HON'BLE MR JUSTICE ALDRIC FENWORTH\n\nDATE OF JUDGMENT: 02.01.2023\n\nJUDGMENT\n\n"
    "1. The appellant was convicted of cheating after the complainant paid a deposit for a flat on 3 March.\n\n"
    "2. The complainant says the flat was never handed over and the deposit was not refunded to him.\n\n"
    "3. The trial court held that the appellant had promised possession by June and had made no arrangement to give it.\n\n"
    "4. The appellant argues that the delay was caused by the builder and that he never meant to deceive anyone at all.\n"
)
HEADER_STRINGS = ["ZORVAN", "Hendrik", "Marlowe Lane", "aged about 49", "Oswin Pardue", "FENWORTH", "02.01.2023"]


def test_header_of_the_order_never_reaches_the_model_only_the_numbered_facts_do(tmp_path: Path) -> None:
    # arm 1: the header details ARE in the request the caller sent, and the plain (no-model) route excludes them from the facts
    assert all(h in HEADER_ORDER for h in HEADER_STRINGS)
    plain = _client(tmp_path / "p").post(PLAIN, json={"text": HEADER_ORDER}).json()["bundle"]
    assert [n["status"] for n in plain["not_citable"] if n["status"] == "caption"] == ["caption"]
    assert not any(h in f["text"] for f in plain["facts"] for h in HEADER_STRINGS) and len(plain["facts"]) == 4
    # arm 2: the assisted route: all that is posted to the model (first message, tool results) has the facts, not the header
    c = _assist_client(tmp_path, program=_program(HEADER_ORDER))
    r = c.post(ASSIST, json={"text": HEADER_ORDER, "consent": True})
    assert r.status_code == 200 and r.json()["assistant"]["used"] is True
    posted = json.dumps(c.rt.transport.posted, ensure_ascii=False)  # type: ignore[attr-defined]
    assert "The appellant was convicted of cheating" in posted and "flat was never handed over" in posted  # the facts are there
    for h in HEADER_STRINGS:
        assert h not in posted, h  # the header details are not


def test_the_message_builder_alone_excludes_the_header_and_a_text_with_no_header_is_unchanged() -> None:
    m = AS.intake_user_message({"text": HEADER_ORDER})
    assert not any(h in m for h in HEADER_STRINGS) and "The appellant was convicted" in m
    plain = "1. First fact is here and it is long enough.\n\n2. Second fact is here and it is also long enough.\n"
    assert [
        f["text"] for f in json.loads(AS.intake_user_message({"text": plain})[len(AS.INTAKE_USER_MESSAGE_PREFIX) :])["facts"]
    ] == [
        "1. First fact is here and it is long enough.",
        "2. Second fact is here and it is also long enough.",
    ]


# ---- the pre-check's findings --------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("consent", ["1", "yes", "true", 1, 0, "True"])
def test_consent_must_be_the_json_boolean_true_nothing_coerces(tmp_path: Path, consent: Any) -> None:
    c = _assist_client(tmp_path)
    r = c.post(ASSIST, json={"text": TEXT, "consent": consent})
    assert r.status_code == 422 and c.rt.transport.calls == [] and c.rt.store.log == []  # type: ignore[attr-defined]


def test_a_blank_text_is_refused_before_anything_runs_on_both_routes(tmp_path: Path) -> None:
    c = _assist_client(tmp_path)
    for path in (PLAIN, ASSIST):
        assert c.post(path, json={"text": " \n\t ", "consent": True}).status_code == 422
    assert c.rt.transport.calls == [] and c.rt.store.log == []  # type: ignore[attr-defined]


def _garbage_index(tmp_path: Path) -> Path:
    p = tmp_path / "bad.sqlite3"
    p.write_bytes(b"this is not a sqlite database" * 50)
    return p


def test_a_failing_citation_lookup_is_check_unavailable_never_a_guess_or_a_500(tmp_path: Path) -> None:
    r = _client(tmp_path, index=_garbage_index(tmp_path)).post(PLAIN, json={"text": TEXT})
    assert r.status_code == 200
    cits = r.json()["bundle"]["citations"]
    assert cits and all(c["verifier_status"] == "CHECK_UNAVAILABLE" for c in cits)


def test_citation_lookups_per_request_are_bounded(tmp_path: Path) -> None:
    from pravrudhi.api.partner import INTAKE_MAX_CITATION_LOOKUPS

    many = "\n\n".join(f"{i}. See (1977) 3 SCC {200 + i} for the rule." for i in range(INTAKE_MAX_CITATION_LOOKUPS + 10))
    cits = _client(tmp_path, index=_index(tmp_path)).post(PLAIN, json={"text": many}).json()["bundle"]["citations"]
    statuses = [c["verifier_status"] for c in cits]
    assert len(statuses) > INTAKE_MAX_CITATION_LOOKUPS
    assert statuses.count("NOT_CHECKED") == len(statuses) - INTAKE_MAX_CITATION_LOOKUPS
    assert all(st != "NOT_CHECKED" for st in statuses[:INTAKE_MAX_CITATION_LOOKUPS])


def test_intake_takes_an_agent_slot_so_a_burst_cannot_starve_the_service(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    started, release = threading.Event(), threading.Event()
    real = intake.build_bundle

    def slow(raw: str, **kw: Any) -> Any:
        started.set()
        release.wait(10)
        return real(raw, **kw)

    monkeypatch.setattr(intake, "build_bundle", slow)
    c = _client(tmp_path)  # max_concurrent 1
    t = threading.Thread(target=lambda: c.post(PLAIN, json={"text": TEXT}), daemon=True)
    t.start()
    assert started.wait(10)
    r = c.post(PLAIN, json={"text": TEXT})
    assert r.status_code == 503 and r.json()["error"] == "agent_at_capacity"
    release.set()
    t.join(10)
    assert c.post(PLAIN, json={"text": TEXT}).status_code == 200  # the slot was released


def _keyed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[TestClient, str]:
    from tests.test_partner_key_metering import ADMIN, _key

    monkeypatch.setenv("PRAVRUDHI_ADMINS", ADMIN.id)
    c = _client(tmp_path, user=ADMIN)
    return c, _key(c, "acme")


def test_a_keyed_intake_call_leaves_an_audit_row_without_the_text(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from pravrudhi.application import tenancy

    c, secret = _keyed(monkeypatch, tmp_path)
    h = {tenancy.API_KEY_HEADER: secret}
    assert c.post(PLAIN, json={"text": TEXT}, headers=h).status_code == 200
    rows = c.get("/api/v1/audit", headers=h).json()["rows"]
    mine = [r for r in rows if r.get("mode") == "intake"]
    assert len(mine) == 1 and mine[0]["status_code"] == 200 and mine[0]["run_id"].startswith("nyaya-intake-")
    assert "complainant" not in json.dumps(rows)  # never the pasted text


def test_the_assisted_route_audits_the_call_and_is_rate_limited(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from pravrudhi.application import tenancy
    from tests.test_partner_key_metering import ADMIN, _key

    monkeypatch.setenv("PRAVRUDHI_ADMINS", ADMIN.id)
    c = _assist_client(tmp_path, user=ADMIN, rate=1000)
    secret = _key(c, "acme")
    h = {tenancy.API_KEY_HEADER: secret}
    r = c.post(ASSIST, json={"text": TEXT, "consent": True}, headers=h)
    assert r.status_code == 200 and r.json()["assistant"]["used"] is True
    rows = c.get("/api/v1/audit", headers=h).json()["rows"]
    mine = [row for row in rows if row["mode"] == "intake_assist"]
    assert len(mine) == 1 and mine[0]["status_code"] == 200 and mine[0]["run_id"].startswith("nyaya-intake-")
    assert "complainant" not in json.dumps(rows)
    limited = _assist_client(tmp_path / "lim", rate=1)
    codes = [limited.post(ASSIST, json={"text": TEXT, "consent": True}).status_code for _ in range(2)]
    assert codes[1] == 429


def test_a_refusal_by_the_tools_is_a_422_and_is_audited(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from pravrudhi.application import tenancy

    def refuse(raw: str, **kw: Any) -> Any:
        raise intake.IntakeRefused("more fact candidates than the tools accept")

    monkeypatch.setattr(intake, "build_bundle", refuse)
    c, secret = _keyed(monkeypatch, tmp_path)
    h = {tenancy.API_KEY_HEADER: secret}
    r = c.post(PLAIN, json={"text": TEXT}, headers=h)
    assert r.status_code == 422 and "candidates" in r.json()["detail"]
    rows = c.get("/api/v1/audit", headers=h).json()["rows"]
    assert [row["status_code"] for row in rows if row["mode"] == "intake"] == [422]


def test_an_unexpected_failure_is_a_500_and_leaves_an_audit_row(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from pravrudhi.application import tenancy

    def boom(raw: str, **kw: Any) -> Any:
        raise RuntimeError("tool bug")

    monkeypatch.setattr(intake, "build_bundle", boom)
    c, secret = _keyed(monkeypatch, tmp_path)
    h = {tenancy.API_KEY_HEADER: secret}
    c.raise_server_exceptions = False  # type: ignore[attr-defined]
    client = TestClient(c.app, raise_server_exceptions=False)
    assert client.post(PLAIN, json={"text": TEXT}, headers=h).status_code == 500
    rows = client.get("/api/v1/audit", headers=h).json()["rows"]
    assert [row["status_code"] for row in rows if row["mode"] == "intake"] == [500]


def test_a_repeated_citation_is_answered_once_and_consistently(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import pravrudhi.api.partner as P

    calls: list[str] = []
    real = P.VerifyCitationRequest

    def counting(**kw: Any) -> Any:
        calls.append(kw["citation"])
        return real(**kw)

    monkeypatch.setattr(P, "VerifyCitationRequest", counting)
    text = "\n\n".join(["1. See (1977) 3 SCC 247 and again (1977) 3 SCC 247 for the rule."] * 3)
    r = _client(tmp_path, index=_index(tmp_path)).post(PLAIN, json={"text": text})
    cits = r.json()["bundle"]["citations"]
    assert len(cits) >= 3 and {c["verifier_status"] for c in cits} == {"IN_INDEX"}
    assert len(calls) == 1  # one lookup for the same citation text, however often it appears


def test_the_total_lookup_time_is_bounded(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import pravrudhi.api.partner as P

    monkeypatch.setattr(P, "INTAKE_LOOKUP_BUDGET_S", -1.0)  # already spent: every citation is NOT_CHECKED, none is looked up
    r = _client(tmp_path, index=_index(tmp_path)).post(PLAIN, json={"text": TEXT})
    cits = r.json()["bundle"]["citations"]
    assert cits and all(c["verifier_status"] == "NOT_CHECKED" for c in cits)
