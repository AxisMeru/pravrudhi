"""The optional `accused` field on POST /api/v1/analyse-facts (accused-attribution D0, additive): it reaches the agent only when
stated, is validated, and with the check on turns a co-accused quote into REFER. All facts and judges are constructed."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from pravrudhi.api.partner import PartnerApiConfig, build_partner_router
from pravrudhi.application.nyaya_agent import NyayaAgent
from pravrudhi.application.nyaya_attribution import AccusedAttributionJudge, AccusedRef
from tests.test_nyaya_accused_attribution import EL1, EL2, EL3, _cfg, _est, _on_cfg, _registry
from tests.test_nyaya_agent import ScriptedJudge

CFG = PartnerApiConfig(rate_limit_per_minute=1000, max_concurrent=100, trust_proxy_header=False)
FACTS = ["TOY: Dev is the husband of Nila.", "TOY: Accused No.1 beat Nila on several evenings.", "TOY: Another unrelated fact."]
BODY: dict[str, Any] = {"facts": FACTS, "narrative": "TOY.", "contract_ids": ["bns85"]}


def _client(tmp_path: Path, *, on: bool = True) -> tuple[TestClient, NyayaAgent, list[dict[str, Any]]]:
    quote = "Accused No.1 beat Nila"
    inner = ScriptedJudge({EL1: [_est("F1", "the husband")], EL2: [_est("F2", quote)], EL3: [_est("F2", quote)]})
    agent = NyayaAgent(AccusedAttributionJudge(inner), _registry(), _on_cfg(tmp_path))
    if not on:
        agent = NyayaAgent(inner, _registry(), _cfg(tmp_path))
    seen: list[dict[str, Any]] = []
    real_run = agent.run

    def spy(*a: Any, **k: Any) -> Any:
        seen.append(k)
        return real_run(*a, **k)

    agent.run = spy  # type: ignore[method-assign]
    app = FastAPI()
    app.include_router(build_partner_router(tmp_path, agent_factory=lambda _root: agent, config=CFG))
    return TestClient(app), agent, seen


def _contract(r: Any) -> dict[str, Any]:
    assert r.status_code == 200, r.text
    return r.json()["contracts"][0]


def test_the_accused_field_reaches_the_agent_as_an_accused_ref(tmp_path: Path) -> None:
    client, _agent, seen = _client(tmp_path)
    body = {**BODY, "accused": {"id": "p2", "aliases": ["Accused No.2", "A2"], "other_parties": [["Accused No.1", "A1"]]}}
    _contract(client.post("/api/v1/analyse-facts", json=body))
    ref = seen[0]["accused"]
    assert ref == AccusedRef("p2", ("Accused No.2", "A2"), (("Accused No.1", "A1"),))


def test_an_absent_accused_is_not_passed_at_all(tmp_path: Path) -> None:
    client, _agent, seen = _client(tmp_path)
    client.post("/api/v1/analyse-facts", json=BODY)
    assert "accused" not in seen[0]


def test_with_the_check_on_a_co_accused_quote_refers_and_the_named_accused_matters(tmp_path: Path) -> None:
    client, _agent, _seen = _client(tmp_path)
    other = _contract(client.post("/api/v1/analyse-facts", json={**BODY, "accused": {"id": "p2", "aliases": ["Accused No.2"]}}))
    assert (other["outcome"], other["reason"]) == ("REFER_TO_LAWYER", "accused_attribution_not_matched")
    same = _contract(client.post("/api/v1/analyse-facts", json={**BODY, "accused": {"id": "p1", "aliases": ["Accused No.1"]}}))
    assert (same["outcome"], same["reason"]) == ("PROOF", "all_elements_established")


def test_with_the_check_on_an_absent_accused_refers_with_accused_not_specified(tmp_path: Path) -> None:
    client, _agent, _seen = _client(tmp_path)
    c = _contract(client.post("/api/v1/analyse-facts", json=BODY))
    assert (c["outcome"], c["reason"]) == ("REFER_TO_LAWYER", "accused_not_specified")


def test_with_the_check_off_the_accused_field_changes_nothing(tmp_path: Path) -> None:
    client, _agent, _seen = _client(tmp_path, on=False)
    plain = _contract(client.post("/api/v1/analyse-facts", json=BODY))
    named = _contract(client.post("/api/v1/analyse-facts", json={**BODY, "accused": {"id": "p2", "aliases": ["Accused No.2"]}}))
    assert plain == named and plain["outcome"] == "PROOF"


@pytest.mark.parametrize(
    "accused",
    [
        {"id": "", "aliases": ["A1"]},
        {"id": "x", "aliases": []},
        {"id": "x", "aliases": [" "]},
        {"id": "x", "aliases": ["a" * 81]},
        {"id": "x", "aliases": ["A1"] * 21},
        {"id": "x", "aliases": ["A1"], "other_parties": [[]]},
        {"id": "x", "aliases": ["A1"], "other_parties": [[""]]},
        {"aliases": ["A1"]},
        {"id": "x", "aliases": "A1"},
    ],
)
def test_a_malformed_accused_is_a_422_before_any_judge_call(tmp_path: Path, accused: dict[str, Any]) -> None:
    client, _agent, seen = _client(tmp_path)
    r = client.post("/api/v1/analyse-facts", json={**BODY, "accused": accused})
    assert r.status_code == 422, r.text
    assert seen == []


def test_the_new_reason_values_are_in_the_published_contract_and_described_as_additive() -> None:
    import json

    root = Path(__file__).resolve().parent.parent
    spec = json.loads((root / "docs" / "api" / "openapi-v1.json").read_text())
    reasons = (
        spec["components"]["schemas"]["ContractReason"]["enum"] if "ContractReason" in spec["components"]["schemas"] else None
    )
    new = {
        "accused_not_specified",
        "accused_attribution_unresolved",
        "accused_attribution_not_matched",
        "accused_attribution_collective",
        "accused_attribution_config_unmatched",
    }
    contract = spec["components"]["schemas"]["ContractResultOut"]["properties"]["reason"]
    text = json.dumps(contract)
    assert all(v in text for v in new) and "Additive" in text and (reasons is None or new <= set(reasons))
    assert "accused" in spec["components"]["schemas"]["AnalyseFactsRequest"]["properties"]
