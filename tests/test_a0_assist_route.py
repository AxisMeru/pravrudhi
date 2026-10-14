"""POST /v1/analyse-facts/assist over real FastAPI routing: dark by default, consent, sign-in, tier, core fallback."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from pravrudhi.api import identity
from pravrudhi.api.partner import PartnerApiConfig, build_partner_router
from pravrudhi.application import assistant_tier as TIER
from pravrudhi.application import nyaya_agent as agent_mod
from pravrudhi.application.a0_assist import limits as L
from pravrudhi.application.a0_assist import runtime as RT
from tests import test_a0_assist_request as RQ
from tests import test_nyaya_agent as T

REPO = Path(__file__).resolve().parent.parent

#: The shipped file turns the route on (0.6.5); these tests start from its dark form.
DARK = {"assist_route_enabled": False, "agent_id": None, "agent_version": None, "environment_id": None}
FACTS = list(T.TOY_FACTS)
BODY = {"facts": FACTS, "narrative": "", "contract_ids": ["bns69"], "consent": True, "session_id": "client-xyz"}
USER = identity.User("u1", "user@example.test", "user")
URL = "/api/v1/analyse-facts/assist"
CFG = PartnerApiConfig(rate_limit_per_minute=1000, max_concurrent=1, trust_proxy_header=False)


def make_runtime(tmp_path, **tr_kw):
    cfg = RQ.make_cfg()
    clock = RQ.Clock()
    rt = RT.Runtime(cfg)
    rt.store = RQ.FakeStore()
    rt.transport = RQ.FakeTransport(RQ.ideal_ref(), clock, **tr_kw)
    rt.limiter = L.AssistLimiter(cfg.runs_per_user_per_day, cfg.runs_per_org_per_day, cfg.per_ip_per_minute)
    rt.slots = threading.BoundedSemaphore(cfg.max_concurrent_sessions)
    return rt


def client(tmp_path, *, user=USER, runtime="enabled", **tr_kw):
    (tmp_path / "configs").mkdir(parents=True, exist_ok=True)
    shipped = yaml.safe_load((REPO / "configs" / "assistant_tier.yaml").read_text())
    shipped.update(DARK)  # the shipped file is enabled (0.6.5); the tests set what they need
    (tmp_path / "configs" / "assistant_tier.yaml").write_text(yaml.safe_dump(shipped))
    agent = agent_mod.NyayaAgent(T.ScriptedJudge(T._proof_script(FACTS)), T._registry(), T._config(tmp_path))
    app = FastAPI()
    app.include_router(build_partner_router(tmp_path, agent_factory=lambda _root: agent, config=CFG))
    app.dependency_overrides[identity.current_user] = lambda: user
    RT.reset()
    if runtime == "enabled":
        RT._runtime = make_runtime(tmp_path, **tr_kw)
    elif runtime == "dark":
        cfg_raw = RQ.make_cfg(assist_route_enabled=False)
        RT._runtime = RT.Runtime(cfg_raw)
    c = TestClient(app)
    c.rt = RT._runtime
    return c


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-real")
    TIER.reset()
    yield
    RT.reset()
    TIER.reset()


def test_the_route_is_404_when_the_runtime_is_absent_or_dark(tmp_path):
    assert client(tmp_path, runtime="none").post(URL, json=BODY).status_code == 404
    c = client(tmp_path / "d", runtime="dark")
    assert c.post(URL, json=BODY).status_code == 404 and c.rt.transport is None


def test_consent_absent_is_400_consent_required_with_no_call_and_no_reservation(tmp_path):
    c = client(tmp_path)
    for body in ({**BODY, "consent": False}, {k: v for k, v in BODY.items() if k != "consent"}):
        r = c.post(URL, json=body)
        assert r.status_code == 400 and r.json() == {"error": "consent_required"}
    assert c.rt.transport.calls == [] and c.rt.store.log == []


def test_sections_are_refused_the_route_selects_by_contract_ids(tmp_path):
    c = client(tmp_path)
    assert c.post(URL, json={**BODY, "sections": ["x"]}).status_code == 422 and c.rt.transport.calls == []


def test_a_signed_in_consenting_caller_gets_the_decide_results_and_an_assistant_block(tmp_path):
    c = client(tmp_path)
    r = c.post(URL, json=BODY)
    assert r.status_code == 200, r.text
    out = r.json()
    a = out["assistant"]
    assert a["used"] is True and a["outcome"] == "finished" and a["session_id"] == "client-xyz"
    assert [s["name"] for s in a["steps"]][-1] == "decide"
    assert out["contracts"][0]["outcome"] == "PROOF" and out["run_id"].startswith("nyaya-assist-")
    assert out["contract_set_version"] is not None or "contract_set_version" in out
    assert "rule_text" not in out["contracts"][0]


def test_the_assisted_body_has_the_same_top_level_keys_as_the_core_body_plus_assistant(tmp_path):
    c = client(tmp_path)
    assisted = c.post(URL, json=BODY).json()
    core = c.post("/api/v1/analyse-facts", json={k: v for k, v in BODY.items() if k not in ("consent", "session_id")}).json()
    # no retention notice on this route (see below); a finished assisted run also carries `appendix` (#416)
    assert set(assisted) - {"assistant", "appendix"} == set(core) - {"retention_notice"}


def test_an_anonymous_caller_gets_the_core_result_with_sign_in_required(tmp_path):
    c = client(tmp_path, user=None)
    r = c.post(URL, json=BODY)
    assert (
        r.status_code == 200 and r.json()["assistant"]["reason"] == "sign_in_required" and r.json()["assistant"]["used"] is False
    )
    assert c.rt.transport.calls == [] and c.rt.store.log == []


def test_the_core_fallback_does_not_deadlock_on_the_single_concurrency_slot(tmp_path):
    c = client(tmp_path, user=None)  # max_concurrent is 1: the fallback runs the core path inside the held slot
    for _ in range(3):
        assert c.post(URL, json=BODY).status_code == 200


def test_the_concurrency_slot_is_released_after_an_assisted_run(tmp_path):
    c = client(tmp_path)
    assert c.post(URL, json=BODY).status_code == 200
    assert c.post(URL, json=BODY).status_code == 200


def test_an_unavailable_tier_gives_the_core_result_and_no_anthropic_call(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    c = client(tmp_path)
    r = c.post(URL, json=BODY)
    assert r.json()["assistant"]["reason"] == "not_configured" and c.rt.transport.calls == [] and c.rt.store.log == []


def test_a_pause_source_code_pauses_the_route_with_the_paused_reason(tmp_path):
    c = client(tmp_path)
    TIER.set_pause_source(lambda: "canary_failed")
    r = c.post(URL, json=BODY)
    assert r.json()["assistant"]["reason"] == "paused" and c.rt.transport.calls == []


def test_a_validation_error_in_the_body_is_422_and_touches_nothing(tmp_path):
    c = client(tmp_path)
    assert c.post(URL, json={**BODY, "facts": []}).status_code == 422 and c.rt.transport.calls == []


def test_the_assist_route_is_not_in_the_public_openapi_contract(tmp_path):
    c = client(tmp_path)
    assert (
        "/api/v1/analyse-facts/assist" not in c.get("/openapi.json").json()["paths"]
        if c.get("/openapi.json").status_code == 200
        else True
    )


def test_no_retention_notice_on_any_response_of_the_assist_route_finished_or_fallback(tmp_path):
    c = client(tmp_path)
    finished = c.post(URL, json=BODY).json()
    assert finished["assistant"]["used"] is True and "retention_notice" not in finished
    c2 = client(tmp_path / "b", user=None)
    fallback = c2.post(URL, json=BODY).json()
    assert fallback["assistant"]["used"] is False and "retention_notice" not in fallback
    core = c2.post("/api/v1/analyse-facts", json={k: v for k, v in BODY.items() if k not in ("consent", "session_id")}).json()
    assert "retention_notice" in core  # the ordinary route is unchanged


# ---- the workflow field (workflow hook) ----------------------------------------------------------------------------------------
def test_workflow_defaults_to_screening_and_an_unknown_value_is_422(tmp_path):
    c = client(tmp_path)
    assert c.post(URL, json=BODY).status_code == 200  # no workflow field: screening, as before
    assert c.post(URL, json={**BODY, "workflow": "screening"}).status_code == 200
    r = c.post(URL, json={**BODY, "workflow": "nonsense"})
    assert r.status_code == 422 and c.rt.transport.calls.count("create_session") == 2  # the two good runs only


def test_a_workflow_with_no_agent_answers_with_the_ordinary_result_and_not_configured(tmp_path):
    c = client(tmp_path)
    before = list(c.rt.transport.calls)
    r = c.post(URL, json={**BODY, "workflow": "hearing_prep"})
    assert r.status_code == 200
    a = r.json()["assistant"]
    assert a["used"] is False and a["reason"] == "not_configured" and r.json()["contracts"]
    assert c.rt.transport.calls == before and c.rt.store.log == []


def test_a_copy_style_agent_is_refused_and_the_caller_gets_the_core_result(tmp_path):
    """The pinned product agent speaks by reference; a copy-style session (the old hand-over) is refused at its first judge call,
    the run is incomplete, and the caller still gets the ordinary result with `used: false` (never an error, never a partial)."""
    c = client(tmp_path)
    c.rt.transport.program = RQ.ideal()  # copy style
    r = c.post(URL, json=BODY)
    assert r.status_code == 200 and r.json()["assistant"]["used"] is False and r.json()["contracts"]
    assert "delete_session" in c.rt.transport.calls
