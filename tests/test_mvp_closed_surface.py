"""Legal design-partner MVP (Lead-2 decision on #525, mode B, 6 Oct 2026): a surface the product hides is CLOSED.

Every route in `roles.LEGAL_MVP_CLOSED` answers an anonymous caller 401, a signed-in non-admin 403, and passes the
operator, in BOTH editions; with `PRAVRUDHI_ADMINS` unset nobody passes (fail closed); with authentication off the
single-operator install keeps working. The legal surface stays member-facing. Org keys and usage (checklist C9, C10)
and the schema and docs pages (C11) are proven here instead of being read from comments.

Admin probes send an invalid body or a non-existent id, so nothing is started, written or sent to a vendor."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from pravrudhi.api import identity, roles
from pravrudhi.api.localguard import TOKEN_HEADER, app_token
from pravrudhi.api.server import create_app

TOKENS = {
    "admin": {"sub": "op-1", "email": "op@example.com", "role": "authenticated"},
    "plain": {"sub": "u-2", "email": "someone@example.com", "role": "authenticated"},
}
EDITIONS = ["product", "studio"]
GATE_REFUSAL = "This surface belongs to the engine's operator."  # roles.require_admin's refusal, said by no handler

# (method, path as registered, concrete path to call, body). One row per (method, route) of LEGAL_MVP_CLOSED.
CLOSED_CALLS: list[tuple[str, str, str, Any]] = [
    ("GET", "/api/providers", "/api/providers", None),
    ("POST", "/api/providers/{provider_id}/key", "/api/providers/no-such-provider/key", {"key": "x"}),
    ("DELETE", "/api/providers/{provider_id}/key", "/api/providers/no-such-provider/key", None),
    ("POST", "/api/chat", "/api/chat", {}),
    ("POST", "/api/chat/stream", "/api/chat/stream", {}),
    ("GET", "/api/chat/threads", "/api/chat/threads", None),
    ("GET", "/api/chat/threads/{thread_id}", "/api/chat/threads/no-such-thread", None),
    ("GET", "/api/memory", "/api/memory", None),
    ("POST", "/api/memory/notes", "/api/memory/notes", {}),
    ("PATCH", "/api/memory/notes/{note_id}", "/api/memory/notes/no-such-note", {}),
    ("DELETE", "/api/memory/notes/{note_id}", "/api/memory/notes/no-such-note", None),
    ("GET", "/api/objectives", "/api/objectives", None),
    ("POST", "/api/objectives", "/api/objectives", {}),
    ("POST", "/api/objectives/plan-preview", "/api/objectives/plan-preview", {}),
    ("GET", "/api/objectives/{oid}", "/api/objectives/no-such-objective", None),
    ("GET", "/api/objectives/{oid}/loom", "/api/objectives/no-such-objective/loom", None),
    ("GET", "/api/objectives/{oid}/plan", "/api/objectives/no-such-objective/plan", None),
    ("GET", "/api/objectives/{oid}/subagents", "/api/objectives/no-such-objective/subagents", None),
    ("POST", "/api/objectives/{oid}/subagents", "/api/objectives/no-such-objective/subagents", None),
    ("GET", "/api/models", "/api/models", None),
    ("GET", "/api/recipes", "/api/recipes", None),
    ("GET", "/api/tools", "/api/tools", None),
    ("GET", "/api/panel/vendors", "/api/panel/vendors", None),
    ("POST", "/api/nyaya/ask", "/api/nyaya/ask", {}),
    ("GET", "/api/nyaya/asks", "/api/nyaya/asks", None),
    ("POST", "/api/nyaya/audit", "/api/nyaya/audit", {}),
    ("GET", "/api/nyaya/vendors", "/api/nyaya/vendors", None),
    ("GET", "/api/nyaya/registry/{contract_id}/elements", "/api/nyaya/registry/no-such-contract/elements", None),
    ("POST", "/api/nyaya/registry/check", "/api/nyaya/registry/check", {}),
    # R2's #300 review: seven more hide-list routes, two of which disclosed host details to any member.
    ("GET", "/api/doctor", "/api/doctor", None),
    ("GET", "/api/workspaces", "/api/workspaces", None),
    ("POST", "/api/workspaces", "/api/workspaces", {}),
    ("GET", "/api/notifications", "/api/notifications", None),
    ("POST", "/api/notifications/read", "/api/notifications/read", {}),
    ("GET", "/api/update", "/api/update", None),
    ("GET", "/api/update/config", "/api/update/config", None),
    ("PUT", "/api/update/config", "/api/update/config", {}),
    ("GET", "/api/update/last-check", "/api/update/last-check", None),
]

# What stays open to a signed-in member: the legal surface and the plain identity and health routes.
MEMBER_OPEN = [
    ("GET", "/api/nyaya/corpus"),
    ("GET", "/api/nyaya/registry/contracts"),
    ("GET", "/api/me"),
    ("GET", "/api/status"),
    ("GET", "/api/v1/status"),
]


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    for v in ("PRAVRUDHI_EDITION", "PRAVRUDHI_STUDIO_LOOPBACK_ONLY", "PRAVRUDHI_IDENTITY_HEADER"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("PRAVRUDHI_AUTH", "required")
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("PRAVRUDHI_ADMINS", "op-1")
    monkeypatch.setattr(identity, "verify_token", lambda token, **_k: TOKENS[token])


def _client(root: Path, edition: str, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("PRAVRUDHI_EDITION", edition)
    return TestClient(create_app(root), base_url="http://localhost", raise_server_exceptions=False)


def _call(c: TestClient, root: Path, method: str, path: str, body: Any, who: str | None) -> Any:
    headers = {TOKEN_HEADER: app_token(root)}
    if who:
        headers["Authorization"] = f"Bearer {who}"
    return c.request(method, path, headers=headers, json=body)


def test_every_closed_route_and_method_has_a_probe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A route added to LEGAL_MVP_CLOSED (or a method added to one) fails here until it has a 401/403/pass row."""
    app = _client(tmp_path, "product", monkeypatch).app
    registered: set[tuple[str, str]] = set()

    def walk(routes: Any) -> None:
        for r in routes:
            if isinstance(r, APIRoute):
                if r.path in roles.LEGAL_MVP_CLOSED:
                    registered.update((m, r.path) for m in (r.methods or set()) - {"HEAD", "OPTIONS"})
            else:
                sub = getattr(r, "original_router", None)
                if sub is not None:
                    walk(sub.routes)

    walk(app.routes)  # type: ignore[attr-defined]
    probed = {(m, p) for m, p, _c, _b in CLOSED_CALLS}
    assert {p for _m, p in registered} == set(roles.LEGAL_MVP_CLOSED), "a closed path is not registered on the app"
    assert registered == probed, (sorted(registered - probed), sorted(probed - registered))


@pytest.mark.parametrize("edition", EDITIONS)
@pytest.mark.parametrize(("method", "route", "path", "body"), CLOSED_CALLS)
def test_closed_route_anonymous_401_member_403_admin_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, edition: str, method: str, route: str, path: str, body: Any
) -> None:
    c = _client(tmp_path, edition, monkeypatch)
    assert _call(c, tmp_path, method, path, body, None).status_code == 401, "anonymous"
    assert _call(c, tmp_path, method, path, body, "plain").status_code == 403, "signed-in non-admin"
    admin = _call(c, tmp_path, method, path, body, "admin")
    # "Passes the gate" = not the gate's own refusal: a handler may still answer 4xx for the invalid probe body or the
    # unknown id (or refuse a signed-in caller who names no workspace), and that is past the gate.
    assert admin.status_code != 401 and GATE_REFUSAL not in admin.text, f"the operator must pass the gate: {admin.status_code}"


@pytest.mark.parametrize("edition", EDITIONS)
@pytest.mark.parametrize(("method", "route", "path", "body"), CLOSED_CALLS)
def test_closed_route_with_no_operator_named_refuses_everyone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, edition: str, method: str, route: str, path: str, body: Any
) -> None:
    """Fail closed: an unset or empty PRAVRUDHI_ADMINS names nobody, so even the usual admin token is refused."""
    monkeypatch.delenv("PRAVRUDHI_ADMINS", raising=False)
    c = _client(tmp_path, edition, monkeypatch)
    assert _call(c, tmp_path, method, path, body, "admin").status_code == 403
    assert _call(c, tmp_path, method, path, body, "plain").status_code == 403


@pytest.mark.parametrize(("method", "route", "path", "body"), CLOSED_CALLS)
def test_a_refused_member_call_reaches_no_handler(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, method: str, route: str, path: str, body: Any
) -> None:
    """The 403 comes from the gate, before the handler: nothing is written under the engine root."""
    c = _client(tmp_path, "product", monkeypatch)
    app_token(tmp_path)  # the local guard's own token file is created on first use; take the snapshot after it
    before = {p.relative_to(tmp_path): p.stat().st_size for p in tmp_path.rglob("*") if p.is_file()}
    r = _call(c, tmp_path, method, path, body, "plain")
    assert r.status_code == 403 and GATE_REFUSAL in r.text
    after = {p.relative_to(tmp_path): p.stat().st_size for p in tmp_path.rglob("*") if p.is_file()}
    assert after == before


@pytest.mark.parametrize("edition", EDITIONS)
@pytest.mark.parametrize(("method", "path"), MEMBER_OPEN)
def test_the_legal_surface_stays_open_to_a_signed_in_member(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, edition: str, method: str, path: str
) -> None:
    if edition == "studio":
        pytest.skip("Studio admits administrators only as a whole (#246); the member surface is the product's")
    c = _client(tmp_path, edition, monkeypatch)
    status = _call(c, tmp_path, method, path, None, "plain").status_code
    assert status not in (401, 403), f"{path} must stay member-facing, got {status}"


def test_the_single_operator_install_keeps_working_with_authentication_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PRAVRUDHI_AUTH", "disabled")
    c = _client(tmp_path, "product", monkeypatch)
    for method, _route, path, body in CLOSED_CALLS:
        status = _call(c, tmp_path, method, path, body, None).status_code
        assert status not in (401, 403), (method, path, status)


# -- C9, C10: org keys and usage are org-admin only ---------------------------------------------------------------

ORG_CALLS = [
    ("POST", "/api/v1/orgs", {"org_id": "probe-org", "name": "Probe"}),
    ("POST", "/api/v1/orgs/org-x/keys", {"label": "probe"}),
    ("GET", "/api/v1/orgs/org-x/keys", None),
    ("POST", "/api/v1/orgs/org-x/keys/key-x/revoke", None),
    ("GET", "/api/v1/orgs/org-x/usage", None),
    ("GET", "/api/v1/orgs/org-x/usage/summary", None),
]


@pytest.mark.parametrize("edition", ["product"])
@pytest.mark.parametrize(("method", "path", "body"), ORG_CALLS)
def test_org_keys_and_usage_are_refused_to_a_member_and_open_to_the_operator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, edition: str, method: str, path: str, body: Any
) -> None:
    monkeypatch.delenv("PRAVRUDHI_TENANCY_PROVISION_SECRET", raising=False)
    # A source checkout ships no packaged partner_api.yaml; the engine reads the root's own copy then.
    (tmp_path / "configs").mkdir()
    shipped = Path(__file__).resolve().parents[1] / "configs" / "partner_api.yaml"
    (tmp_path / "configs" / "partner_api.yaml").write_text(shipped.read_text())
    c = _client(tmp_path, edition, monkeypatch)
    assert _call(c, tmp_path, method, path, body, None).status_code in (401, 403), "anonymous"
    assert _call(c, tmp_path, method, path, body, "plain").status_code == 403, "signed-in non-admin"
    admin = _call(c, tmp_path, method, path, body, "admin")
    assert admin.status_code != 401, f"an allowlisted operator reaches it (got {admin.status_code})"
    assert admin.status_code != 403 or "allowlisted admin" not in admin.text


# -- C11: the schema and docs pages ---------------------------------------------------------------------------------


@pytest.mark.parametrize("edition", EDITIONS)
@pytest.mark.parametrize("path", ["/openapi.json", "/docs", "/redoc"])
def test_schema_and_docs_are_closed_to_non_admins_in_both_editions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, edition: str, path: str
) -> None:
    c = _client(tmp_path, edition, monkeypatch)
    assert c.get(path).status_code == 401
    assert c.get(path, headers={"Authorization": "Bearer plain"}).status_code == 403
    assert c.get(path, headers={"Authorization": "Bearer admin"}).status_code == 200


def test_schema_and_docs_stay_open_on_a_single_operator_install(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PRAVRUDHI_AUTH", "disabled")
    c = _client(tmp_path, "product", monkeypatch)
    for path in ("/openapi.json", "/docs", "/redoc"):
        assert c.get(path).status_code == 200
