"""A2 matter span-index routes: registered only when the flag is on; the delete route also when a store directory exists.
Both arms of the flag, sign-in, tenancy, the routes' own budgets, the key-only gate, the audit row on every status.
Invented text."""

from __future__ import annotations

import re
import shutil
import sys
import threading
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from pravrudhi.api import identity, roles
from pravrudhi.api.partner import PartnerApiConfig, build_partner_router
from pravrudhi.api.server import create_app
from pravrudhi.application import audit, tenancy
from pravrudhi.application import matter_spans_config as msc
from pravrudhi.application.matter_spans_config import MatterSpansConfig
from tests.flag_peek_util import peek_all_true
from tests.test_api_partner import _agent, _proof_script
from tests.test_draft_check import b64, make_pdf
from tests.test_matter_spans_delete import NEEDLE, holders

USER = identity.User("u1", "user@example.test", "user")
USER2 = identity.User("u2", "two@example.test", "user")
CFG = PartnerApiConfig(rate_limit_per_minute=1000, max_concurrent=1, trust_proxy_header=False)
REPO = Path(__file__).resolve().parent.parent
MODULE = "pravrudhi.application.matter_spans"
P = "/api/v1/matter-spans"
ALL = [f"{P}/{n}" for n in ("matters", "documents", "query", "list", "delete")]
PAPER = "1. The statutory notice was sent on 4 May.\n\n2. The accused did not reply to the non-bailable allegation."


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv(msc.DIR_ENV, str(tmp_path / "store"))
    monkeypatch.setattr(identity, "PARTNER_KEY_ROUTES", identity.PARTNER_KEY_ROUTES)
    monkeypatch.setattr(identity, "PARTNER_KEY_PATHS", identity.PARTNER_KEY_PATHS)
    monkeypatch.setattr(roles, "USER_FACING", roles.USER_FACING)


def _on(monkeypatch: pytest.MonkeyPatch, cfg: MatterSpansConfig | None = None) -> None:
    monkeypatch.setattr(msc, "load_matter_spans_config", lambda _root: cfg or MatterSpansConfig())
    peek_all_true(monkeypatch)


def _client(tmp_path: Path, *, user: Any = USER, cfg: PartnerApiConfig = CFG) -> TestClient:
    app = FastAPI()
    agent = _agent(tmp_path, _proof_script())
    app.include_router(build_partner_router(tmp_path, agent_factory=lambda _r: agent, config=cfg))
    app.dependency_overrides[identity.current_user] = lambda: user
    return TestClient(app, raise_server_exceptions=False)


def _paths(client: TestClient) -> set[str]:
    from fastapi.routing import APIRoute, APIRouter

    def walk(routes: Any) -> Any:
        for r in routes:
            if isinstance(r, APIRoute):
                yield r.path
            else:
                sub = getattr(r, "original_router", None)
                if isinstance(sub, APIRouter):
                    yield from walk(sub.routes)

    return set(walk(client.app.routes))  # type: ignore[attr-defined]


def _flow(c: TestClient, text: str = PAPER) -> tuple[str, str]:
    m = c.post(f"{P}/matters", json={}).json()["matter_id"]
    d = c.post(f"{P}/documents", json={"matter_id": m, "text": text, "name": "paper"}).json()["doc_id"]
    return m, d


# ---- the flag ---------------------------------------------------------------------------------------------------------------


def test_with_the_flag_off_and_no_store_the_routes_are_absent_nothing_is_imported_and_nothing_is_created(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delitem(sys.modules, MODULE, raising=False)
    before = (identity.PARTNER_KEY_ROUTES, identity.PARTNER_KEY_PATHS, roles.USER_FACING)
    c = _client(tmp_path)
    assert not (_paths(c) & set(ALL))
    assert c.post(f"{P}/matters", json={}).status_code == 404 and c.post(f"{P}/delete", json={}).status_code == 404
    assert MODULE not in sys.modules
    assert before == (identity.PARTNER_KEY_ROUTES, identity.PARTNER_KEY_PATHS, roles.USER_FACING)
    assert not (tmp_path / "store").exists()


def test_with_the_flag_off_a_broken_module_cannot_stop_the_engine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import pravrudhi.application as pkg

    monkeypatch.setitem(sys.modules, MODULE, None)
    monkeypatch.delattr(pkg, "matter_spans", raising=False)
    c = _client(tmp_path)
    assert c.get("/api/v1/status").status_code == 200 and not (_paths(c) & set(ALL))


def test_with_the_flag_on_a_broken_module_turns_the_routes_off(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import pravrudhi.application as pkg

    _on(monkeypatch)
    monkeypatch.setitem(sys.modules, MODULE, None)
    monkeypatch.delattr(pkg, "matter_spans", raising=False)
    c = _client(tmp_path)
    assert c.get("/api/v1/status").status_code == 200 and not (_paths(c) & set(ALL))


def test_with_the_flag_on_all_five_routes_exist_outside_openapi(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _on(monkeypatch)
    c = _client(tmp_path)
    assert set(ALL) <= _paths(c)
    assert all(("POST", p) in identity.PARTNER_KEY_ROUTES and p in roles.USER_FACING for p in ALL)
    assert P not in c.get("/openapi.json").text


def test_the_flag_in_the_shipped_config_is_on() -> None:  # on since 0.6.23 (19 Oct 2026)
    assert msc.load_matter_spans_config(REPO) is not None
    text = (REPO / "configs" / "partner_api.yaml").read_text()
    assert "\nwf_a2_span_index: true\n" in text


def test_with_the_flag_on_no_route_of_the_whole_app_is_unclassified_or_stale(monkeypatch: pytest.MonkeyPatch) -> None:
    _on(monkeypatch)
    c = TestClient(create_app(REPO))
    paths = _paths(c)
    assert set(ALL) <= paths
    classified = roles.ADMIN_ONLY | roles.USER_FACING | roles.ADMIN_IN_BOTH_EDITIONS
    assert not (paths - classified), sorted(paths - classified)
    assert not (classified - paths), sorted(classified - paths)


# ---- the delete path outlives the flag ---------------------------------------------------------------------------------------


def test_data_stored_with_the_flag_on_stays_deletable_with_the_flag_off(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Turn on, store a secret, turn off + restart, delete through the flag-off path, scan every file for the secret."""
    store = tmp_path / "store"
    _on(monkeypatch)
    on = _client(tmp_path)
    m, d = _flow(on, f"1. A paragraph that holds {NEEDLE} for the test.\n\n2. Another paragraph.")
    assert holders(store, NEEDLE)
    monkeypatch.undo()  # the flag is off again (the shipped default) ...
    monkeypatch.setenv(msc.DIR_ENV, str(store))  # ... and the store directory still exists
    monkeypatch.setattr(identity, "PARTNER_KEY_ROUTES", identity.PARTNER_KEY_ROUTES)
    monkeypatch.setattr(identity, "PARTNER_KEY_PATHS", identity.PARTNER_KEY_PATHS)
    monkeypatch.setattr(roles, "USER_FACING", roles.USER_FACING)
    off = _client(tmp_path)
    assert f"{P}/delete" in _paths(off) and f"{P}/query" not in _paths(off) and f"{P}/documents" not in _paths(off)
    assert off.post(f"{P}/query", json={"matter_id": m, "q": "paragraph"}).status_code == 404  # reads and writes are gone
    r = off.post(f"{P}/delete", json={"matter_id": m, "doc_id": d})
    assert r.status_code == 200 and r.json()["deleted"] is True
    assert holders(store, NEEDLE) == []
    assert off.post(f"{P}/delete", json={"matter_id": m}).status_code == 200
    assert list(store.rglob("*")) == [] or all(p.is_dir() for p in store.rglob("*"))


def test_the_delete_route_is_not_registered_when_there_is_no_store_and_the_flag_is_off(tmp_path: Path) -> None:
    assert f"{P}/delete" not in _paths(_client(tmp_path))
    assert not (tmp_path / "store").exists()


# ---- sign-in, tenancy, answers ----------------------------------------------------------------------------------------------


def test_an_anonymous_caller_is_told_the_papers_are_for_signed_in_users(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _on(monkeypatch)
    c = _client(tmp_path, user=None)
    probes = [(f"{P}/matters", {}), (f"{P}/query", {"matter_id": "0" * 32, "q": "x"}), (f"{P}/delete", {"matter_id": "0" * 32})]
    for p, body in probes:
        r = c.post(p, json=body)
        assert r.status_code == 401 and "signed-in" in r.json()["detail"], p


def test_the_flow_create_ingest_query_list_delete(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _on(monkeypatch)
    c = _client(tmp_path)
    m, d = _flow(c)
    q = c.post(f"{P}/query", json={"matter_id": m, "q": "non-bailable allegation"}).json()
    assert q["found"] is True and q["spans"][0]["doc_id"] == d and q["spans"][0]["page"] == 1
    miss = c.post(f"{P}/query", json={"matter_id": m, "q": "deposit flat"}).json()
    assert miss["found"] is False and miss["message"] == "Not found in the papers you added"
    assert [x["doc_id"] for x in c.post(f"{P}/list", json={"matter_id": m}).json()["documents"]] == [d]
    assert c.post(f"{P}/delete", json={"matter_id": m, "doc_id": d}).json()["deleted"] is True
    assert c.post(f"{P}/query", json={"matter_id": m, "q": "notice"}).json()["found"] is False
    assert c.post(f"{P}/delete", json={"matter_id": m}).json()["deleted"] is True
    assert c.post(f"{P}/query", json={"matter_id": m, "q": "notice"}).status_code == 404


def test_another_signed_in_user_gets_not_found_for_every_route(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _on(monkeypatch)
    a = _client(tmp_path, user=USER)
    m, d = _flow(a)
    b = _client(tmp_path, user=USER2)
    for p, body in (
        (f"{P}/query", {"matter_id": m, "q": "notice"}), (f"{P}/list", {"matter_id": m}),
        (f"{P}/documents", {"matter_id": m, "text": "x y z"}), (f"{P}/delete", {"matter_id": m}),
        (f"{P}/delete", {"matter_id": m, "doc_id": d}),
    ):
        r = b.post(p, json=body)
        assert r.status_code == 404 and r.json()["error"] == "matter_not_found", p
    assert a.post(f"{P}/query", json={"matter_id": m, "q": "notice"}).json()["found"] is True  # untouched


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("documents", {"text": "a"}),  # no matter id
        ("documents", {"matter_id": "0" * 32}),  # neither text nor pdf
        ("documents", {"matter_id": "0" * 32, "text": "a", "pdf_base64": "QQ=="}),
        ("query", {"matter_id": "0" * 32}),
        ("query", {"matter_id": "short", "q": "x"}),
        ("list", {}),
        ("delete", {"matter_id": 5}),
    ],
)
def test_a_bad_body_is_422_and_nothing_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, path: str, body: dict[str, Any]
) -> None:
    _on(monkeypatch)
    assert _client(tmp_path).post(f"{P}/{path}", json=body).status_code == 422


def test_the_refusals_have_stable_codes_and_no_path_or_stack(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _on(monkeypatch, MatterSpansConfig(max_chars=1_000))
    c = _client(tmp_path)
    m = c.post(f"{P}/matters", json={}).json()["matter_id"]
    r = c.post(f"{P}/documents", json={"matter_id": m, "text": "word " * 400})
    assert r.status_code == 413 and r.json()["error"] == "too_long" and set(r.json()) == {"detail", "error"}
    r = c.post(f"{P}/query", json={"matter_id": m, "q": "   "})
    assert r.status_code == 422 and r.json()["error"] == "empty_query"
    r = c.post(f"{P}/documents", json={"matter_id": m, "pdf_base64": b64(b"not a pdf")})
    assert r.status_code == 422 and r.json()["error"] == "pdf_unreadable"
    assert "Traceback" not in r.text and str(tmp_path) not in r.text


def test_a_text_layer_pdf_is_indexed_through_the_route(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _on(monkeypatch)
    c = _client(tmp_path)
    m = c.post(f"{P}/matters", json={}).json()["matter_id"]
    pdf = make_pdf(["Page one has the accused and a cheque.", "Page two has the statutory notice and its date."])
    assert c.post(f"{P}/documents", json={"matter_id": m, "pdf_base64": b64(pdf)}).json()["n_pages"] == 2
    assert c.post(f"{P}/query", json={"matter_id": m, "q": "statutory notice"}).json()["spans"][0]["page"] == 2


def test_no_response_claims_more_than_deletion_from_the_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _on(monkeypatch)
    c = _client(tmp_path)
    m, d = _flow(c)
    texts = [c.post(f"{P}/delete", json={"matter_id": m, "doc_id": d}).text, c.post(f"{P}/delete", json={"matter_id": m}).text]
    low = " ".join(texts).lower()
    for word in ("erased", "irrecoverable", "unrecoverable", "permanently", "forever", "wiped", "destroyed"):
        assert word not in low, word
    assert "deleted from this service's store" in low


# ---- the route's own budgets ------------------------------------------------------------------------------------------------


def test_the_routes_have_their_own_rate_budget_and_spend_none_of_intakes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _on(monkeypatch)
    cfg = PartnerApiConfig(rate_limit_per_minute=2, max_concurrent=1, trust_proxy_header=False)
    c = _client(tmp_path, cfg=cfg)
    assert [c.post(f"{P}/matters", json={}).status_code for _ in range(3)] == [200, 200, 429]
    assert [c.post("/api/v1/intake", json={"text": "1. A fact."}).status_code for _ in range(2)] == [200, 200]


def test_exhausting_intake_does_not_spend_the_matter_budget(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _on(monkeypatch)
    cfg = PartnerApiConfig(rate_limit_per_minute=2, max_concurrent=1, trust_proxy_header=False)
    c = _client(tmp_path, cfg=cfg)
    assert [c.post("/api/v1/intake", json={"text": "1. A fact."}).status_code for _ in range(3)] == [200, 200, 429]
    assert c.post(f"{P}/matters", json={}).status_code == 200


def test_the_routes_have_their_own_slots_and_intake_slots_are_unaffected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _on(monkeypatch, MatterSpansConfig(max_concurrent=1))
    from pravrudhi.application import matter_spans as M

    gate, released = threading.Event(), threading.Event()
    real = M.MatterStore.create_matter

    def slow(self: Any, tenant: str) -> str:
        gate.set()
        released.wait(5)
        return real(self, tenant)

    monkeypatch.setattr(M.MatterStore, "create_matter", slow)
    c = _client(tmp_path)
    t = threading.Thread(target=lambda: c.post(f"{P}/matters", json={}))
    t.start()
    assert gate.wait(5)
    r = c.post(f"{P}/list", json={"matter_id": "0" * 32})
    assert r.status_code == 503 and r.json().get("error") == "agent_at_capacity"
    assert c.post("/api/v1/intake", json={"text": "1. A fact."}).status_code == 200  # intake's one slot is free
    released.set()
    t.join(10)


# ---- keys, the whole-surface gate and the audit row on every status ---------------------------------------------------------


def _keyed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, cfg: MatterSpansConfig | None = None) -> tuple[TestClient, str, Path]:
    _on(monkeypatch, cfg)
    monkeypatch.setenv("PRAVRUDHI_AUTH", "required")
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_JWT_SECRET", "s" * 40)
    monkeypatch.setenv("PRAVRUDHI_DISABLE_LOCAL_GUARD", "1")
    monkeypatch.setenv(tenancy.TENANCY_PROVISION_SECRET_ENV, "p" * 40)
    root = tmp_path / "root"
    shutil.copytree(REPO / "configs", root / "configs")
    conf = root / "configs" / "partner_api.yaml"
    conf.write_text(re.sub(r"(?m)^rate_limit_per_minute:.*$", "rate_limit_per_minute: 1000", conf.read_text()))
    tenancy.create_org(root, "acme", "Acme")
    key = tenancy.create_key(root, "acme", label="t")
    client = TestClient(create_app(root), headers={"host": "127.0.0.1:8008"}, raise_server_exceptions=False)
    return client, key.secret, root


def _rows(root: Path) -> list[dict[str, Any]]:
    from datetime import UTC, datetime

    key_id = tenancy.keys_for_org(root, "acme")[0].key_id
    rows, _next = audit.page(root, key_id, offset=0, limit=100, retention_s=3600, now=lambda: datetime.now(UTC))
    return rows


def test_a_key_only_caller_reaches_every_route_and_each_status_leaves_a_prefixed_audit_row(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    c, secret, root = _keyed(monkeypatch, tmp_path, MatterSpansConfig(max_chars=1_000))
    h = {tenancy.API_KEY_HEADER: secret}
    m = c.post(f"{P}/matters", json={}, headers=h).json()["matter_id"]  # 200
    assert c.post(f"{P}/documents", json={"matter_id": m, "text": PAPER}, headers=h).status_code == 200
    assert c.post(f"{P}/query", json={"matter_id": m, "q": "notice"}, headers=h).status_code == 200
    assert c.post(f"{P}/list", json={"matter_id": m}, headers=h).status_code == 200
    assert c.post(f"{P}/documents", json={"matter_id": m, "text": "word " * 400}, headers=h).status_code == 413
    assert c.post(f"{P}/query", json={"matter_id": "0" * 32, "q": "notice"}, headers=h).status_code == 404
    assert c.post(f"{P}/query", json={"matter_id": m, "q": " "}, headers=h).status_code == 422
    assert c.post(f"{P}/delete", json={"matter_id": m}, headers=h).status_code == 200
    rows = _rows(root)
    assert sorted(r["status_code"] for r in rows) == sorted([200, 200, 200, 200, 413, 404, 422, 200])
    assert all(r["mode"] == "intake" and r["run_id"].startswith("matter-spans-") for r in rows)
    assert "notice" not in str(rows).lower() and "statutory" not in str(rows).lower()  # never the text


def test_a_full_slot_leaves_a_503_audit_row_with_the_prefix(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from pravrudhi.application import matter_spans as M

    gate, released = threading.Event(), threading.Event()
    real = M.MatterStore.create_matter

    def slow(self: Any, tenant: str) -> str:
        gate.set()
        released.wait(5)
        return real(self, tenant)

    c, secret, root = _keyed(monkeypatch, tmp_path, MatterSpansConfig(max_concurrent=1))
    monkeypatch.setattr(M.MatterStore, "create_matter", slow)
    h = {tenancy.API_KEY_HEADER: secret}
    t = threading.Thread(target=lambda: c.post(f"{P}/matters", json={}, headers=h))
    t.start()
    assert gate.wait(5)
    assert c.post(f"{P}/list", json={"matter_id": "0" * 32}, headers=h).status_code == 503
    released.set()
    t.join(10)
    rows = _rows(root)
    assert sorted(r["status_code"] for r in rows) == [200, 503] and all(r["run_id"].startswith("matter-spans-") for r in rows)


def test_a_failure_inside_an_operation_leaves_a_prefixed_500_audit_row(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from pravrudhi.application import matter_spans as M

    def boom(self: Any, tenant: str) -> str:
        raise RuntimeError("internal")

    c, secret, root = _keyed(monkeypatch, tmp_path)
    monkeypatch.setattr(M.MatterStore, "create_matter", boom)
    r = c.post(f"{P}/matters", json={}, headers={tenancy.API_KEY_HEADER: secret})
    assert r.status_code == 500
    rows = _rows(root)
    assert len(rows) == 1 and rows[0]["status_code"] == 500 and rows[0]["run_id"].startswith("matter-spans-")


VALID = {
    "matters": {}, "documents": {"matter_id": "0" * 32, "text": "a b c"}, "query": {"matter_id": "0" * 32, "q": "word"},
    "list": {"matter_id": "0" * 32}, "delete": {"matter_id": "0" * 32},
}


def test_the_gate_refuses_no_credential_and_a_bad_key_is_401(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    c, secret, _root = _keyed(monkeypatch, tmp_path)
    for name, body in VALID.items():
        p = f"{P}/{name}"
        r = c.post(p, json=body)
        assert r.status_code == 401 and r.json()["detail"] == "Missing bearer token", p
        r2 = c.post(p, json=body, headers={tenancy.API_KEY_HEADER: "pnk_aaaaaaaaaaaaaaaa_notthesecret"})
        assert r2.status_code == 401 and r2.json()["detail"] == "Invalid or revoked API key", p
        r3 = c.post(p, json=body, headers={tenancy.API_KEY_HEADER: secret, "Authorization": "Bearer not-a-token"})
        assert r3.status_code == 401 and "Invalid token" in r3.json()["detail"], p


def test_two_orgs_with_keys_never_see_each_others_matters(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    c, secret, root = _keyed(monkeypatch, tmp_path)
    tenancy.create_org(root, "zorbland", "Zorbland")
    other = tenancy.create_key(root, "zorbland", label="o").secret
    h1, h2 = {tenancy.API_KEY_HEADER: secret}, {tenancy.API_KEY_HEADER: other}
    m = c.post(f"{P}/matters", json={}, headers=h1).json()["matter_id"]
    c.post(f"{P}/documents", json={"matter_id": m, "text": PAPER}, headers=h1)
    assert c.post(f"{P}/query", json={"matter_id": m, "q": "notice"}, headers=h2).status_code == 404
    assert c.post(f"{P}/delete", json={"matter_id": m}, headers=h2).status_code == 404
    assert c.post(f"{P}/query", json={"matter_id": m, "q": "notice"}, headers=h1).json()["found"] is True


def test_an_object_store_named_in_the_environment_is_used_and_unusable_credentials_answer_503(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pravrudhi.application import matter_objectstore as obj

    _on(monkeypatch)
    monkeypatch.setenv(obj.ENDPOINT_ENV, "https://s3.example.test")
    monkeypatch.setenv(obj.BUCKET_ENV, "bkt")
    monkeypatch.delenv(obj.ACCESS_KEY_ENV, raising=False)
    monkeypatch.delenv(obj.SECRET_KEY_ENV, raising=False)
    c = _client(tmp_path)
    r = c.post(f"{P}/matters", json={})
    assert r.status_code == 503 and "s3.example.test" not in r.text and "bkt" not in r.text
    assert not (tmp_path / "store").exists() or not any((tmp_path / "store").iterdir())  # nothing fell back to a local disk


def test_with_an_object_store_named_the_delete_route_exists_with_the_flag_off_and_no_local_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pravrudhi.application import matter_objectstore as obj

    assert f"{P}/delete" not in _paths(_client(tmp_path))
    monkeypatch.setenv(obj.ENDPOINT_ENV, "https://s3.example.test")
    monkeypatch.setenv(obj.BUCKET_ENV, "bkt")
    off = _client(tmp_path)
    assert f"{P}/delete" in _paths(off) and f"{P}/query" not in _paths(off) and f"{P}/documents" not in _paths(off)
