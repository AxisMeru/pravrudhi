"""#388: the vendor allowlist for API callers. Default-closed, cli vendors never, config not code, CLI untouched."""

from __future__ import annotations

import pytest

from pravrudhi.api.identity import User
from pravrudhi.application import nyaya, panel, tenant_vendors
from pravrudhi.application.credentials import serving_api, serving_org, store_for_project
from pravrudhi.application.tenant_vendors import VendorNotAllowed

USER = User(id="u1", email="t@example.com", role="authenticated")


@pytest.fixture
def api():
    t = serving_api.set(True)
    yield
    serving_api.reset(t)


@pytest.fixture
def calls(monkeypatch):
    import pravrudhi.agents.cli_agents as ca
    import pravrudhi.models.openai_compat as oc

    seen: list[str] = []
    monkeypatch.setattr(ca, "_run", lambda *a, **k: seen.append("cli") or (0, "x", ""))
    monkeypatch.setattr(oc.ChatClient, "chat", lambda *a, **k: seen.append("api"))
    return seen


def cfg(tmp_path, text):
    p = tmp_path / "tv.yaml"
    p.write_text(text)
    return p


def store(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    return store_for_project(ws, engine_root=tmp_path / "engine", user=USER)


@pytest.mark.parametrize("vid", ["claude-cli", "codex-cli"])
def test_anonymous_api_caller_cannot_use_a_cli_vendor_even_if_requested(tmp_path, api, calls, vid):
    with pytest.raises(VendorNotAllowed):
        panel.ask_vendor(panel.VENDORS[vid], "p", root=tmp_path, store=store(tmp_path))
    assert calls == []


def test_unlisted_vendor_is_refused_with_zero_calls(tmp_path, api, calls):
    with pytest.raises(VendorNotAllowed):
        panel.ask_vendor(panel.VENDORS["glm-local"], "p", root=tmp_path, store=store(tmp_path))
    assert calls == []


def test_listed_vendor_passes_the_gate(tmp_path, api):
    tenant_vendors.require("openai-api")


def test_cli_vendor_in_the_config_is_a_load_error_not_an_opt_in(tmp_path):
    p = cfg(tmp_path, "default: [openai-api, claude-cli]\n")
    assert tenant_vendors.allowed_ids(path=p) == frozenset()
    p = cfg(tmp_path, "default: [openai-api]\norgs:\n  acme:\n    extend: [codex-cli]\n")
    assert tenant_vendors.allowed_ids("acme", path=p) == frozenset()
    assert tenant_vendors.allowed_ids("other", path=p) == {"openai-api"}


def test_org_override_is_honoured_and_only_for_that_org(tmp_path):
    p = cfg(tmp_path, "default: [openai-api]\norgs:\n  acme:\n    extend: [qwen-dashscope]\n  solo:\n    allow: [google-api]\n")
    assert tenant_vendors.allowed_ids(None, path=p) == {"openai-api"}
    assert tenant_vendors.allowed_ids("acme", path=p) == {"openai-api", "qwen-dashscope"}
    assert tenant_vendors.allowed_ids("solo", path=p) == {"google-api"}
    assert tenant_vendors.allowed_ids("unknown", path=p) == {"openai-api"}


def test_org_override_reaches_ask_vendor_through_serving_org(tmp_path, api, monkeypatch):
    p = cfg(tmp_path, "default: [openai-api]\norgs:\n  acme:\n    extend: [qwen-dashscope]\n")
    monkeypatch.setattr(tenant_vendors, "_default_path", lambda: p)
    with pytest.raises(VendorNotAllowed):
        tenant_vendors.require("qwen-dashscope", serving_org.get())
    t = serving_org.set("acme")
    try:
        tenant_vendors.require("qwen-dashscope", serving_org.get())
    finally:
        serving_org.reset(t)


@pytest.mark.parametrize("text", [None, "", "- a\n- b\n", "default: openai-api\n", "default: [1, 2]\n",
                                  "default: [openai-api]\norgs: [x]\n", "default: [x\n",
                                  "default: [openai-api]\norgs:\n  acme: {extend: oops}\n"])
def test_missing_or_malformed_config_means_closed(tmp_path, text):
    p = tmp_path / "absent.yaml" if text is None else cfg(tmp_path, text)
    assert tenant_vendors.allowed_ids("acme", path=p) == frozenset()


def test_missing_config_refuses_ask_vendor(tmp_path, api, calls, monkeypatch):
    monkeypatch.setattr(tenant_vendors, "_default_path", lambda: tmp_path / "absent.yaml")
    with pytest.raises(VendorNotAllowed):
        panel.ask_vendor(panel.VENDORS["openai-api"], "p", root=tmp_path, store=store(tmp_path))
    assert calls == []


def test_shipped_config_default_lists_no_cli_vendor_and_is_nonempty():
    ids = tenant_vendors.allowed_ids()
    assert ids and not any(panel.VENDORS[i].interface == "cli" for i in ids if i in panel.VENDORS)


def test_a_tenants_own_root_cannot_supply_an_allowlist(tmp_path, api, calls):
    (tmp_path / "configs").mkdir()
    (tmp_path / "configs" / "tenant_vendors.yaml").write_text("default: [claude-cli, glm-local]\n")
    with pytest.raises(VendorNotAllowed):
        panel.ask_vendor(panel.VENDORS["glm-local"], "p", root=tmp_path, store=store(tmp_path))
    assert calls == []


def test_available_vendors_shows_only_allowed_and_keyed_to_an_api_caller(tmp_path, api):
    s = store(tmp_path)
    s.put("openai", "sk-tn-" + "t" * 30)
    out = nyaya.available_vendors(tmp_path, tuple(panel.VENDORS), store=s)
    assert {v["id"] for v in out} <= tenant_vendors.allowed_ids()
    assert "claude-cli" not in {v["id"] for v in out} and "codex-cli" not in {v["id"] for v in out}
    by = {v["id"]: v for v in out}
    assert by["openai-api"]["available"] is True and by["anthropic-api"]["available"] is False


def test_cli_is_unaffected_outside_the_api_context(tmp_path, calls, monkeypatch):
    monkeypatch.setattr(panel, "_claude_cli_env", lambda: {})
    assert not serving_api.get()
    out = nyaya.available_vendors(tmp_path, ("claude-cli", "codex-cli", "qwen-dashscope"))
    assert [v["id"] for v in out] == ["claude-cli", "codex-cli", "qwen-dashscope"]
    try:
        panel.ask_vendor(panel.VENDORS["claude-cli"], "p", root=tmp_path)
    except Exception as e:  # noqa: BLE001 -- only whether the gate let the call through matters here
        assert not isinstance(e, VendorNotAllowed)
    assert calls == ["cli"]


def test_providers_vendors_route_hides_unallowed_vendors(tmp_path):
    from fastapi.testclient import TestClient

    from pravrudhi.api.server import create_app

    app = create_app(tmp_path)
    r = TestClient(app).get("/api/panel/vendors")
    if r.status_code == 200:
        ids = {v["id"] for v in r.json()}
        assert ids <= tenant_vendors.allowed_ids()


# --- edition-keyed cli carve-out: the deployment decides, never the request ---

ADMIN = User(id="admin", email="op@example.com", role="admin")


@pytest.fixture
def edition(monkeypatch):
    def set_(v):
        if v is None:
            monkeypatch.delenv("PRAVRUDHI_EDITION", raising=False)
        else:
            monkeypatch.setenv("PRAVRUDHI_EDITION", v)

    monkeypatch.delenv("PRAVRUDHI_STUDIO_LOOPBACK_ONLY", raising=False)
    monkeypatch.setattr(tenant_vendors, "_bind_host", "127.0.0.1")
    return set_


@pytest.mark.parametrize("vid", ["claude-cli", "codex-cli"])
def test_product_edition_refuses_cli_even_for_an_admin_caller(tmp_path, api, calls, edition, vid, monkeypatch):
    monkeypatch.setenv("PRAVRUDHI_ADMINS", ADMIN.email)
    edition("product")
    from pravrudhi.api.roles import ADMIN as ADMIN_ROLE
    from pravrudhi.api.roles import role_of

    assert role_of(ADMIN) is ADMIN_ROLE
    with pytest.raises(VendorNotAllowed):
        panel.ask_vendor(panel.VENDORS[vid], "x", root=tmp_path, store=store(tmp_path))
    assert calls == []
    assert vid not in tenant_vendors.allowed_ids()


@pytest.mark.parametrize("vid", ["claude-cli", "codex-cli"])
def test_studio_edition_allows_cli_vendors_from_the_studio_section(edition, vid):
    edition("studio")
    assert vid in tenant_vendors.allowed_ids()
    tenant_vendors.require(vid)


@pytest.mark.parametrize("declared", [None, "", "Studi0", "staging", "both", "prod"])
def test_missing_or_unknown_edition_is_product_so_closed(edition, declared):
    edition(declared)
    assert not tenant_vendors.is_studio_edition()
    assert not {"claude-cli", "codex-cli"} & tenant_vendors.allowed_ids()


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.5", "example.com", "", None])
def test_studio_env_on_a_non_loopback_or_unknown_bind_is_closed(edition, monkeypatch, host):
    edition("studio")
    monkeypatch.setattr(tenant_vendors, "_bind_host", host)
    assert not {"claude-cli", "codex-cli"} & tenant_vendors.allowed_ids()


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "localhost"])
def test_studio_on_loopback_allows_and_product_on_loopback_refuses(edition, monkeypatch, host):
    monkeypatch.setattr(tenant_vendors, "_bind_host", host)
    edition("studio")
    assert {"claude-cli", "codex-cli"} <= tenant_vendors.allowed_ids()
    edition("product")
    assert not {"claude-cli", "codex-cli"} & tenant_vendors.allowed_ids()


def test_container_asserts_loopback_only_because_it_binds_all_interfaces_inside(edition, monkeypatch):
    edition("studio")
    monkeypatch.setattr(tenant_vendors, "_bind_host", "0.0.0.0")
    monkeypatch.setenv("PRAVRUDHI_STUDIO_LOOPBACK_ONLY", "1")
    assert {"claude-cli", "codex-cli"} <= tenant_vendors.allowed_ids()
    edition("product")  # the assertion alone never makes a product engine Studio
    assert not {"claude-cli", "codex-cli"} & tenant_vendors.allowed_ids()
    monkeypatch.setenv("PRAVRUDHI_STUDIO_LOOPBACK_ONLY", "true")  # only the exact value counts
    edition("studio")
    assert not {"claude-cli", "codex-cli"} & tenant_vendors.allowed_ids()


def test_serve_entrypoints_record_the_bind_host(monkeypatch, tmp_path):
    import sys
    import types

    from pravrudhi.api import server
    from pravrudhi.application import app_serve

    fake = types.SimpleNamespace(run=lambda *a, **k: None)
    monkeypatch.setitem(sys.modules, "uvicorn", fake)
    monkeypatch.setattr(server, "create_app", lambda root: None)
    monkeypatch.setattr(app_serve, "build_app", lambda root: None)
    monkeypatch.setattr(tenant_vendors, "_bind_host", None)
    server.serve(tmp_path, host="0.0.0.0")
    assert tenant_vendors._bind_host == "0.0.0.0"
    app_serve.serve(tmp_path, host="127.0.0.1", open_browser=False)
    assert tenant_vendors._bind_host == "127.0.0.1"


def test_studio_section_is_ignored_in_product_and_cli_in_default_still_closes(tmp_path, edition):
    p = cfg(tmp_path, "default: [openai-api]\nstudio: [claude-cli]\n")
    edition("product")
    assert tenant_vendors.allowed_ids(path=p) == {"openai-api"}
    edition("studio")
    assert tenant_vendors.allowed_ids(path=p) == {"openai-api", "claude-cli"}
    bad = cfg(tmp_path, "default: [openai-api, claude-cli]\nstudio: [claude-cli]\n")
    assert tenant_vendors.allowed_ids(path=bad) == frozenset()


@pytest.mark.parametrize("studio", ["claude-cli", "{a: 1}", "[1, 2]"])
def test_malformed_studio_section_closes_everything_in_studio(tmp_path, edition, studio):
    edition("studio")
    assert tenant_vendors.allowed_ids(path=cfg(tmp_path, f"default: [openai-api]\nstudio: {studio}\n")) == frozenset()


def test_shipped_config_studio_section_lists_exactly_the_cli_vendors(edition):
    import yaml

    shipped = yaml.safe_load(tenant_vendors._default_path().read_text())
    assert set(shipped["studio"]) == {"claude-cli", "codex-cli"}
    edition("product")
    assert not {"claude-cli", "codex-cli"} & tenant_vendors.allowed_ids()
