"""The 2026-09-11 adversarial review's verified defects, each pinned by a test that failed before the fix.

C2: `agent-for-operator` fell through to the human sign-off path because two modules kept their own copy of
    the agent-identity set. H1: a promoted harness recipe could not be re-read, so every night started from the
    baseline. C1: a credential pasted into a request reached the published snapshot verbatim.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from pravrudhi.api import server
from pravrudhi.api.server import create_app
from pravrudhi.application import delegation, gate
from pravrudhi.application.demo_export import SecretInSnapshot, redact_secrets
from pravrudhi.targets.harness_grammar import BASELINE, HarnessRecipe, parse_harness
from tests.test_gate import _emit, repo  # noqa: F401  (fixture re-exported on purpose)


def test_one_agent_identity_set_everywhere() -> None:
    assert server.AGENT_IDENTITIES is delegation.AGENT_IDENTITIES
    assert gate.AGENT_IDENTITIES is delegation.AGENT_IDENTITIES
    assert "agent-for-operator" in gate.AGENT_IDENTITIES


def test_sign_gate_refuses_the_delegation_identity_as_a_human_name(repo: Path) -> None:  # noqa: F811
    out = _emit(repo)
    with pytest.raises(PermissionError):
        gate.sign_gate(out, by="agent-for-operator", note="x")


def test_inbox_sign_route_treats_agent_for_operator_as_an_agent(tmp_path: Path) -> None:
    """With no delegation recorded the agent branch answers 403 naming the delegation; the human branch would
    have gone on to look for the pack. Which message comes back says which branch ran."""
    from pravrudhi.api.localguard import TOKEN_HEADER, app_token
    from pravrudhi.application.init import init_project

    init_project(tmp_path)
    c = TestClient(create_app(tmp_path), base_url="http://127.0.0.1:8008")
    headers = {"X-Pravrudhi-Operator": "agent-for-operator", TOKEN_HEADER: app_token(tmp_path)}
    r = c.post("/api/inbox/sign", json={"pack": "nothing.json", "decision": "approve", "note": ""}, headers=headers)
    assert r.status_code == 403
    assert "delegation" in r.json()["detail"]


def _delegation_file(root: Path) -> None:
    (root / "configs").mkdir(exist_ok=True)
    (root / "configs" / "delegation.yaml").write_text(
        yaml.safe_dump(
            {
                "active": True,
                "granted": "2026-09-10",
                "instruction": "proceed autonomously",
                "signature_identity": "agent-for-operator",
                "scope": {"gate_signoff": True, "promote_t2": True, "interp_claim": False},
                "conditions": {
                    "gate_signoff": {"require_gate_check_clean": True, "require_closure_layers_pass": True},
                    "promote_t2": {"require_green_badge": True},
                },
            }
        )
    )


def test_delegated_sign_closes_a_clean_gate_as_the_delegation_identity(repo: Path) -> None:  # noqa: F811
    _delegation_file(repo)
    out = _emit(repo)
    gate.sign_gate_delegated(out, root=repo, contracts_dir=repo / "contracts")
    signed = json.loads(out.read_text())
    assert signed["signoff"]["by"] == "agent-for-operator"
    assert "delegation of 2026-09-10" in signed["signoff"]["note"]
    assert signed["closure"]["signoff"]["verdict"] == "pass"
    assert gate.check_gate(out, contracts_dir=repo / "contracts") == []


def test_delegated_sign_refuses_when_the_gate_check_is_not_clean(repo: Path) -> None:  # noqa: F811
    """The schema already refuses a passing gate with a failing layer, so the condition that can fail on a
    well-formed gate is the gate check: here the contract card has gone missing after emission."""
    _delegation_file(repo)
    out = _emit(repo)
    (repo / "contracts" / "L0_scaffold.md").unlink()
    with pytest.raises(PermissionError, match="gate check found"):
        gate.sign_gate_delegated(out, root=repo, contracts_dir=repo / "contracts")
    assert json.loads(out.read_text())["signoff"]["by"] is None


def test_delegated_sign_without_a_delegation_is_a_human_act(repo: Path) -> None:  # noqa: F811
    out = _emit(repo)
    with pytest.raises(PermissionError, match="human act"):
        gate.sign_gate_delegated(out, root=repo, contracts_dir=repo / "contracts")


def test_a_promoted_recipe_round_trips_through_its_own_json() -> None:
    rec = HarnessRecipe(strategy="retry_policy", execution_family="feedback", retries=2, max_new_tokens=1024)
    back = parse_harness(rec.harness_json())
    assert not isinstance(back, str), back
    assert back.strategy == "retry_policy" and back.execution_family == "feedback"
    assert back.harness_json() == rec.harness_json()
    assert "rationale" not in rec.harness_json()
    assert not isinstance(parse_harness(BASELINE.harness_json()), str)


def test_a_bot_token_pasted_into_a_request_never_reaches_the_snapshot() -> None:
    token = "1234567890:AA" + "x" * 33
    text = json.dumps({"ask": f"Use this token to access the HTTP API: {token} chat_id: 8679892510"})
    out = redact_secrets(text)
    assert token not in out and "8679892510" not in out
    assert "<redacted:telegram-bot-token>" in out and "chat_id: <redacted:telegram-chat-id>" in out
    assert redact_secrets("sk-" + "a" * 40) == "<redacted:openai-style-key>"
    assert redact_secrets("hf_" + "b" * 34) == "<redacted:huggingface-token>"
    assert redact_secrets("ghp_" + "c" * 36) == "<redacted:github-token>"
    assert redact_secrets("night 26 scored 0.0399 at 16:14 UTC") == "night 26 scored 0.0399 at 16:14 UTC"
    assert issubclass(SecretInSnapshot, RuntimeError)
