"""POST /v1/analyse-facts/assist with workflow=hearing_prep over real FastAPI routing (#415): the pack comes from the engine's
own decide results, on the assisted path AND on every fallback path; the A0 rules (consent, sign-in, workflow agent) are unchanged."""

from __future__ import annotations

import json
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
from pravrudhi.application.hearing_pack.pack import build_pack
from tests import test_a0_assist_request as RQ
from tests import test_nyaya_agent as T

REPO = Path(__file__).resolve().parent.parent
FACTS = list(T.TOY_FACTS)
URL = "/api/v1/analyse-facts/assist"
BODY = {"facts": FACTS, "narrative": "", "contract_ids": ["bns69"], "consent": True, "workflow": "hearing_prep"}
USER = identity.User("u1", "user@example.test", "user")
HP_AGENT = {"agent_id": "agent_hp", "agent_version": 3, "environment_id": "env_hp"}
CFG = PartnerApiConfig(rate_limit_per_minute=1000, max_concurrent=1, trust_proxy_header=False)


def pack_program(*, call_pack=True, pack_input=None):
    """The RQ.ideal screening program, then (optionally) the `pack` tool call, then the end of the turn."""

    def program(tr):
        i = 0
        ing = yield RQ.tool_use(i := i + 1, "ingest", {"facts": list(FACTS), "narrative": ""})
        yield RQ.tool_use(i := i + 1, "select_contracts", {"contract_ids": [RQ.CID]})
        desc = yield RQ.tool_use(i := i + 1, "describe_contract", {"contract_id": RQ.CID})
        outs = []
        for e in desc["elements"]:
            inp = {
                "contract_id": RQ.CID,
                "element": e["element_id"],
                "is_denial": e["is_denial"],
                "facts": ing["facts"],
                "narrative": "",
            }
            outs.append((yield RQ.tool_use(i := i + 1, "judge_element", inp)))
        for o in outs:
            if o.get("quote"):
                yield RQ.tool_use(
                    i := i + 1, "quote_check", {"facts": ing["facts"], "fact_id": o["fact_id"], "quote": o["quote"]}
                )
        asm = yield RQ.tool_use(i := i + 1, "assemble", {"contract_id": RQ.CID, "element_results": outs})
        lc = yield RQ.tool_use(i := i + 1, "lean_check", {"wire": asm["wire"], "contract_id": RQ.CID})
        yield RQ.tool_use(
            i := i + 1, "decide", {"contract_id": RQ.CID, "element_results": outs, "lean_outcome": lc["lean_outcome"]}
        )
        if call_pack:
            yield RQ.tool_use(i := i + 1, "pack", pack_input or {"contract_ids": [RQ.CID]})
        yield {"id": "z", "type": "session.status_idle", "stop_reason": {"type": "end_turn"}}

    return program


class SpyTransport(RQ.FakeTransport):
    def create_session(self, agent_id, version, env, budget):
        self.created_with = (agent_id, version, env)
        return super().create_session(agent_id, version, env, budget)


def client(tmp_path, program, *, workflows=None, user=USER):
    (tmp_path / "configs").mkdir(parents=True, exist_ok=True)
    shipped = yaml.safe_load((REPO / "configs" / "assistant_tier.yaml").read_text())
    (tmp_path / "configs" / "assistant_tier.yaml").write_text(yaml.safe_dump(shipped))
    agent = agent_mod.NyayaAgent(T.ScriptedJudge(T._proof_script(FACTS)), T._registry(), T._config(tmp_path))
    app = FastAPI()
    app.include_router(build_partner_router(tmp_path, agent_factory=lambda _root: agent, config=CFG))
    app.dependency_overrides[identity.current_user] = lambda: user
    RT.reset()
    cfg = RQ.make_cfg(workflows={"hearing_prep": HP_AGENT} if workflows is None else workflows)
    rt = RT.Runtime(cfg)
    rt.store = RQ.FakeStore()
    rt.transport = SpyTransport(program, RQ.Clock())
    rt.limiter = L.AssistLimiter(cfg.runs_per_user_per_day, cfg.runs_per_org_per_day, cfg.per_ip_per_minute)
    rt.slots = threading.BoundedSemaphore(cfg.max_concurrent_sessions)
    RT._runtime = rt
    c = TestClient(app)
    c.rt = rt
    return c


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-real")
    TIER.reset()
    yield
    RT.reset()
    TIER.reset()


def canon(x):
    return json.dumps(x, sort_keys=True)


def expected_pack(out):
    facts = [{"id": f"F{i + 1}", "text": t.strip()} for i, t in enumerate(FACTS)]
    return build_pack(out["contracts"], facts, "")


def test_a_finished_run_returns_the_packs_own_output_on_the_workflows_agent(tmp_path):
    c = client(tmp_path, pack_program())
    r = c.post(URL, json=BODY)
    assert r.status_code == 200, r.text
    out = r.json()
    a = out["assistant"]
    assert a["used"] is True and a["outcome"] == "finished" and [s["name"] for s in a["steps"]][-1] == "pack"
    assert c.rt.transport.created_with == ("agent_hp", 3, "env_hp")
    assert canon(out["pack"]) == canon(expected_pack(out))
    assert out["pack"]["contracts"][0]["contract_id"] == "bns69" and out["contracts"][0]["outcome"] == "PROOF"


def test_the_screening_workflow_has_no_pack_key(tmp_path):
    c = client(tmp_path, RQ.ideal())
    body = {k: v for k, v in BODY.items() if k != "workflow"}
    out = c.post(URL, json=body).json()
    assert out["assistant"]["used"] is True and "pack" not in out


def test_an_unfinished_run_falls_back_to_the_core_result_plus_the_same_pack(tmp_path):
    c = client(tmp_path, pack_program(call_pack=False))  # the agent never calls pack: the run is incomplete
    out = c.post(URL, json=BODY).json()
    assert out["assistant"]["used"] is False and out["assistant"]["reason"] == "error"
    assert canon(out["pack"]) == canon(expected_pack(out))


def test_a_tampered_pack_call_is_refused_and_the_fallback_pack_is_still_the_engines_own(tmp_path):
    c = client(tmp_path, pack_program(pack_input={"contract_ids": ["not_the_selected_one"]}))
    out = c.post(URL, json=BODY).json()
    assert out["assistant"]["used"] is False and out["assistant"]["reason"] == "safety"
    assert canon(out["pack"]) == canon(expected_pack(out))


def test_a_workflow_with_no_configured_agent_is_the_core_result_plus_the_pack_and_costs_nothing(tmp_path):
    c = client(tmp_path, pack_program(), workflows={})
    out = c.post(URL, json=BODY).json()
    assert out["assistant"]["used"] is False and out["assistant"]["reason"] == "not_configured"
    assert c.rt.transport.calls == [] and c.rt.store.log == []
    assert canon(out["pack"]) == canon(expected_pack(out))


def test_an_anonymous_caller_gets_sign_in_required_and_the_pack(tmp_path):
    c = client(tmp_path, pack_program(), user=None)
    out = c.post(URL, json=BODY).json()
    assert out["assistant"]["reason"] == "sign_in_required" and "pack" in out
    assert c.rt.transport.calls == [] and c.rt.store.log == []


def test_consent_is_still_required_and_an_unknown_workflow_is_422(tmp_path):
    c = client(tmp_path, pack_program())
    r = c.post(URL, json={**BODY, "consent": False})
    assert r.status_code == 400 and r.json() == {"error": "consent_required"} and c.rt.transport.calls == []
    assert c.post(URL, json={**BODY, "workflow": "nonsense"}).status_code == 422


def test_the_pack_shows_no_reason_code_and_no_outcome_word(tmp_path):
    out = client(tmp_path, pack_program()).post(URL, json=BODY).json()
    shown = " ".join(
        v
        for ct in out["pack"]["contracts"]
        for ob in [ct, *ct["open_ingredients"], *ct["defences_cited"], *ct["review_flags"]]
        for k, v in ob.items()
        if k.endswith("_text")
    )
    assert "all_elements_established" not in shown and "missing_element" not in shown
    assert not any(w in json.dumps(out["pack"]) for w in ("PROOF", "DENIAL", "ABSTAIN", "REFER_TO_LAWYER"))


def test_a_ceiling_store_refusal_and_a_failed_session_create_both_return_the_pack(tmp_path):
    c = client(tmp_path, pack_program())
    c.rt.store = RQ.FakeStore(reserve={"ok": False, "reason": "ceiling"})
    out = c.post(URL, json=BODY).json()
    assert out["assistant"]["reason"] == "daily_ceiling" and canon(out["pack"]) == canon(expected_pack(out))
    c2 = client(tmp_path / "b", pack_program())
    c2.rt.transport = SpyTransport(pack_program(), RQ.Clock(), fail_create=RQ.TR.UpstreamError(500, "server_error"))
    out2 = c2.post(URL, json=BODY).json()
    assert out2["assistant"]["used"] is False and canon(out2["pack"]) == canon(expected_pack(out2))


def test_a_paused_tier_still_returns_the_pack(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    TIER.reset()
    out = client(tmp_path, pack_program()).post(URL, json=BODY).json()
    assert out["assistant"]["used"] is False and canon(out["pack"]) == canon(expected_pack(out))
