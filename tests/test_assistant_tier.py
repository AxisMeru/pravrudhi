"""#122 engine half: the assistant tier's state and reason, one test per rule arm, and the /api/health shape."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from pravrudhi.api.server import create_app
from pravrudhi.application import assistant_tier as T

REPO = Path(__file__).resolve().parent.parent

#: The shipped file turns the route on (0.6.5); these tests start from its dark form.
DARK = {"assist_route_enabled": False, "agent_id": None, "agent_version": None, "environment_id": None}
KEY = {"ANTHROPIC_API_KEY": "sk-test"}
UNAVAILABLE = "unavailable"


def base_cfg(**over):
    cfg = yaml.safe_load((REPO / "configs" / "assistant_tier.yaml").read_text())
    cfg.update(DARK)  # the shipped file is enabled (0.6.5); these tests start dark
    cfg.update(over)
    return cfg


def root_with(tmp_path: Path, cfg=None, *, raw: str | None = None) -> Path:
    (tmp_path / "configs").mkdir(exist_ok=True)
    (tmp_path / "configs" / "assistant_tier.yaml").write_text(
        raw if raw is not None else yaml.safe_dump(cfg if cfg is not None else base_cfg())
    )
    return tmp_path


@pytest.fixture(autouse=True)
def _clean():
    T.reset()
    yield
    T.reset()


def st(root, env=KEY, now=lambda: 1000.0):
    return T.status(root, env=env, now=now)


def test_shipped_config_is_available_with_a_key(tmp_path):
    assert st(root_with(tmp_path)) == {"state": "available", "reason": None}


def test_shipped_config_has_no_unsafe_default():
    cfg = base_cfg()
    assert (
        cfg["enabled"] is True
        and cfg["force_unavailable"] is False
        and cfg["budget_file"] is None
        and cfg["key_env_names"] == ["ANTHROPIC_API_KEY"]
    )


# disabled arms
def test_enabled_false_is_disabled(tmp_path):
    assert st(root_with(tmp_path, base_cfg(enabled=False))) == {"state": UNAVAILABLE, "reason": "disabled"}


def test_force_unavailable_overrides_a_healthy_tier(tmp_path):
    assert st(root_with(tmp_path, base_cfg(force_unavailable=True))) == {"state": UNAVAILABLE, "reason": "disabled"}


def test_missing_config_fails_closed(tmp_path):
    assert st(tmp_path) == {"state": UNAVAILABLE, "reason": "disabled"}


def test_config_not_a_mapping_fails_closed(tmp_path):
    assert st(root_with(tmp_path, raw="- a\n- b\n")) == {"state": UNAVAILABLE, "reason": "disabled"}


def test_malformed_yaml_fails_closed(tmp_path):
    assert st(root_with(tmp_path, raw="enabled: [unclosed")) == {"state": UNAVAILABLE, "reason": "disabled"}


@pytest.mark.parametrize(
    "key,val",
    [
        ("failure_ttl_s", "300"),
        ("failure_ttl_s", -1),
        ("budget_floor_usd", None),
        ("budget_floor_usd", -5),
        ("failure_ttl_s", True),
    ],
)
def test_malformed_numbers_fail_closed(tmp_path, key, val):
    assert st(root_with(tmp_path, base_cfg(**{key: val}))) == {"state": UNAVAILABLE, "reason": "disabled"}


def test_enabled_must_be_true_not_truthy(tmp_path):
    assert st(root_with(tmp_path, base_cfg(enabled="yes")))["reason"] == "disabled"


# no_key arms
def test_no_key_env_set(tmp_path):
    assert st(root_with(tmp_path), env={}) == {"state": UNAVAILABLE, "reason": "no_key"}


def test_empty_key_is_no_key(tmp_path):
    assert st(root_with(tmp_path), env={"ANTHROPIC_API_KEY": ""})["reason"] == "no_key"


def test_any_configured_name_counts(tmp_path):
    root = root_with(tmp_path, base_cfg(key_env_names=["A_KEY", "B_KEY"]))
    assert st(root, env={"B_KEY": "x"})["state"] == "available" and st(root, env={"C_KEY": "x"})["reason"] == "no_key"


@pytest.mark.parametrize("names", [[], None, "ANTHROPIC_API_KEY"])
def test_key_names_must_be_a_non_empty_list(tmp_path, names):
    assert st(root_with(tmp_path, base_cfg(key_env_names=names)))["reason"] == "no_key"


# last-call arms
@pytest.mark.parametrize(
    "code,reason",
    [
        (429, "quota"),
        (402, "quota"),
        (500, "upstream_error"),
        (503, "upstream_error"),
        (404, "upstream_error"),
        (401, "upstream_error"),
        (599, "upstream_error"),
    ],
)
def test_failed_call_makes_it_unavailable_with_the_reason(tmp_path, code, reason):
    root = root_with(tmp_path)
    T.record_outcome(code, now=lambda: 1000.0)
    assert st(root) == {"state": UNAVAILABLE, "reason": reason}


@pytest.mark.parametrize("code", [200, 201, 204, 302])
def test_success_status_does_not_block(tmp_path, code):
    root = root_with(tmp_path)
    T.record_outcome(code, now=lambda: 1000.0)
    assert st(root)["state"] == "available"


def test_later_success_clears_an_earlier_failure(tmp_path):
    root = root_with(tmp_path)
    T.record_outcome(500, now=lambda: 1000.0)
    T.record_outcome(200, now=lambda: 1001.0)
    assert st(root)["state"] == "available"


def test_failure_expires_after_the_ttl(tmp_path):
    root = root_with(tmp_path, base_cfg(failure_ttl_s=300))
    T.record_outcome(500, now=lambda: 1000.0)
    assert st(root, now=lambda: 1300.0)["reason"] == "upstream_error" and st(root, now=lambda: 1300.5)["state"] == "available"


def test_no_outcome_recorded_is_available(tmp_path):
    assert st(root_with(tmp_path))["state"] == "available"


# budget arms
def budget(tmp_path, body):
    p = tmp_path / "budget.json"
    p.write_text(body if isinstance(body, str) else json.dumps(body))
    return str(p)


def test_budget_below_floor(tmp_path):
    root = root_with(tmp_path, base_cfg(budget_file=budget(tmp_path, {"remaining_usd": 19.99}), budget_floor_usd=20))
    assert st(root) == {"state": UNAVAILABLE, "reason": "budget_floor"}


def test_budget_equal_to_floor_is_available(tmp_path):
    assert (
        st(root_with(tmp_path, base_cfg(budget_file=budget(tmp_path, {"remaining_usd": 20}), budget_floor_usd=20)))["state"]
        == "available"
    )


def test_budget_above_floor(tmp_path):
    assert st(root_with(tmp_path, base_cfg(budget_file=budget(tmp_path, {"remaining_usd": 140.5}))))["state"] == "available"


@pytest.mark.parametrize(
    "body",
    [
        "not json",
        "[]",
        "{}",
        '{"remaining_usd": "140"}',
        '{"remaining_usd": true}',
        '{"remaining_usd": NaN}',
        '{"remaining_usd": null}',
    ],
)
def test_unreadable_or_malformed_budget_fails_closed(tmp_path, body):
    assert st(root_with(tmp_path, base_cfg(budget_file=budget(tmp_path, body))))["reason"] == "budget_floor"


def test_missing_budget_file_fails_closed(tmp_path):
    assert st(root_with(tmp_path, base_cfg(budget_file=str(tmp_path / "nope.json"))))["reason"] == "budget_floor"


def test_no_budget_file_skips_the_check(tmp_path):
    assert st(root_with(tmp_path, base_cfg(budget_file=None)))["state"] == "available"


def test_failure_reason_precedes_budget(tmp_path):
    root = root_with(tmp_path, base_cfg(budget_file=budget(tmp_path, {"remaining_usd": 0})))
    T.record_outcome(429, now=lambda: 1000.0)
    assert st(root)["reason"] == "quota"


def test_no_key_precedes_failure_and_budget(tmp_path):
    root = root_with(tmp_path, base_cfg(budget_file=budget(tmp_path, {"remaining_usd": 0})))
    T.record_outcome(500, now=lambda: 1000.0)
    assert st(root, env={})["reason"] == "no_key"


def test_disabled_precedes_everything(tmp_path):
    assert st(root_with(tmp_path, base_cfg(enabled=False)), env={})["reason"] == "disabled"


# limits
def test_limits_from_config(tmp_path):
    assert T.limits(root_with(tmp_path, base_cfg(call_timeout_s=30, session_budget_usd=0.5))) == {
        "call_timeout_s": 30.0,
        "session_budget_usd": 0.5,
    }


@pytest.mark.parametrize(
    "over", [{"call_timeout_s": 0}, {"session_budget_usd": -1}, {"call_timeout_s": "60"}, {"session_budget_usd": None}]
)
def test_limits_fail_closed(tmp_path, over):
    assert T.limits(root_with(tmp_path, base_cfg(**over))) is None


def test_limits_without_a_config(tmp_path):
    assert T.limits(tmp_path) is None


# /api/health
def client(root):
    return TestClient(create_app(root), base_url="http://127.0.0.1:8008")


def test_health_shape_available(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    body = client(root_with(tmp_path)).get("/api/health").json()
    assert body["assistant_tier"] == {"state": "available", "reason": None} and body["frontier_reader_available"] is False
    assert {"ok", "version", "kernel", "ledger"} <= set(body)


def test_health_unavailable_forces_frontier_reader_false(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    body = client(root_with(tmp_path)).get("/api/health").json()
    assert body["assistant_tier"] == {"state": UNAVAILABLE, "reason": "no_key"} and body["frontier_reader_available"] is False


def test_health_force_flag(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    body = client(root_with(tmp_path, base_cfg(force_unavailable=True))).get("/api/health").json()
    assert body["assistant_tier"] == {"state": UNAVAILABLE, "reason": "disabled"} and body["frontier_reader_available"] is False


def test_health_reflects_a_recorded_failure(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    c = client(root_with(tmp_path))
    assert c.get("/api/health").json()["assistant_tier"]["state"] == "available"
    T.record_outcome(429)
    body = c.get("/api/health").json()
    assert body["assistant_tier"] == {"state": UNAVAILABLE, "reason": "quota"} and body["frontier_reader_available"] is False


def test_health_without_config_is_unavailable_not_an_error(tmp_path):
    r = client(tmp_path).get("/api/health")
    assert r.status_code == 200 and r.json()["assistant_tier"] == {"state": UNAVAILABLE, "reason": "disabled"}


def test_the_call_site_contract_is_stated_in_the_module():
    doc = T.__doc__
    assert (
        "BINDING CONTRACT" in doc
        and 'limits(root)["call_timeout_s"]' in doc
        and 'limits(root)["session_budget_usd"]' in doc
        and "record_outcome(http_status)" in doc
    )
    assert "402/429 = quota" in doc and "upstream_error" in doc and "falls back to the core result" in doc


@pytest.mark.parametrize("scenario", ["available", "no_key", "forced", "quota", "budget"])
def test_frontier_reader_available_is_false_in_every_state(tmp_path, monkeypatch, scenario):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    cfg = base_cfg()
    if scenario == "no_key":
        monkeypatch.delenv("ANTHROPIC_API_KEY")
    if scenario == "forced":
        cfg["force_unavailable"] = True
    if scenario == "budget":
        cfg["budget_file"] = str(tmp_path / "missing.json")
    c = client(root_with(tmp_path, cfg))
    if scenario == "quota":
        T.record_outcome(429)
    body = c.get("/api/health").json()
    assert body["frontier_reader_available"] is False
    assert (body["assistant_tier"]["state"] == "available") == (scenario == "available")


def test_config_that_is_not_valid_utf8_fails_closed(tmp_path):
    root = root_with(tmp_path)
    (root / "configs" / "assistant_tier.yaml").write_bytes(b"\xff\xfe enabled: true")
    assert st(root) == {"state": UNAVAILABLE, "reason": "disabled"} and T.limits(root) is None


def test_deeply_nested_config_fails_closed(tmp_path):
    assert st(root_with(tmp_path, raw="x: " + "[" * 100000))["reason"] == "disabled"


def test_deeply_nested_budget_file_fails_closed(tmp_path):
    p = tmp_path / "budget.json"
    p.write_text("[" * 100000)
    assert st(root_with(tmp_path, base_cfg(budget_file=str(p))))["reason"] == "budget_floor"


def test_budget_file_that_is_not_valid_utf8_fails_closed(tmp_path):
    p = tmp_path / "budget.json"
    p.write_bytes(b"\xff\xfe")
    assert st(root_with(tmp_path, base_cfg(budget_file=str(p))))["reason"] == "budget_floor"


def test_health_never_500s_when_the_tier_check_raises(tmp_path, monkeypatch):
    def boom(root):
        raise RuntimeError("anything")

    monkeypatch.setattr(T, "status", boom)
    r = client(root_with(tmp_path)).get("/api/health")
    assert (
        r.status_code == 200
        and r.json()["assistant_tier"] == {"state": UNAVAILABLE, "reason": "disabled"}
        and r.json()["frontier_reader_available"] is False
    )


def test_health_with_a_non_utf8_config_is_not_a_500(tmp_path):
    root = root_with(tmp_path)
    (root / "configs" / "assistant_tier.yaml").write_bytes(b"\xff\xfe")
    r = client(root).get("/api/health")
    assert r.status_code == 200 and r.json()["assistant_tier"]["reason"] == "disabled"


def test_a_pause_source_with_a_code_makes_the_tier_paused_with_the_documented_reason(tmp_path):
    root = root_with(tmp_path)
    T.set_pause_source(lambda: "canary_failed")
    assert T.status(root, env=KEY) == {"state": UNAVAILABLE, "reason": "upstream_error"}
    assert T.pause_cause() == "canary_failed"  # the fine-grained cause stays internal


def test_a_pause_source_returning_none_leaves_the_tier_as_it_was(tmp_path):
    root = root_with(tmp_path)
    T.set_pause_source(lambda: None)
    assert T.status(root, env=KEY)["state"] == "available"


def test_a_pause_source_that_raises_pauses_the_tier_never_raises(tmp_path):
    root = root_with(tmp_path)

    def boom():
        raise RuntimeError("x")

    T.set_pause_source(boom)
    assert T.status(root, env=KEY) == {"state": UNAVAILABLE, "reason": "upstream_error"}
    assert T.pause_cause() == "pause_source_error"


def test_clearing_the_pause_source_restores_the_tier(tmp_path):
    root = root_with(tmp_path)
    T.set_pause_source(lambda: "x")
    T.set_pause_source(None)
    assert T.status(root, env=KEY)["state"] == "available"


GUARD_CAUSES = [
    "store_not_configured",
    "store_unreachable",
    "store_status_unreadable",
    "store_not_initialised",
    "canary_failed",
    "canary_never_run",
    "canary_stale",
    "canary_error",
    "canary_timeout",
    "sweep_stale",
    "pending_deletes_count",
    "pending_deletes_age",
    "transport_not_configured",
    "first_check_pending",
    "sweeper_not_configured",
    "sweeper_store_error",
    "sweeper_crashed",
    "delete_canary_failed",
    "workspace_check_failed",
]


@pytest.mark.parametrize("cause", GUARD_CAUSES)
def test_every_guard_cause_is_paused_with_upstream_error_never_disabled_budget_floor_or_quota(tmp_path, cause):
    T.set_pause_source(lambda: cause)
    got = T.status(root_with(tmp_path), env=KEY)
    assert got == {"state": UNAVAILABLE, "reason": "upstream_error"}
    assert got["reason"] not in ("disabled", "budget_floor", "quota", "no_key")


def test_disabled_stays_for_config_off_only(tmp_path):
    T.set_pause_source(lambda: "canary_failed")
    assert T.status(root_with(tmp_path, base_cfg(enabled=False)), env=KEY)["reason"] == "disabled"
    assert T.status(root_with(tmp_path, base_cfg(force_unavailable=True)), env=KEY)["reason"] == "disabled"


def test_the_guard_causes_listed_here_are_exactly_the_ones_the_code_can_set():
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent / "src" / "pravrudhi" / "application" / "a0_assist"
    found = set()
    for f in root.glob("*.py"):
        found |= set(re.findall(r'pauses\.set\(\s*"[a-z_]+",\s*"([a-z_]+)"', f.read_text()))
        found |= set(re.findall(r'return "([a-z_]+)"', f.read_text())) if f.name == "guard.py" else set()
    assert found - {"canary_never_run", "canary_stale", "canary_failed", "store_not_configured"} <= set(GUARD_CAUSES)
