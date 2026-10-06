"""#145: the OpenAPI contract is checked in and drift-tested; enums are in the schema; every documented example in
docs/api/partner-quickstart.md is executed against the app with a stub judge. Constructed inputs only."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from pravrudhi.api import identity
from pravrudhi.api.identity import User
from pravrudhi.api.partner import PartnerApiConfig, build_partner_router
from pravrudhi.api.partner_openapi import CONTRACT_PATH, render
from pravrudhi.application import tenancy
from pravrudhi.application.nyaya_agent import ContractReason, ElementStatus, Outcome
from pravrudhi.application.service_window import ServiceWindow
from tests.test_api_partner import BNS69_DENY, BNS69_EL, _agent, _est, _not, _proof_script

ROOT = Path(__file__).resolve().parent.parent
QUICKSTART = ROOT / "docs" / "api" / "partner-quickstart.md"
ADMIN = User(id="op-1", email="op@example.com", role="authenticated")


def _literal_values(t: Any) -> set[str]:
    return set(t.__args__)


def test_checked_in_contract_matches_the_code() -> None:
    assert (ROOT / CONTRACT_PATH).read_text() == render(), (
        "docs/api/openapi-v1.json is stale: run `python -m pravrudhi.api.partner_openapi --write`"
    )


def test_status_outcome_and_reason_are_enums_in_the_schema() -> None:
    schemas = json.loads((ROOT / CONTRACT_PATH).read_text())["components"]["schemas"]
    el, ct = schemas["ElementResultOut"]["properties"], schemas["ContractResultOut"]["properties"]
    assert set(el["status"]["enum"]) == _literal_values(ElementStatus)
    assert set(ct["outcome"]["enum"]) == _literal_values(Outcome)
    assert set(ct["reason"]["enum"]) == _literal_values(ContractReason)
    lean = ct["lean_outcome"]["anyOf"][0]
    assert set(lean["enum"]) == _literal_values(Outcome)


def test_every_reason_the_agent_can_finish_with_is_in_the_enum() -> None:
    """Planted drift guard: scan the agent source for `finish(<outcome>, "<reason>"` literals."""
    src = (ROOT / "src/pravrudhi/application/nyaya_agent.py").read_text()
    found = set(re.findall(r'finish\("[A-Z_]+", "([a-z0-9_]+)"', src))
    assert found and found <= _literal_values(ContractReason)


def test_an_unlisted_status_is_refused_by_the_response_model() -> None:
    from pravrudhi.api.partner import ElementResultOut

    base = {"element": "e", "is_denial": False, "status": "established", "claimed": True, "p_established": 0.9,
            "fact_id": "F1", "quote": "q", "start": 0, "end": 1, "quote_check": "ok", "attempts": 1,
            "occurrences": 1, "offsets_source": "system", "quote_source": "model", "error": None}
    ElementResultOut(**base)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        ElementResultOut(**{**base, "status": "maybe"})  # type: ignore[arg-type]


# -- documented examples ----------------------------------------------------------------------------------

_BLOCK = re.compile(r"```json example:([a-z0-9-]+)\n(.*?)\n```", re.S)


def _examples() -> dict[str, dict[str, Any]]:
    return {name: json.loads(body) for name, body in _BLOCK.findall(QUICKSTART.read_text())}


def _subset(expected: Any, actual: Any, path: str = "") -> None:
    if isinstance(expected, dict):
        assert isinstance(actual, dict), f"{path}: expected an object"
        for k, v in expected.items():
            assert k in actual, f"{path}/{k} missing from the real response"
            _subset(v, actual[k], f"{path}/{k}")
    elif isinstance(expected, list):
        assert isinstance(actual, list) and len(actual) >= len(expected), f"{path}: list shorter than documented"
        for i, v in enumerate(expected):
            _subset(v, actual[i], f"{path}[{i}]")
    else:
        assert expected == actual, f"{path}: documented {expected!r}, real {actual!r}"


def _client(tmp_path: Path, name: str) -> tuple[TestClient, str]:
    script: dict[str, Any] = _proof_script()
    cfg_kw: dict[str, Any] = {"rate_limit_per_minute": 1000, "max_concurrent": 100, "trust_proxy_header": False}
    clock = None
    if name == "judge-unavailable":
        script = {BNS69_EL[0]: [ConnectionError("down")], BNS69_EL[1]: [_est("F3", "Lata had sexual intercourse with Kiran")],
                  BNS69_DENY: [_not()]}
    if name == "rate-limited":
        cfg_kw["rate_limit_per_minute"] = 1
    if name == "outside-window":
        cfg_kw.update(service_window=ServiceWindow(tz="Europe/London", open=_t("09:00"), close=_t("21:00")),
                      service_window_enforce=True)
        clock = lambda: datetime(2026, 10, 2, 22, 0, tzinfo=UTC)  # noqa: E731
    agent = _agent(tmp_path, script)
    app = FastAPI()
    app.include_router(build_partner_router(tmp_path, agent_factory=lambda _r: agent,
                                            config=PartnerApiConfig(**cfg_kw), clock=clock))
    app.dependency_overrides[identity.current_user] = lambda: ADMIN
    c = TestClient(app)
    c.post("/api/v1/orgs", json={"org_id": "acme", "name": "acme"})
    secret = str(c.post("/api/v1/orgs/acme/keys", json={}).json()["secret"])
    return c, secret


def _t(s: str) -> Any:
    from datetime import time

    return time.fromisoformat(s)


def test_there_are_examples_for_the_error_codes_the_quickstart_names() -> None:
    names = set(_examples())
    assert {"analyse-facts-proof", "status", "invalid-key", "rate-limited", "judge-unavailable",
            "outside-window", "empty-fact"} <= names


@pytest.mark.parametrize("name", sorted(_examples()))
def test_every_documented_example_runs_against_the_app(name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PRAVRUDHI_ADMINS", ADMIN.id)
    ex = _examples()[name]
    c, secret = _client(tmp_path, name)
    headers = {k: (secret if v == "$PRAVRUDHI_API_KEY" else v) for k, v in ex.get("headers", {}).items()}
    if "after" in ex:
        c.post(ex["path"], json=ex["request"], headers=headers)
    r = c.request(ex["method"], ex["path"], json=ex.get("request"), headers=headers)
    assert r.status_code == ex["status"], r.text
    _subset(ex["response"], r.json())
    for h in ex.get("headers_out", {}):
        assert h in r.headers


def test_a_wrong_documented_example_is_caught() -> None:
    with pytest.raises(AssertionError):
        _subset({"contracts": [{"outcome": "DENIAL"}]}, {"contracts": [{"outcome": "PROOF"}]})


def test_tenancy_import_is_used() -> None:
    assert tenancy.API_KEY_HEADER.lower() == "x-pravrudhi-api-key"


def test_the_contract_states_the_per_fact_limit_and_the_error_responses_the_docs_list() -> None:
    doc = json.loads((ROOT / CONTRACT_PATH).read_text())
    req = doc["components"]["schemas"]["AnalyseFactsRequest"]["properties"]["facts"]
    assert req["items"]["maxLength"] == 4000 and req["maxItems"] == 8
    paths = doc["paths"]
    assert {"401", "429", "503"} <= set(paths["/api/v1/analyse-facts"]["post"]["responses"])
    assert {"401", "429", "503"} <= set(paths["/api/v1/analyse-facts/jobs"]["post"]["responses"])
    assert {"401", "404", "503"} <= set(paths["/api/v1/analyse-facts/jobs/{job_id}"]["get"]["responses"])
