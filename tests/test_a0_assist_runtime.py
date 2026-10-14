"""Runtime assembly: dark means inert; enabled means paused until the first store check; secrets only through env."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest
import yaml

from pravrudhi.application import assistant_tier as T
from pravrudhi.application.a0_assist import runtime as R
from pravrudhi.application.a0_assist import store as ST

REPO = Path(__file__).resolve().parent.parent


def root_with(tmp_path, **over):
    cfg = yaml.safe_load((REPO / "configs" / "assistant_tier.yaml").read_text())
    cfg.update(over)
    (tmp_path / "configs").mkdir(exist_ok=True)
    (tmp_path / "configs" / "assistant_tier.yaml").write_text(yaml.safe_dump(cfg))
    return tmp_path


ENABLED = {
    "assist_route_enabled": True,
    "agent_id": "agent_p",
    "agent_version": 1,
    "environment_id": "env_p",
    "store_ca_file": "/app/ca.crt",
}
ENV = {
    "ANTHROPIC_API_KEY": "sk-test-key",
    "ANTHROPIC_WORKSPACE_ID": "wrkspc_1",
    "SUPABASE_ASSIST_DB_URL": "postgresql://u:p@h:5432/d",
}


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    # the packaged config wins over a root's configs/ in an installed engine; point the reader at the test root
    import pravrudhi.application.config_files as CF

    monkeypatch.setattr(CF, "PACKAGED_CONFIG_DIR", Path("/nonexistent"))
    R.reset()
    T.reset()
    yield
    R.reset()
    T.reset()


def test_an_unreadable_config_gives_no_runtime(tmp_path):
    assert R.build(tmp_path, {}) is None


def test_a_dark_route_builds_an_inert_runtime_with_no_store_transport_thread_or_pause_source(tmp_path):
    rt = R.build(root_with(tmp_path), ENV)
    assert rt is not None and rt.enabled is False
    assert rt.store is None and rt.transport is None and rt.thread is None and T._pause_source is None


def test_an_enabled_route_is_paused_until_the_first_check(tmp_path, monkeypatch):
    monkeypatch.setattr(ST, "from_env", lambda *a, **k: (object(), None))
    rt = R.build(root_with(tmp_path, **ENABLED), ENV, start_thread=False)
    assert rt.enabled and rt.transport is not None and rt.store is not None and rt.sweeper is not None
    assert rt.pauses.snapshot() == {"startup": "first_check_pending"}
    assert T._pause_source() == "first_check_pending"


def test_a_missing_key_or_workspace_id_pauses_with_no_transport(tmp_path, monkeypatch):
    monkeypatch.setattr(ST, "from_env", lambda *a, **k: (object(), None))
    for env in ({**ENV, "ANTHROPIC_API_KEY": ""}, {k: v for k, v in ENV.items() if k != "ANTHROPIC_WORKSPACE_ID"}):
        rt = R.build(root_with(tmp_path, **ENABLED), env, start_thread=False)
        assert rt.transport is None and rt.pauses.snapshot()["transport"] == "transport_not_configured"


def test_an_unset_dsn_or_missing_driver_means_no_store_and_the_tier_stays_paused(tmp_path):
    rt = R.build(
        root_with(tmp_path, **ENABLED), {k: v for k, v in ENV.items() if k != "SUPABASE_ASSIST_DB_URL"}, start_thread=False
    )
    assert rt.store is None
    rt.sweeper.run_once()
    assert T._pause_source() is not None


def test_the_loop_clears_the_startup_pause_only_after_the_first_pass(tmp_path, monkeypatch):
    monkeypatch.setattr(ST, "from_env", lambda *a, **k: (None, "not_configured"))
    rt = R.build(root_with(tmp_path, **ENABLED), ENV, start_thread=False)
    calls = []
    rt.sweeper.run_once = lambda: calls.append(dict(rt.pauses.snapshot()))
    t = threading.Thread(target=R._loop, args=(rt,))
    t.start()
    for _ in range(200):
        if calls and "startup" not in rt.pauses.snapshot():
            break
        threading.Event().wait(0.01)
    rt.stop.set()
    t.join(5)
    assert calls and calls[0].get("startup") == "first_check_pending" and "startup" not in rt.pauses.snapshot()


def test_a_crashing_pass_pauses_and_the_loop_survives(tmp_path, monkeypatch):
    monkeypatch.setattr(ST, "from_env", lambda *a, **k: (None, "not_configured"))
    rt = R.build(root_with(tmp_path, **ENABLED), ENV, start_thread=False)

    def boom():
        rt.stop.set()
        raise RuntimeError("x")

    rt.sweeper.run_once = boom
    R._loop(rt)
    assert rt.pauses.snapshot().get("sweeper") == "sweeper_crashed"


def test_the_status_document_hides_the_age_fields_from_anonymous_callers(tmp_path, monkeypatch):
    class S:
        def status(self):
            return {"oldest_pending_delete_age_s": 7, "sweep_last_ok_age_s": 9}

    monkeypatch.setattr(ST, "from_env", lambda *a, **k: (S(), None))
    rt = R.build(root_with(tmp_path, **ENABLED), ENV, start_thread=False)
    anon, signed = R.status_document(rt, signed_in=False), R.status_document(rt, signed_in=True)
    assert "oldest_pending_delete_age_s" not in anon and "sweep_last_ok_age_s" not in anon
    assert signed["oldest_pending_delete_age_s"] == 7 and signed["sweep_last_ok_age_s"] == 9
    assert anon["provider"] == "Anthropic" and anon["consent_required"] is True and anon["session_budget_usd"] == 0.25


def test_the_status_document_without_a_runtime_has_no_numbers():
    d = R.status_document(None, signed_in=True)
    assert d["call_timeout_s"] is None and "oldest_pending_delete_age_s" not in d


def test_no_secret_value_is_stored_on_the_runtime_repr(tmp_path, monkeypatch):
    monkeypatch.setattr(ST, "from_env", lambda *a, **k: (None, "not_configured"))
    rt = R.build(root_with(tmp_path, **ENABLED), ENV, start_thread=False)
    assert "sk-test-key" not in repr(rt) and "postgresql://" not in repr(rt)
