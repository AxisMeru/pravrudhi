"""#228: the partner client sample (docs/api/examples/partner_client.py) and the curl walkthrough run against the
real app with a scripted stand-in judge, so neither can rot. Constructed inputs only; not a live-judge run."""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from pravrudhi.api import identity
from pravrudhi.api.partner import PartnerApiConfig, build_partner_router
from tests.test_api_partner import _agent, _proof_script

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "docs" / "api" / "examples"
DOC = ROOT / "docs" / "api" / "partner-quickstart-client.md"
PROVISION_SECRET = "p" * 40


def _load() -> Any:
    spec = importlib.util.spec_from_file_location("partner_client", EXAMPLES / "partner_client.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


sample = _load()


def _app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **cfg: Any) -> TestClient:
    monkeypatch.setenv("PRAVRUDHI_TENANCY_PROVISION_SECRET", PROVISION_SECRET)
    monkeypatch.delenv("PRAVRUDHI_ADMINS", raising=False)
    agent = _agent(tmp_path, _proof_script())
    app = FastAPI()
    config = PartnerApiConfig(**{"rate_limit_per_minute": 1000, "max_concurrent": 100, "trust_proxy_header": False, **cfg})
    app.include_router(build_partner_router(tmp_path, agent_factory=lambda _r: agent, config=config))
    app.dependency_overrides[identity.current_user] = lambda: None
    return TestClient(app)


def _send(c: TestClient) -> Any:
    def send(method: str, path: str, body: dict[str, Any] | None, headers: dict[str, str]) -> tuple[int, dict[str, str], Any]:
        r = c.request(method, path, json=body, headers=headers)
        return r.status_code, dict(r.headers), r.json()

    return send


def test_split_facts_one_per_paragraph() -> None:
    assert sample.split_facts("a b\nc\n\n\nd\n") == ["a b c", "d"]


@pytest.mark.parametrize(
    ("text", "msg"),
    [("  \n\n ", "no non-empty"), ("\n\n".join("x" * 3 for _ in range(9)), "at most 8"), ("y" * 4001, "exceed 4000")],
)
def test_split_facts_refuses_what_the_api_refuses(text: str, msg: str) -> None:
    with pytest.raises(ValueError, match=msg):
        sample.split_facts(text)


def test_the_whole_flow_provision_split_analyse_summarise(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    send = _send(_app(tmp_path, monkeypatch))
    key = sample.provision(send, PROVISION_SECRET, "acme-test")
    facts = sample.split_facts((EXAMPLES / "toy_facts.txt").read_text())
    assert len(facts) == 3
    result = sample.PartnerClient(send, key).analyse_facts(facts, ["bns69"], proceeding_posture="trial")
    assert result["contracts"][0]["outcome"] == "PROOF"
    assert result["standard"]["requested"] == "proved" and result["standard"]["applied"] is None
    assert result["standard"]["in_judge_prompt"] is False  # default legacy prompt: basis recorded, judge never saw it
    lines = sample.summarise(result)
    assert "NOT applied" in lines[0] and "requested: proved" in lines[0]
    assert "bns69: PROOF (all_elements_established)" in lines


def test_provision_is_refused_without_the_right_secret(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(sample.ApiError) as e:
        sample.provision(_send(_app(tmp_path, monkeypatch)), "wrong" * 10, "acme-test")
    assert e.value.status in (401, 403)


def test_a_bad_key_raises_api_error_with_the_status(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(sample.ApiError) as e:
        sample.PartnerClient(_send(_app(tmp_path, monkeypatch)), "not-a-real-key").analyse_facts(["TOY: x"], ["bns69"])
    assert e.value.status == 401


def test_rate_limit_error_carries_retry_after(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    send = _send(_app(tmp_path, monkeypatch, rate_limit_per_minute=1))
    client = sample.PartnerClient(send, sample.provision(send, PROVISION_SECRET, "acme-test"))
    client.analyse_facts(["TOY: x"], ["bns69"])
    with pytest.raises(sample.ApiError) as e:
        client.analyse_facts(["TOY: x"], ["bns69"])
    assert e.value.status == 429 and e.value.retry_after


def test_refer_to_lawyer_is_flagged_as_a_refusal_not_a_result() -> None:
    lines = sample.summarise(
        {
            "standard": {"requested": "proved", "applied": "proved", "source": "default", "in_judge_prompt": True},
            "contracts": [{"contract_id": "bns69", "outcome": "REFER_TO_LAWYER", "reason": "x"}],
        }
    )
    assert any("declines to give a verdict" in line for line in lines)
    assert "given to the judge" in lines[0] and "NOT" not in lines[0]


# -- the walkthrough document ---------------------------------------------------------------------------


def test_every_documented_route_exists_in_the_contract() -> None:
    paths = json.loads((ROOT / "docs/api/openapi-v1.json").read_text())["paths"]
    for route in set(re.findall(r"https://api\.example\.com(/api/v1[^\s\"'?]*)", DOC.read_text())):
        assert re.sub(r"/orgs/[^/]+", "/orgs/{org_id}", route) in paths, route


def test_the_walkthrough_uses_only_placeholder_hosts() -> None:
    for path in [DOC, EXAMPLES / "partner_client.py"]:
        hosts = set(re.findall(r"https?://([A-Za-z0-9.-]+)", path.read_text()))
        assert hosts <= {"api.example.com"}, (path.name, hosts)


def test_the_walkthrough_says_what_the_contract_lacks() -> None:
    text = DOC.read_text()
    for absent in ("Matters", "Server-side document upload", "Streaming (SSE)"):
        assert absent in text
    paths = json.loads((ROOT / "docs/api/openapi-v1.json").read_text())["paths"]
    assert not [p for p in paths if re.search(r"matter|upload|stream", p)], "contract grew a route; update the doc"
    assert "/api/v1/analyse-facts/jobs" in paths and "/analyse-facts/jobs" in text
