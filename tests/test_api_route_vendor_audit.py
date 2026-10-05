"""Route audit for the tenant-key and allowlist fixes (#387, #388).

Two guarantees:
1. Every route of `create_app` that can reach `ask_vendor`, `available_vendors`, `Vendor.reachable_in` or
   `Vendor.key` is listed in `GUARDED_ROUTES`. A new route that reaches them without being listed fails the
   static test, so a future route cannot skip the guard unnoticed.
2. Every listed route, called with the operator's env keys set and an empty tenant store, as an anonymous,
   a signed-in and a partner-key caller, makes zero vendor calls and never reports the operator's key.
"""

from __future__ import annotations

import ast
import importlib
import inspect
import pkgutil
import textwrap
import types
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

import pravrudhi
from pravrudhi.api import identity
from pravrudhi.api.identity import User
from pravrudhi.api.localguard import TOKEN_HEADER, app_token
from pravrudhi.api.server import create_app
from pravrudhi.application import tenancy

OP_KEY = "sk-op-" + "o" * 30
TARGETS = frozenset({"ask_vendor", "available_vendors", "reachable_in", "key"})

# (method, path) -> request kwargs for TestClient.request
GUARDED_ROUTES: dict[tuple[str, str], dict[str, Any]] = {
    ("GET", "/api/panel/vendors"): {},
    ("GET", "/api/nyaya/vendors"): {},
    ("POST", "/api/nyaya/ask"): {"json": {"question": "q?", "vendors": ["openai-api", "anthropic-api", "google-api"]}},
    ("POST", "/api/nyaya/audit"): {"json": {"sources": "s", "answer": "a", "checker": "openai-api"}},
}

SIGNED_IN = User(id="u1", email="t@example.com", role="authenticated")


def _routes(routes: Any) -> Iterator[APIRoute]:
    for r in routes:
        if isinstance(r, APIRoute):
            yield r
        elif type(r).__name__ == "_IncludedRouter":
            yield from _routes(r.original_router.routes)


def _is_pravrudhi(obj: object) -> bool:
    return (getattr(obj, "__module__", None) or "").startswith("pravrudhi")


def _called_names(fn: Any) -> tuple[ast.AST | None, set[str]]:
    try:
        tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
    except (OSError, TypeError):
        return None, set()
    names = {
        c.func.attr if isinstance(c.func, ast.Attribute) else c.func.id
        for c in ast.walk(tree)
        if isinstance(c, ast.Call) and isinstance(c.func, (ast.Attribute, ast.Name))
    }
    return tree, names


def _method_index() -> dict[str, set[types.FunctionType]]:
    index: dict[str, set[types.FunctionType]] = {}
    for info in pkgutil.walk_packages(pravrudhi.__path__, "pravrudhi."):
        if info.name.endswith("__main__") or ".cli" in info.name:
            continue
        mod = importlib.import_module(info.name)
        for cls in vars(mod).values():
            if isinstance(cls, type) and cls.__module__ == info.name:
                for k, v in vars(cls).items():
                    if isinstance(v, types.FunctionType):
                        index.setdefault(k, set()).add(v)
    return index


def _reaches_targets(endpoint: Any, methods: dict[str, set[types.FunctionType]]) -> set[str]:
    """LIMIT, NOT COMPLETE COVERAGE: this is a name-based static walk, not a dispatch-aware one. A method call on
    an instance (`agent.run(...)`, `judge.judge(...)`) is followed only when exactly one class in pravrudhi
    defines that method name, so any route that reaches vendor code through an ambiguous method name is
    invisible here. The routes this cannot see are covered by hand: `/api/v1/analyse-facts` is exercised
    behaviourally in `test_analyse_facts_*` below. Treat a green static test as "no NEW direct path", never
    as proof that no path exists.

    Transitive, name-based reachability from a route endpoint. Free functions and classes resolve through the
    function's globals and closure; a method call on an instance is followed only when exactly one class in
    pravrudhi defines that name (an ambiguous name would drag in every route, so it is not followed)."""
    seen: set[Any] = set()
    stack = [endpoint]
    hits: set[str] = set()
    while stack:
        fn = stack.pop()
        if fn in seen:
            continue
        seen.add(fn)
        if fn.__name__ in TARGETS:
            hits.add(fn.__name__)
        tree, called = _called_names(fn)
        if tree is None:
            continue
        hits |= called & TARGETS
        env = {**fn.__globals__, **inspect.getclosurevars(fn).nonlocals}
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id in env:
                obj = env[node.id]
                if isinstance(obj, types.FunctionType) and _is_pravrudhi(obj):
                    stack.append(obj)
                elif isinstance(obj, type) and _is_pravrudhi(obj):
                    stack.extend(v for v in vars(obj).values() if isinstance(v, types.FunctionType))
            elif isinstance(node, ast.Attribute):
                base = node.value
                if isinstance(base, ast.Name) and isinstance(env.get(base.id), types.ModuleType):
                    obj = getattr(env[base.id], node.attr, None)
                    if isinstance(obj, types.FunctionType) and _is_pravrudhi(obj):
                        stack.append(obj)
        for name in called:
            candidates = methods.get(name, set())
            if len(candidates) == 1:
                stack.extend(candidates)
    return hits


@pytest.fixture
def engine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    eng = tmp_path / "engine"
    eng.mkdir()
    for v in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GOOGLE_API_KEY", "DASHSCOPE_API_KEY"):
        monkeypatch.setenv(v, OP_KEY)
    return eng


def test_every_route_reaching_vendor_key_code_is_listed(engine: Path) -> None:
    methods = _method_index()
    reached = {
        (m, r.path) for r in _routes(create_app(engine).routes) if _reaches_targets(r.endpoint, methods) for m in r.methods
    }
    unlisted = reached - GUARDED_ROUTES.keys()
    stale = GUARDED_ROUTES.keys() - reached
    assert not unlisted, (
        f"route(s) reach ask_vendor/available_vendors/reachable_in/Vendor.key but are not in GUARDED_ROUTES: "
        f"{sorted(unlisted)}. Resolve the key from the tenant store only, then list the route with a request body."
    )
    assert not stale, f"GUARDED_ROUTES lists route(s) that no longer reach those functions: {sorted(stale)}"


def _client(engine: Path, caller: str) -> tuple[TestClient, dict[str, str]]:
    app = create_app(engine)
    headers: dict[str, str] = {TOKEN_HEADER: app_token(engine)}
    if caller == "signed-in":
        app.dependency_overrides[identity.current_user] = lambda: SIGNED_IN
    elif caller == "partner-key":
        tenancy.create_org(engine, "acme", "Acme")
        headers[tenancy.API_KEY_HEADER] = tenancy.create_key(engine, "acme", label="t").secret
    return TestClient(app, base_url="http://localhost"), headers


@pytest.mark.parametrize("caller", ["anonymous", "signed-in", "partner-key"])
@pytest.mark.parametrize("route", sorted(GUARDED_ROUTES), ids=lambda r: f"{r[0]} {r[1]}")
def test_guarded_route_makes_no_vendor_call_and_never_reports_the_operator_key(
    engine: Path, monkeypatch: pytest.MonkeyPatch, route: tuple[str, str], caller: str
) -> None:
    import pravrudhi.models.openai_compat as oc

    calls: list[int] = []
    monkeypatch.setattr(oc.ChatClient, "chat", lambda *a, **k: calls.append(1))
    client, headers = _client(engine, caller)
    method, path = route
    params = {"workspace": "w1"} if caller == "signed-in" else {}
    r = client.request(method, path, headers=headers, params=params, **GUARDED_ROUTES[route])
    assert r.status_code not in (400, 401, 404, 405, 421), f"the request never reached the route: {r.status_code} {r.text}"
    assert calls == []
    assert OP_KEY not in r.text
    assert "key in environment" not in r.text


# -- /api/v1/analyse-facts: reached through instance-method dispatch the static walk cannot follow -------------


def _partner_client(engine: Path, agent: Any, caller: str) -> tuple[TestClient, dict[str, str]]:
    from fastapi import FastAPI

    from pravrudhi.api.partner import PartnerApiConfig, build_partner_router
    from pravrudhi.application.credentials import ServingApiMiddleware

    app = FastAPI()
    app.add_middleware(ServingApiMiddleware)
    cfg = PartnerApiConfig(rate_limit_per_minute=1000, max_concurrent=100, trust_proxy_header=False)
    app.include_router(build_partner_router(engine, agent_factory=lambda _root: agent, config=cfg))
    headers: dict[str, str] = {}
    if caller == "partner-key":
        tenancy.create_org(engine, "acme", "Acme")
        headers[tenancy.API_KEY_HEADER] = tenancy.create_key(engine, "acme", label="t").secret
    return TestClient(app, base_url="http://localhost", raise_server_exceptions=False), headers


def _frontier_agent(tmp_path: Path) -> Any:
    """An agent whose judge is a `FrontierJudge` over the real `panel.ask_vendor` with NO store handed in: the
    shape that would reach a vendor with the operator's key if the serving-API guard were absent. No config
    builds this today (`NyayaAgent.house` only builds `HouseJudge`); the test pins that a future one is safe."""
    from pravrudhi.application import panel
    from pravrudhi.application.nyaya_agent import AgentConfig, NyayaAgent
    from pravrudhi.application.nyaya_judges import FrontierJudge
    from tests.test_api_partner import ScriptedRegistry

    config = AgentConfig(
        tau=0.74,
        refer_band=(0.5, 0.74),
        max_retries=1,
        audit_dir=tmp_path / "audit",
        judge_statute_text={"bns69": "TRAINING statute text for bns69"},
        validated_contracts=frozenset({"bns69"}),
    )
    return NyayaAgent(FrontierJudge(panel.VENDORS["openai-api"], root=tmp_path), ScriptedRegistry(), config)


@pytest.mark.parametrize("caller", ["anonymous", "partner-key"])
def test_analyse_facts_with_a_frontier_judge_makes_no_vendor_call(
    tmp_path: Path, engine: Path, monkeypatch: pytest.MonkeyPatch, caller: str
) -> None:
    import pravrudhi.models.openai_compat as oc
    from tests.test_api_partner import _req

    calls: list[int] = []
    monkeypatch.setattr(oc.ChatClient, "chat", lambda *a, **k: calls.append(1))
    client, headers = _partner_client(engine, _frontier_agent(tmp_path), caller)
    r = client.post("/api/v1/analyse-facts", json=_req(), headers=headers)
    assert r.status_code not in (404, 405, 421), r.text
    assert calls == []
    assert OP_KEY not in r.text
    assert "key in environment" not in r.text


@pytest.mark.parametrize("caller", ["anonymous", "partner-key"])
def test_analyse_facts_house_judge_path_still_works_with_fake_judges(tmp_path: Path, engine: Path, caller: str) -> None:
    from tests.test_api_partner import _agent, _proof_script, _req

    client, headers = _partner_client(engine, _agent(tmp_path, _proof_script()), caller)
    r = client.post("/api/v1/analyse-facts", json=_req(), headers=headers)
    assert r.status_code == 200, r.text
    assert OP_KEY not in r.text


# -- /api/nyaya/ask runs each vendor in a pool thread: the guards must hold there too (R2, #227) -----------------

CLI_ASK = {"question": "q?", "vendors": ["claude-cli", "codex-cli"]}


@pytest.fixture
def cli_calls(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    import pravrudhi.agents.cli_agents as ca
    from pravrudhi.application import tenant_vendors

    seen: list[list[str]] = []
    monkeypatch.setattr(ca, "_run", lambda cmd, *a, **k: seen.append(cmd) or (0, "answer", "", 0.1))
    monkeypatch.setattr(tenant_vendors, "_bind_host", None)
    monkeypatch.delenv("PRAVRUDHI_EDITION", raising=False)
    monkeypatch.delenv("PRAVRUDHI_STUDIO_LOOPBACK_ONLY", raising=False)
    return seen


@pytest.mark.parametrize("caller", ["anonymous", "signed-in", "partner-key"])
@pytest.mark.parametrize("edition", ["product", "studio-without-loopback"])
def test_ask_with_cli_vendors_is_refused_and_runs_nothing(
    engine: Path, monkeypatch: pytest.MonkeyPatch, cli_calls: list[list[str]], caller: str, edition: str
) -> None:
    if edition != "product":
        monkeypatch.setenv("PRAVRUDHI_EDITION", "studio")
    client, headers = _client(engine, caller)
    params = {"workspace": "w1"} if caller == "signed-in" else {}
    r = client.post("/api/nyaya/ask", headers=headers, params=params, json=CLI_ASK)
    assert r.status_code == 403, r.text
    assert "vendor not allowed" in r.text
    assert cli_calls == []


@pytest.mark.parametrize("caller", ["anonymous", "signed-in", "partner-key"])
def test_ask_with_a_cli_vendor_runs_only_in_the_loopback_studio(
    engine: Path, monkeypatch: pytest.MonkeyPatch, cli_calls: list[list[str]], caller: str
) -> None:
    monkeypatch.setenv("PRAVRUDHI_EDITION", "studio")
    monkeypatch.setenv("PRAVRUDHI_STUDIO_LOOPBACK_ONLY", "1")
    client, headers = _client(engine, caller)
    params = {"workspace": "w1"} if caller == "signed-in" else {}
    r = client.post("/api/nyaya/ask", headers=headers, params=params, json={"question": "q?", "vendors": ["codex-cli"]})
    assert r.status_code == 200, r.text
    assert [c[0] for c in cli_calls] == ["codex"]


def test_a_pool_thread_inherits_the_serving_guards(engine: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The thread case: a worker sees the request's serving ContextVars, and with no store a BYOK vendor in the
    worker never falls back to the operator key."""
    import pravrudhi.models.openai_compat as oc
    from pravrudhi.application import nyaya, panel
    from pravrudhi.application.credentials import serving_api, serving_org

    seen: list[tuple[bool, str | None]] = []

    def probe(v: panel.Vendor, p: str) -> panel.Answer:
        seen.append((serving_api.get(), serving_org.get()))
        return panel.Answer(v.id, v.interface, v.model, "", "answer", 0.1, None, None)

    chat: list[int] = []
    monkeypatch.setattr(oc.ChatClient, "chat", lambda *a, **k: chat.append(1))
    t1, t2 = serving_api.set(True), serving_org.set("acme")
    try:
        nyaya.ask(engine, "q?", ("openai-api", "anthropic-api"), ask_fn=probe)
        assert seen == [(True, "acme")] * 2
        rec = nyaya.ask(engine, "q?", ("openai-api",), store=None)
    finally:
        serving_org.reset(t2)
        serving_api.reset(t1)
    assert chat == [] and rec.answers[0].error and OP_KEY not in rec.answers[0].error
