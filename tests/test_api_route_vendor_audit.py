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
    """Transitive, name-based reachability from a route endpoint. Free functions and classes resolve through the
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
