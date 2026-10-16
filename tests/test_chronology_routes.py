# ruff: noqa: E501
"""A3 chronology route (POST /api/v1/chronology), registered only when wf_a3_chronology is on; it needs A2 ON (its flag, not the
store folder). Both arms of both flags, sign-in, tenancy, the route's own budgets, the key-only gate, the audit row on every
status, and the safety refusals. Invented text."""

from __future__ import annotations

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
from pravrudhi.application import chronology_config as cc
from pravrudhi.application import matter_spans_config as msc
from pravrudhi.application.chronology_config import ChronologyConfig
from pravrudhi.application.matter_spans_config import MatterSpansConfig
from tests.test_api_partner import _agent, _proof_script

USER = identity.User("u1", "user@example.test", "user")
USER2 = identity.User("u2", "two@example.test", "user")
CFG = PartnerApiConfig(rate_limit_per_minute=1000, max_concurrent=1, trust_proxy_header=False)
REPO = Path(__file__).resolve().parent.parent
MODULE = "pravrudhi.application.chronology_matter"
CORE = "pravrudhi.application.chronology"
PATH = "/api/v1/chronology"
A2 = "/api/v1/matter-spans"
PAPER = "Ravi Kumar was arrested on 13 June 2024. The matter is listed on 5.9.2026. In March 2019 he left the city."


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv(msc.DIR_ENV, str(tmp_path / "store"))
    monkeypatch.setattr(identity, "PARTNER_KEY_ROUTES", identity.PARTNER_KEY_ROUTES)
    monkeypatch.setattr(identity, "PARTNER_KEY_PATHS", identity.PARTNER_KEY_PATHS)
    monkeypatch.setattr(roles, "USER_FACING", roles.USER_FACING)


def _flags(monkeypatch: pytest.MonkeyPatch, a3: ChronologyConfig | None = None, a2: MatterSpansConfig | None = None) -> None:
    monkeypatch.setattr(cc, "load_chronology_config", lambda _root: a3)
    monkeypatch.setattr(msc, "load_matter_spans_config", lambda _root: a2)


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


def _matter(c: TestClient, *texts: str) -> str:
    m = c.post(f"{A2}/matters", json={}).json()["matter_id"]
    for i, t in enumerate(texts):
        assert c.post(f"{A2}/documents", json={"matter_id": m, "text": t, "name": f"paper {i}.pdf"}).status_code == 200
    return m


def _on(monkeypatch: pytest.MonkeyPatch) -> None:
    _flags(monkeypatch, ChronologyConfig(), MatterSpansConfig())


# ---- the flags ---------------------------------------------------------------------------------------------------------------------


def test_with_the_flag_off_the_route_is_absent_nothing_is_imported_and_the_lists_are_the_released_ones(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _flags(monkeypatch)
    monkeypatch.delitem(sys.modules, MODULE, raising=False)
    before = (identity.PARTNER_KEY_ROUTES, identity.PARTNER_KEY_PATHS, roles.USER_FACING)
    c = _client(tmp_path)
    assert PATH not in _paths(c) and c.post(PATH, json={"matter_id": "a" * 32}).status_code == 404
    assert MODULE not in sys.modules
    assert before == (identity.PARTNER_KEY_ROUTES, identity.PARTNER_KEY_PATHS, roles.USER_FACING)


def test_with_the_flag_off_a_broken_module_cannot_stop_the_engine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import pravrudhi.application as pkg

    _flags(monkeypatch)
    monkeypatch.setitem(sys.modules, MODULE, None)
    monkeypatch.delattr(pkg, "chronology_matter", raising=False)
    c = _client(tmp_path)
    assert c.get("/api/v1/status").status_code == 200 and PATH not in _paths(c)


def test_with_the_flag_on_a_broken_module_turns_the_route_off(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import pravrudhi.application as pkg

    _on(monkeypatch)
    monkeypatch.setitem(sys.modules, MODULE, None)
    monkeypatch.delattr(pkg, "chronology_matter", raising=False)
    c = _client(tmp_path)
    assert c.get("/api/v1/status").status_code == 200 and PATH not in _paths(c)


def test_with_the_flag_on_the_route_exists_opens_the_gate_lists_and_is_not_in_openapi(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _on(monkeypatch)
    c = _client(tmp_path)
    assert PATH in _paths(c) and ("POST", PATH) in identity.PARTNER_KEY_ROUTES and PATH in identity.PARTNER_KEY_PATHS
    assert PATH in roles.USER_FACING and PATH not in roles.ADMIN_ONLY and PATH not in roles.ADMIN_IN_BOTH_EDITIONS
    assert PATH not in c.get("/openapi.json").text


def test_with_the_flag_on_no_route_of_the_whole_app_is_unclassified_or_stale(monkeypatch: pytest.MonkeyPatch) -> None:
    _on(monkeypatch)
    c = TestClient(create_app(REPO))
    paths = _paths(c)
    assert PATH in paths
    classified = roles.ADMIN_ONLY | roles.USER_FACING | roles.ADMIN_IN_BOTH_EDITIONS
    assert not (paths - classified), sorted(paths - classified)
    assert not (classified - paths), sorted(classified - paths)


def test_the_flag_in_the_shipped_config_is_off() -> None:
    assert cc.load_chronology_config(REPO) is None


def test_the_three_optional_routes_can_all_be_on_at_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _on(monkeypatch)
    c = _client(tmp_path)
    m = _matter(c, PAPER)
    assert (
        c.post(PATH, json={"matter_id": m}).status_code == 200
        and c.post(f"{A2}/query", json={"matter_id": m, "q": "arrested"}).status_code == 200
    )


# ---- A2 is the input: on means its FLAG, not a store folder ---------------------------------------------------------------------


def test_a3_on_with_a2_off_refuses_and_reads_nothing_even_when_a_store_folder_holds_a_matter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _on(monkeypatch)
    c = _client(tmp_path)
    m = _matter(c, PAPER)
    assert (tmp_path / "store").is_dir()
    _flags(monkeypatch, ChronologyConfig(), None)  # A2 switched off, the folder stays so it can be deleted
    c2 = _client(tmp_path)
    r = c2.post(PATH, json={"matter_id": m})
    assert r.status_code == 409 and r.json() == {"detail": "Stored papers are not available.", "error": "store_off"}


def test_a_matter_with_no_papers_is_a_stated_refusal_not_an_empty_table(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _on(monkeypatch)
    c = _client(tmp_path)
    m = c.post(f"{A2}/matters", json={}).json()["matter_id"]
    r = c.post(PATH, json={"matter_id": m})
    assert r.status_code == 409 and r.json() == {"detail": "There are no papers to read. Add papers first.", "error": "no_papers"}


def test_a_half_ingested_document_is_never_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import sqlite3

    from pravrudhi.application.matter_spans import DB_NAME, MatterStore

    _on(monkeypatch)
    c = _client(tmp_path)
    m = _matter(c, PAPER)
    store = MatterStore(tmp_path / "store", MatterSpansConfig())
    db = next((tmp_path / "store").rglob(DB_NAME))
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE document SET ingest_done = 0")
    assert c.post(PATH, json={"matter_id": m}).status_code == 409  # nothing committed: no papers
    _ = store


# ---- sign-in, tenancy and the answer -----------------------------------------------------------------------------------------------


def test_an_anonymous_caller_is_told_it_is_for_signed_in_users(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _on(monkeypatch)
    r = _client(tmp_path, user=None).post(PATH, json={"matter_id": "a" * 32})
    assert r.status_code == 401 and "signed-in" in r.json()["detail"]


def test_a_signed_in_caller_gets_the_table_for_their_own_matter(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _on(monkeypatch)
    c = _client(tmp_path)
    m = _matter(c, PAPER)
    r = c.post(PATH, json={"matter_id": m})
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["run_id"].startswith("chronology-") and b["matter_id"] == m
    assert [e["date_as_written"] for e in b["events"]] == ["13 June 2024"] and b["events"][0]["mentions"] == "an arrest"
    assert [e["date_as_written"] for e in b["scheduled"]] == ["5.9.2026"] and [
        e["date_as_written"] for e in b["partial_dates"]
    ] == ["March 2019"]
    assert b["statement"].startswith("Built and tested on court judgment recitals and invented fixtures only")
    assert b["papers_read"][0]["name"] == "paper 0.pdf"


def test_another_tenants_matter_is_exactly_as_not_found_as_none(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _on(monkeypatch)
    m = _matter(_client(tmp_path), PAPER)
    other = _client(tmp_path, user=USER2).post(PATH, json={"matter_id": m})
    none = _client(tmp_path, user=USER2).post(PATH, json={"matter_id": "b" * 32})
    assert other.status_code == none.status_code == 404 and other.json() == none.json()


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"matter_id": 5},
        {"matter_id": "x"},
        {"matter_id": "A" * 32},
        {"matter_id": "a" * 32, "z": 1},
        {"matter_id": "../" * 8},
    ],
)
def test_a_bad_body_is_422_and_nothing_runs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: dict[str, Any]) -> None:
    _on(monkeypatch)
    assert _client(tmp_path).post(PATH, json=body).status_code == 422


def test_two_documents_of_one_matter_group_a_pair_through_the_route(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _on(monkeypatch)
    c = _client(tmp_path)
    m = _matter(c, "Ravi Kumar was arrested on 13 June 2024.", "Ravi Kumar was arrested on 14 June 2024.")
    b = c.post(PATH, json={"matter_id": m}).json()
    assert len(b["groups"]) == 1 and b["groups"][0]["heading"] == "Rows that may be the same event with different dates"


def test_the_delete_removes_the_rows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _on(monkeypatch)
    c = _client(tmp_path)
    m = _matter(c, PAPER)
    assert c.post(PATH, json={"matter_id": m}).json()["events"]
    assert c.post(f"{A2}/delete", json={"matter_id": m}).status_code == 200
    assert c.post(PATH, json={"matter_id": m}).status_code == 404


# ---- the route's own budgets -------------------------------------------------------------------------------------------------------


def test_the_route_has_its_own_rate_budget_and_spends_none_of_intakes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _on(monkeypatch)
    cfg = PartnerApiConfig(rate_limit_per_minute=3, max_concurrent=1, trust_proxy_header=False)
    c = _client(tmp_path, cfg=cfg)
    body = {"matter_id": "a" * 32}
    assert [c.post(PATH, json=body).status_code for _ in range(4)] == [404, 404, 404, 429]
    assert [c.post("/api/v1/intake", json={"text": "1. A fact."}).status_code for _ in range(2)] == [200, 200]


def test_the_route_has_its_own_slots_and_a_full_slot_is_a_coded_503(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from pravrudhi.application import chronology_matter as M

    _flags(monkeypatch, ChronologyConfig(max_concurrent=1), MatterSpansConfig())
    gate, released = threading.Event(), threading.Event()
    real = M.chronology_for_matter

    def slow(*a: Any, **k: Any) -> Any:
        gate.set()
        released.wait(5)
        return real(*a, **k)

    c = _client(tmp_path)
    m = _matter(c, PAPER)
    monkeypatch.setattr(M, "chronology_for_matter", slow)
    results: list[int] = []
    t = threading.Thread(target=lambda: results.append(c.post(PATH, json={"matter_id": m}).status_code))
    t.start()
    assert gate.wait(5)
    second = c.post(PATH, json={"matter_id": m})
    assert second.status_code == 503 and second.json().get("error") == "agent_at_capacity"
    assert c.post("/api/v1/intake", json={"text": "1. A fact."}).status_code == 200  # an existing route's slot is untouched
    released.set()
    t.join(10)
    assert results == [200]


# ---- keys, the whole-surface gate and the audit row ----------------------------------------------------------------------------------


def _keyed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, cfg: ChronologyConfig | None = None) -> tuple[TestClient, str, Path]:
    _flags(monkeypatch, cfg or ChronologyConfig(), MatterSpansConfig())
    monkeypatch.setenv("PRAVRUDHI_AUTH", "required")
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_JWT_SECRET", "s" * 40)
    monkeypatch.setenv("PRAVRUDHI_DISABLE_LOCAL_GUARD", "1")
    monkeypatch.setenv(tenancy.TENANCY_PROVISION_SECRET_ENV, "p" * 40)
    root = tmp_path / "root"
    shutil.copytree(REPO / "configs", root / "configs")
    tenancy.create_org(root, "acme", "Acme")
    key = tenancy.create_key(root, "acme", label="t")
    client = TestClient(create_app(root), headers={"host": "127.0.0.1:8008"}, raise_server_exceptions=False)
    return client, key.secret, root


def _rows(root: Path) -> list[dict[str, Any]]:
    from datetime import UTC, datetime

    key_id = tenancy.keys_for_org(root, "acme")[0].key_id
    rows, _next = audit.page(root, key_id, offset=0, limit=50, retention_s=3600, now=lambda: datetime.now(UTC))
    return rows


def test_a_key_only_caller_reaches_the_route_and_the_audit_row_carries_the_run_id_and_no_text(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    c, secret, root = _keyed(monkeypatch, tmp_path)
    hdr = {tenancy.API_KEY_HEADER: secret}
    m = c.post(f"{A2}/matters", json={}, headers=hdr).json()["matter_id"]
    assert c.post(f"{A2}/documents", json={"matter_id": m, "text": PAPER, "name": "p.pdf"}, headers=hdr).status_code == 200
    r = c.post(PATH, json={"matter_id": m}, headers=hdr)
    assert r.status_code == 200, r.text
    mine = [x for x in _rows(root) if str(x["run_id"]).startswith("chronology-")]
    assert len(mine) == 1 and mine[0]["status_code"] == 200 and mine[0]["mode"] == "intake"
    assert "Ravi" not in str(_rows(root)) and "June" not in str(mine)


@pytest.mark.parametrize(("body", "status"), [({"matter_id": "c" * 32}, 404)])
def test_a_refused_call_leaves_an_audit_row_with_the_chronology_run_id(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, body: dict[str, Any], status: int
) -> None:
    c, secret, root = _keyed(monkeypatch, tmp_path)
    r = c.post(PATH, json=body, headers={tenancy.API_KEY_HEADER: secret})
    assert r.status_code == status
    rows = [x for x in _rows(root) if str(x["run_id"]).startswith("chronology-")]
    assert len(rows) == 1 and rows[0]["status_code"] == status


def test_a_failure_inside_the_build_leaves_a_500_audit_row(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from pravrudhi.application import chronology_matter as M

    def boom(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("internal")

    c, secret, root = _keyed(monkeypatch, tmp_path)
    monkeypatch.setattr(M, "chronology_for_matter", boom)
    r = c.post(PATH, json={"matter_id": "d" * 32}, headers={tenancy.API_KEY_HEADER: secret})
    assert r.status_code == 500
    rows = [x for x in _rows(root) if str(x["run_id"]).startswith("chronology-")]
    assert len(rows) == 1 and rows[0]["status_code"] == 500


def test_the_gate_refuses_the_route_without_a_credential_and_a_bad_key_is_401(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    c, _secret, _root = _keyed(monkeypatch, tmp_path)
    r = c.post(PATH, json={"matter_id": "a" * 32})
    assert r.status_code == 401 and r.json()["detail"] == "Missing bearer token"
    r2 = c.post(PATH, json={"matter_id": "a" * 32}, headers={tenancy.API_KEY_HEADER: "pnk_aaaaaaaaaaaaaaaa_notthesecret"})
    assert r2.status_code == 401 and r2.json()["detail"] == "Invalid or revoked API key"


def test_a_key_of_another_org_cannot_read_a_matter_and_the_error_shape_has_no_path_or_stack(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    c, secret, root = _keyed(monkeypatch, tmp_path)
    tenancy.create_org(root, "other", "Other")
    other = tenancy.create_key(root, "other", label="o")
    hdr = {tenancy.API_KEY_HEADER: secret}
    m = c.post(f"{A2}/matters", json={}, headers=hdr).json()["matter_id"]
    c.post(f"{A2}/documents", json={"matter_id": m, "text": PAPER, "name": "p.pdf"}, headers=hdr)
    r = c.post(PATH, json={"matter_id": m}, headers={tenancy.API_KEY_HEADER: other.secret})
    assert (
        r.status_code == 404
        and set(r.json()) == {"detail", "error"}
        and "/" not in r.json()["detail"]
        and "Traceback" not in r.text
    )
