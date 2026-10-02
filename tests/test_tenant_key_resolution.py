"""#387: a signed-in tenant's vendor call resolves the tenant's stored key or nothing, never the operator's
environment variable or credential file. The single-operator path (no user) keeps both."""

from __future__ import annotations

import pytest

from pravrudhi.api.identity import User
from pravrudhi.application import nyaya, panel
from pravrudhi.application.credentials import store_for, store_for_project

OP_KEY = "sk-op-" + "o" * 30
TENANT_KEY = "sk-tn-" + "t" * 30
USER = User(id="u1", email="t@example.com", role="authenticated")


@pytest.fixture
def engine(tmp_path, monkeypatch):
    eng = tmp_path / "engine"
    eng.mkdir()
    for v in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GOOGLE_API_KEY", "DASHSCOPE_API_KEY"):
        monkeypatch.setenv(v, OP_KEY)
    return eng


def tenant_store(tmp_path, key=None):
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    s = store_for_project(ws, engine_root=tmp_path / "engine", user=USER)
    if key:
        s.put("openai", key)
    return s


@pytest.mark.parametrize("vid", ["openai-api", "anthropic-api", "google-api"])
def test_tenant_with_empty_store_does_not_get_the_operator_env_key(tmp_path, engine, vid):
    assert panel.VENDORS[vid].key(tmp_path, store=tenant_store(tmp_path)) is None


def test_tenant_with_a_stored_key_gets_that_key_not_the_env(tmp_path, engine):
    assert panel.VENDORS["openai-api"].key(tmp_path, store=tenant_store(tmp_path, TENANT_KEY)) == TENANT_KEY


def test_tenant_does_not_reach_the_operators_credential_file(tmp_path, engine, monkeypatch):
    monkeypatch.delenv("DASHSCOPE_API_KEY")
    f = tmp_path / "dashscope-plan.env"
    f.write_text(f"DASHSCOPE_API_KEY={OP_KEY}\n")
    f.chmod(0o600)
    v = panel.Vendor(id="x", interface="openai_compat", model="m", base_url="http://x/v1",
                     credential="DASHSCOPE_API_KEY", credential_file=str(f), provider="alibaba-plan")
    assert v.key(tmp_path, store=tenant_store(tmp_path)) is None
    assert v.key(tmp_path) == OP_KEY  # the single-operator path is unchanged


def test_single_operator_path_keeps_env_and_file(tmp_path, engine):
    assert panel.VENDORS["openai-api"].key(tmp_path) == OP_KEY
    op_store = store_for(tmp_path / "engine", None, operator_path=True)
    assert panel.VENDORS["openai-api"].key(tmp_path, store=op_store) == OP_KEY


def test_ask_vendor_for_a_tenant_refuses_with_zero_calls(tmp_path, engine, monkeypatch):
    import pravrudhi.models.openai_compat as oc

    calls = []
    monkeypatch.setattr(oc.ChatClient, "chat", lambda *a, **k: calls.append(1))
    with pytest.raises(RuntimeError, match="not set"):
        panel.ask_vendor(panel.VENDORS["openai-api"], "p", root=tmp_path, store=tenant_store(tmp_path))
    assert calls == []


def test_available_vendors_reports_off_the_tenant_store(tmp_path, engine):
    rows = {r["id"]: r for r in nyaya.available_vendors(
        tmp_path, ("openai-api", "anthropic-api"), store=tenant_store(tmp_path))}
    assert not rows["openai-api"]["available"] and "no key" in rows["openai-api"]["why"]
    rows = {r["id"]: r for r in nyaya.available_vendors(
        tmp_path, ("openai-api",), store=tenant_store(tmp_path, TENANT_KEY))}
    assert rows["openai-api"]["available"]


def test_anonymous_store_is_closed_by_default(tmp_path, engine):
    anon = store_for_project(tmp_path / "engine", engine_root=tmp_path / "engine", user=None)
    assert anon.tenant_only
    assert panel.VENDORS["openai-api"].key(tmp_path, store=anon) is None


def test_partner_key_caller_uses_only_that_orgs_store(tmp_path, engine):
    org = User(id="org-7", email="org7@example.com", role="authenticated")
    ws = tmp_path / "org7"
    ws.mkdir()
    s = store_for_project(ws, engine_root=tmp_path / "engine", user=org)
    assert panel.VENDORS["openai-api"].key(tmp_path, store=s) is None
    s.put("openai", TENANT_KEY)
    assert panel.VENDORS["openai-api"].key(tmp_path, store=s) == TENANT_KEY


def test_cli_opt_in_is_explicit(tmp_path, engine):
    assert not store_for(tmp_path, None).operator_path
    assert panel.VENDORS["openai-api"].key(tmp_path, store=store_for(tmp_path, None)) is None
    assert panel.VENDORS["openai-api"].key(tmp_path, store=store_for(tmp_path, None, operator_path=True)) == OP_KEY


def test_anonymous_api_ask_with_vendors_refuses_with_zero_calls(tmp_path, engine, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import pravrudhi.models.openai_compat as oc
    from pravrudhi.api.nyaya import build_nyaya_router

    calls = []
    monkeypatch.setattr(oc.ChatClient, "chat", lambda *a, **k: calls.append(1))
    app = FastAPI()
    app.include_router(build_nyaya_router(engine))
    r = TestClient(app).post("/api/nyaya/ask", json={"question": "q?", "vendors": ["openai-api"]})
    assert calls == []
    assert OP_KEY not in r.text


def test_api_request_reaching_ask_vendor_with_no_store_raises_with_zero_calls(tmp_path, engine, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import pravrudhi.models.openai_compat as oc
    from pravrudhi.application.credentials import ServingApiMiddleware

    calls = []
    monkeypatch.setattr(oc.ChatClient, "chat", lambda *a, **k: calls.append(1))
    app = FastAPI()
    app.add_middleware(ServingApiMiddleware)
    seen = {}

    @app.get("/x")
    def x():
        try:
            panel.ask_vendor(panel.VENDORS["openai-api"], "p", root=tmp_path, store=None)
        except RuntimeError as e:
            seen["err"] = str(e)
        try:
            panel.VENDORS["openai-api"].key(tmp_path)
        except RuntimeError as e:
            seen["key"] = str(e)
        return {}

    TestClient(app).get("/x")
    assert calls == [] and seen == {"err": "API call without tenant store", "key": "API call without tenant store"}
    assert panel.VENDORS["openai-api"].key(tmp_path) == OP_KEY  # outside the API: unchanged


def test_create_app_installs_the_serving_api_middleware(tmp_path):
    from pravrudhi.api.server import create_app
    from pravrudhi.application.credentials import ServingApiMiddleware

    assert any(m.cls is ServingApiMiddleware for m in create_app(tmp_path).user_middleware)


def test_reachable_in_for_a_tenant_never_reports_the_operator_env(tmp_path, engine):
    ok, why = panel.VENDORS["openai-api"].reachable_in(tmp_path, store=tenant_store(tmp_path))
    assert not ok and "environment" not in why.split("none of")[0]
