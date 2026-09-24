"""The partner API's first endpoint: POST /api/v1/analyse-facts, wrapping application.nyaya_agent.NyayaAgent
over real FastAPI routing (house rule: no stub scorer as evidence -- but the AGENT here is exercised with the
same scripted Judge/Registry test doubles `test_nyaya_agent.py` itself uses, since this test file's job is the
HTTP wiring -- request parsing, response shape, error mapping -- not re-proving the agent loop's own decision
logic, which is already covered there against the real pinned Lean binary).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from pravrudhi.api.partner import build_partner_router
from pravrudhi.application import nyaya_lean_registry as reg
from pravrudhi.application.nyaya_agent import AgentConfig, NyayaAgent
from pravrudhi.application.nyaya_judges import ElementJudgment, JudgeRequest

TOY_FACTS = [
    "TOY: Kiran was engaged to Lata and told her he would marry her in the spring.",
    "TOY: Kiran had already decided never to marry Lata when he made that promise.",
    "TOY: Relying on the promise, Lata had sexual intercourse with Kiran.",
]
BNS69_EL = [
    "induces the woman by deceitful means",
    "sexual intercourse with the woman actually occurs",
]
BNS69_DENY = "the sexual intercourse amounts to the offence of rape"


def _est(fid: str, quote: str, p: float = 0.97) -> ElementJudgment:
    return ElementJudgment("established", p, fid, quote, "model")


def _not(p: float = 0.03) -> ElementJudgment:
    return ElementJudgment("not_established", p)


class ScriptedJudge:
    def __init__(self, script: dict[str, list[ElementJudgment]]) -> None:
        self.script = script

    def judge(self, request: JudgeRequest) -> ElementJudgment:
        queue = self.script[request.element]
        return queue.pop(0) if len(queue) > 1 else queue[0]

    name = "scripted-test-judge"


class ScriptedRegistry:
    sha256 = "scripted-test-registry"

    def __init__(self) -> None:
        self.contracts = {"bns69": reg.DescribedContract("bns69", list(BNS69_EL), [BNS69_DENY])}
        self.sources = {"bns69": ["Bharatiya Nyaya Sanhita §69"]}

    def list_contracts(self) -> dict[str, list[str]]:
        return dict(self.sources)

    def describe(self, contract_id: str) -> reg.DescribedContract:
        return self.contracts[contract_id]

    def source_text(self, contract_id: str) -> str:
        return f"RETRIEVED statute text for {contract_id}"

    def check(self, assertions: dict, contract_id: str) -> dict[str, Any]:
        c = self.contracts[contract_id]
        met = {k for k, v in assertions.items() if v}
        denied = [e for e in c.denials if e in met]
        omitted = [e for e in c.elements if e not in met]
        flagged = denied or omitted
        return {
            "verdict": "flagged" if flagged else "grounded",
            "denied_claims": denied,
            "unlicensed_claims": [],
            "omitted_claims": omitted,
        }


def _proof_script() -> dict[str, list[ElementJudgment]]:
    return {
        BNS69_EL[0]: [_est("F2", "never to marry Lata")],
        BNS69_EL[1]: [_est("F3", "Lata had sexual intercourse with Kiran")],
        BNS69_DENY: [_not()],
    }


def _agent(tmp_path: Path, script: dict[str, list[ElementJudgment]]) -> NyayaAgent:
    config = AgentConfig(
        tau=0.74,
        refer_band=(0.5, 0.74),
        max_retries=2,
        audit_dir=tmp_path / "audit",
        judge_statute_text={"bns69": "TRAINING statute text for bns69"},
    )
    return NyayaAgent(ScriptedJudge(script), ScriptedRegistry(), config)


def _client(tmp_path: Path, script: dict[str, list[ElementJudgment]] | None = None) -> TestClient:
    agent = _agent(tmp_path, script if script is not None else _proof_script())
    app = FastAPI()
    app.include_router(build_partner_router(tmp_path, agent_factory=lambda _root: agent))
    return TestClient(app)


def test_proof_response_shape_and_status(tmp_path: Path) -> None:
    c = _client(tmp_path)
    resp = c.post(
        "/api/v1/analyse-facts",
        json={"facts": TOY_FACTS, "narrative": "TOY narrative.", "contract_ids": ["bns69"]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["judge"] == "scripted-test-judge"
    assert len(body["contracts"]) == 1
    contract = body["contracts"][0]
    assert contract["contract_id"] == "bns69"
    assert contract["outcome"] == "PROOF"
    assert contract["reason"] == "all_elements_established"


def test_quote_source_is_visible_per_element(tmp_path: Path) -> None:
    # Lead-2-assistant's explicit requirement: whole-fact claims need to be visible to the user, not just
    # the final verdict -- quote_source must survive the HTTP boundary, per element.
    c = _client(tmp_path)
    resp = c.post(
        "/api/v1/analyse-facts",
        json={"facts": TOY_FACTS, "narrative": "TOY narrative.", "contract_ids": ["bns69"]},
    )
    body = resp.json()
    elements = body["contracts"][0]["elements"]
    established = [e for e in elements if e["status"] == "established"]
    assert established, "expected at least one established element in the PROOF fixture"
    for e in established:
        assert e["quote_source"] == "model"
        assert e["quote"] is not None
        assert e["fact_id"] is not None


def _unreachable_factory(_root: Path) -> NyayaAgent:
    raise AssertionError("the agent factory must not be called for a request that fails validation first")


def test_empty_facts_is_422() -> None:
    app = FastAPI()
    app.include_router(build_partner_router(Path("."), agent_factory=_unreachable_factory))
    c = TestClient(app)
    resp = c.post("/api/v1/analyse-facts", json={"facts": []})
    assert resp.status_code == 422


def test_denial_outcome(tmp_path: Path) -> None:
    script = {
        BNS69_EL[0]: [_est("F2", "never to marry Lata")],
        BNS69_EL[1]: [_est("F3", "Lata had sexual intercourse with Kiran")],
        BNS69_DENY: [_est("F3", "Lata had sexual intercourse with Kiran")],
    }
    c = _client(tmp_path, script)
    resp = c.post(
        "/api/v1/analyse-facts",
        json={"facts": TOY_FACTS, "narrative": "TOY narrative.", "contract_ids": ["bns69"]},
    )
    body = resp.json()
    assert body["contracts"][0]["outcome"] == "DENIAL"


def test_unknown_contract_id_is_422(tmp_path: Path) -> None:
    c = _client(tmp_path)
    resp = c.post(
        "/api/v1/analyse-facts",
        json={"facts": TOY_FACTS, "contract_ids": ["not_a_real_contract"]},
    )
    assert resp.status_code == 422


def test_audit_path_is_included_in_the_response(tmp_path: Path) -> None:
    c = _client(tmp_path)
    resp = c.post(
        "/api/v1/analyse-facts",
        json={"facts": TOY_FACTS, "narrative": "TOY narrative.", "contract_ids": ["bns69"]},
    )
    body = resp.json()
    assert body["audit_path"]
    assert Path(body["audit_path"]).exists()
