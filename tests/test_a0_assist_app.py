"""The assisted tier in the real app: dark by default, paused at startup when enabled, GET /api/assistant shape."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from pravrudhi.api.server import create_app
from pravrudhi.application import assistant_tier as T
from pravrudhi.application.a0_assist import runtime as R

REPO = Path(__file__).resolve().parent.parent


def root_with(tmp_path, **over):
    cfg = yaml.safe_load((REPO / "configs" / "assistant_tier.yaml").read_text())
    cfg.update(over)
    (tmp_path / "configs").mkdir(parents=True, exist_ok=True)
    (tmp_path / "configs" / "assistant_tier.yaml").write_text(yaml.safe_dump(cfg))
    return tmp_path


ENABLED = {
    "assist_route_enabled": True,
    "agent_id": "agent_p",
    "agent_version": 1,
    "environment_id": "env_p",
    "store_ca_file": "/app/ca.crt",
}


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    import pravrudhi.application.config_files as CF

    monkeypatch.setattr(CF, "PACKAGED_CONFIG_DIR", Path("/nonexistent"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-real")
    monkeypatch.delenv("SUPABASE_ASSIST_DB_URL", raising=False)
    monkeypatch.delenv("ANTHROPIC_WORKSPACE_ID", raising=False)
    monkeypatch.setenv("PRAVRUDHI_AUTH", "disabled")
    R.reset()
    T.reset()
    yield
    R.reset()
    T.reset()


def test_a_dark_config_builds_an_inert_runtime_and_leaves_health_as_it_was(tmp_path):
    c = TestClient(create_app(root_with(tmp_path)), base_url="http://127.0.0.1:8008")
    assert R._runtime is not None and R._runtime.enabled is False and T._pause_source is None
    assert c.get("/api/health").json()["assistant_tier"]["state"] == "available"


def test_an_enabled_config_is_paused_from_startup_so_health_reads_paused(tmp_path):
    c = TestClient(create_app(root_with(tmp_path, **ENABLED)), base_url="http://127.0.0.1:8008")
    assert R._runtime is not None and R._runtime.enabled is True and T._pause_source is not None
    assert c.get("/api/health").json()["assistant_tier"] == {"state": "unavailable", "reason": "upstream_error"}


def test_a_broken_config_leaves_the_route_off_and_the_app_up(tmp_path):
    root = root_with(tmp_path)
    (root / "configs" / "assistant_tier.yaml").write_text("assist_route_enabled: [not, a, bool]\n")
    c = TestClient(create_app(root), base_url="http://127.0.0.1:8008")
    assert c.get("/api/health").status_code == 200 and R._runtime is None


def test_get_assistant_has_the_documented_shape_and_hides_the_ages_from_anonymous_callers(tmp_path):
    c = TestClient(create_app(root_with(tmp_path)), base_url="http://127.0.0.1:8008")
    d = c.get("/api/assistant").json()
    assert d["provider"] == "Anthropic" and d["consent_required"] is True and d["session_budget_usd"] == 0.25
    assert d["assistant_tier"]["state"] in ("available", "unavailable")
    assert "oldest_pending_delete_age_s" not in d and "sweep_last_ok_age_s" not in d


def test_get_assistant_never_500s_when_the_runtime_misbehaves(tmp_path, monkeypatch):
    c = TestClient(create_app(root_with(tmp_path)), base_url="http://127.0.0.1:8008")
    monkeypatch.setattr(
        R,
        "status_document",
        lambda *a, **k: (
            (_ for _ in ()).throw(RuntimeError("x")) if k.get("signed_in") is not False else {"provider": "Anthropic"}
        ),
    )
    r = c.get("/api/assistant")
    assert r.status_code == 200 and r.json()["provider"] == "Anthropic"


def test_get_assistant_shows_the_two_ages_to_a_signed_in_caller_only(tmp_path, monkeypatch):
    from pravrudhi.api import identity
    from pravrudhi.application.a0_assist import store as ST

    class S:
        def status(self):
            return {"oldest_pending_delete_age_s": 7, "sweep_last_ok_age_s": 9}

    monkeypatch.setattr(ST, "from_env", lambda *a, **k: (S(), None))
    monkeypatch.setattr(R, "_loop", lambda rt: None)
    app = create_app(root_with(tmp_path, **ENABLED))
    c = TestClient(app, base_url="http://127.0.0.1:8008")
    anon = c.get("/api/assistant").json()
    app.dependency_overrides[identity.current_user] = lambda: identity.User("u1", "user@example.test", "user")
    signed = c.get("/api/assistant").json()
    assert "oldest_pending_delete_age_s" not in anon and "sweep_last_ok_age_s" not in anon
    assert signed["oldest_pending_delete_age_s"] == 7 and signed["sweep_last_ok_age_s"] == 9
