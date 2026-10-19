# ruff: noqa: E501
"""S1 matter citation sweep route (POST /api/v1/matter-citation-sweep), registered only when wf_s1_matter_citation_sweep is on; it needs A2 ON (its
flag, not the store folder) and a configured citation index. Both arms of both flags, sign-in, tenancy, keys, the audit row, refusals. Invented text."""

from __future__ import annotations

import re
import shutil
import sys
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
from pravrudhi.application import matter_sweep_config as swc
from pravrudhi.application.case_index import CaseRecord, insert_case, open_index
from pravrudhi.application.matter_spans_config import MatterSpansConfig
from pravrudhi.application.matter_sweep_config import SweepConfig
from tests.flag_peek_util import peek_all_true
from tests.test_api_partner import _agent, _proof_script

USER = identity.User("u1", "user@example.test", "user")
USER2 = identity.User("u2", "two@example.test", "user")
CFG = PartnerApiConfig(rate_limit_per_minute=1000, max_concurrent=1, trust_proxy_header=False)
REPO = Path(__file__).resolve().parent.parent
MODULES = ("pravrudhi.application.matter_sweep", "pravrudhi.application.matter_sweep_matter", "pravrudhi.application.draft_check")
PATH = "/api/v1/matter-citation-sweep"
A2 = "/api/v1/matter-spans"
GOOD = "(2087) 4 SCC 512"
MISSING = "(2086) 1 SCC 99"
PAPER = f"The applicant relied on {GOOD} and also on {MISSING} before the court."


def _index(tmp_path: Path) -> Path:
    p = tmp_path / "idx.sqlite3"
    conn = open_index(p)
    citer = f"In Aldric Vennoway v. State of Zorbland {GOOD} the Court clarified the rule."
    insert_case(conn, CaseRecord("c1", "Later Case v Other", "Supreme Court", 2095, "sc_pdf", "/x", citer))
    insert_case(conn, CaseRecord("r1", "Aldric Vennoway vs State Of Zorbland", "Supreme Court", 2087, "sc_pdf", "/y", "body"))
    conn.commit()
    conn.close()
    return p


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv(msc.DIR_ENV, str(tmp_path / "store"))
    monkeypatch.setenv("PRAVRUDHI_CITATION_INDEX", str(_index(tmp_path)))
    monkeypatch.setenv("PRAVRUDHI_CITATION_WARM", "0")
    monkeypatch.setattr(identity, "PARTNER_KEY_ROUTES", identity.PARTNER_KEY_ROUTES)
    monkeypatch.setattr(identity, "PARTNER_KEY_PATHS", identity.PARTNER_KEY_PATHS)
    monkeypatch.setattr(roles, "USER_FACING", roles.USER_FACING)


def _flags(monkeypatch: pytest.MonkeyPatch, s1: SweepConfig | None = None, a2: MatterSpansConfig | None = None) -> None:
    monkeypatch.setattr(swc, "load_sweep_config", lambda _root: s1)
    monkeypatch.setattr(msc, "load_matter_spans_config", lambda _root: a2)
    peek_all_true(monkeypatch)


def _on(monkeypatch: pytest.MonkeyPatch, cfg: SweepConfig | None = None) -> None:
    _flags(monkeypatch, cfg or SweepConfig(), MatterSpansConfig())


def _client(tmp_path: Path, *, user: Any = USER) -> TestClient:
    app = FastAPI()
    agent = _agent(tmp_path, _proof_script())
    app.include_router(build_partner_router(tmp_path, agent_factory=lambda _r: agent, config=CFG))
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


def _matter(c: TestClient, *docs: tuple[str, str]) -> str:
    m = c.post(f"{A2}/matters", json={}).json()["matter_id"]
    for name, text in docs:
        assert c.post(f"{A2}/documents", json={"matter_id": m, "text": text, "name": name}).status_code == 200
    return m


# ---- the flags ------------------------------------------------------------------------------------------------------------------


def test_with_the_flag_off_the_route_is_absent_nothing_is_imported_and_the_lists_are_the_released_ones(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _flags(monkeypatch)
    for m in MODULES:
        monkeypatch.delitem(sys.modules, m, raising=False)
    before = (identity.PARTNER_KEY_ROUTES, identity.PARTNER_KEY_PATHS, roles.USER_FACING)
    c = _client(tmp_path)
    assert PATH not in _paths(c)
    unknown = c.post("/api/v1/no-such-path-at-all", json={"matter_id": "a" * 32})
    r = c.post(PATH, json={"matter_id": "a" * 32})
    assert r.status_code == unknown.status_code == 404 and r.content == unknown.content
    assert not any(m in sys.modules for m in MODULES)
    assert before == (identity.PARTNER_KEY_ROUTES, identity.PARTNER_KEY_PATHS, roles.USER_FACING)


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
    paths = _paths(TestClient(create_app(REPO)))
    classified = roles.ADMIN_ONLY | roles.USER_FACING | roles.ADMIN_IN_BOTH_EDITIONS
    assert PATH in paths and not (paths - classified) and not (classified - paths)


def test_a1s_flag_is_not_required_only_its_module_is_imported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _on(monkeypatch)
    c = _client(tmp_path)
    assert "/api/v1/draft-citation-check" not in _paths(c) and PATH in _paths(c)


def test_s1_on_with_a2_off_refuses_and_reads_nothing_even_when_a_store_folder_holds_a_matter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _on(monkeypatch)
    m = _matter(_client(tmp_path), ("a.pdf", PAPER))
    assert (tmp_path / "store").is_dir()
    _flags(monkeypatch, SweepConfig(), None)
    r = _client(tmp_path).post(PATH, json={"matter_id": m})
    assert r.status_code == 409 and r.json() == {"detail": "Stored papers are not available.", "error": "store_off"}


# ---- refusals and sign-in ------------------------------------------------------------------------------------------------------------


def test_no_configured_index_refuses_with_r1s_line_after_the_tenant_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _on(monkeypatch)
    c = _client(tmp_path)
    m = _matter(c, ("a.pdf", PAPER))
    monkeypatch.delenv("PRAVRUDHI_CITATION_INDEX", raising=False)
    r = c.post(PATH, json={"matter_id": m})
    assert r.status_code == 409 and r.json() == {
        "detail": "The citation index is not available here, so no citation was checked.",
        "error": "index_off",
    }
    foreign = _client(tmp_path, user=USER2).post(PATH, json={"matter_id": m})
    none = _client(tmp_path, user=USER2).post(PATH, json={"matter_id": "b" * 32})
    assert foreign.status_code == none.status_code == 404 and foreign.content == none.content  # a foreign matter is NEVER a 409


def test_a_matter_with_no_papers_is_a_stated_refusal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _on(monkeypatch)
    c = _client(tmp_path)
    m = c.post(f"{A2}/matters", json={}).json()["matter_id"]
    r = c.post(PATH, json={"matter_id": m})
    assert r.status_code == 409 and r.json() == {"detail": "There are no papers to read. Add papers first.", "error": "no_papers"}


def test_an_anonymous_caller_is_told_it_is_for_signed_in_users(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _on(monkeypatch)
    r = _client(tmp_path, user=None).post(PATH, json={"matter_id": "a" * 32})
    assert r.status_code == 401 and "signed-in" in r.json()["detail"]


@pytest.mark.parametrize("body", [{}, {"matter_id": 5}, {"matter_id": "x"}, {"matter_id": "a" * 32, "z": 1}])
def test_a_bad_body_is_422_and_nothing_runs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: dict[str, Any]) -> None:
    _on(monkeypatch)
    assert _client(tmp_path).post(PATH, json=body).status_code == 422


# ---- a sweep ---------------------------------------------------------------------------------------------------------------------


def test_a_signed_in_caller_gets_the_sweep_for_their_own_matter(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _on(monkeypatch)
    c = _client(tmp_path)
    m = _matter(c, ("Paper one.pdf", PAPER), ("Paper two.pdf", f"See {GOOD} once more."))
    r = c.post(PATH, json={"matter_id": m})
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["run_id"].startswith("sweep-") and b["matter_id"] == m and b["heading"] == "Papers you added"
    d = b["matter_denominator"]
    assert d["read"] == 3 and d["distinct"] == 2 and d["found"] == 2 and d["not_found"] == 1 and d["lookups_made"] == 2
    assert b["state"] == "listed_strings_looked_up"
    assert (
        b["summary"].startswith("Existence check only, in the papers you added.")
        and "3 citations listed in 2 papers; 2 distinct citations." in b["summary"]
    )
    assert [p["name"] for p in b["papers_read"]] == ["Paper one.pdf", "Paper two.pdf"]


def test_the_route_reads_no_text_of_another_matter_and_a_foreign_matter_is_404(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _on(monkeypatch)
    m = _matter(_client(tmp_path), ("zebra-secret-name.pdf", f"zebra-secret {PAPER}"))
    other = _client(tmp_path, user=USER2)
    r_foreign = other.post(PATH, json={"matter_id": m})
    r_none = other.post(PATH, json={"matter_id": "b" * 32})
    assert r_foreign.status_code == r_none.status_code == 404 and r_foreign.content == r_none.content
    assert "zebra-secret" not in r_foreign.text


def test_a_matter_deleted_between_two_sweeps_answers_404_on_the_second(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _on(monkeypatch)
    c = _client(tmp_path)
    m = _matter(c, ("a.pdf", PAPER))
    assert c.post(PATH, json={"matter_id": m}).status_code == 200
    assert c.post(f"{A2}/delete", json={"matter_id": m}).status_code == 200
    r = c.post(PATH, json={"matter_id": m})
    none = c.post(PATH, json={"matter_id": "c" * 32})
    assert r.status_code == 404 and r.content == none.content


def test_a_half_ingested_document_is_never_swept_and_is_counted_not_ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import sqlite3

    from pravrudhi.application.matter_spans import DB_NAME

    _on(monkeypatch)
    c = _client(tmp_path)
    m = _matter(c, ("a.pdf", PAPER), ("b.pdf", f"See {GOOD}."))
    db = next((tmp_path / "store").rglob(DB_NAME))
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE document SET ingest_done = 0 WHERE name = 'b.pdf'")
    b = c.post(PATH, json={"matter_id": m}).json()
    assert [p["name"] for p in b["papers_read"]] == ["a.pdf"] and b["matter_denominator"]["papers_excluded_not_ready"] == 1
    assert "1 paper was not ready and is not included." in b["notes"] and b["state"] == "unread_or_unchecked_remain"


def test_one_sweep_per_matter_at_a_time_a_second_gets_409_busy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from pravrudhi.application import matter_sweep_matter as M

    _on(monkeypatch)
    c = _client(tmp_path)
    m = _matter(c, ("a.pdf", PAPER))
    tenant_keys = {k for k in M._inflight}
    assert tenant_keys == set()
    M._inflight.add(("user:u1", m))
    try:
        r = c.post(PATH, json={"matter_id": m})
    finally:
        M._inflight.discard(("user:u1", m))
    assert r.status_code == 409 and r.json()["error"] == "busy"
    assert c.post(PATH, json={"matter_id": m}).status_code == 200  # released: the next one runs


def test_over_the_distinct_cap_the_route_answers_422_with_the_live_cap_and_looks_nothing_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _on(monkeypatch, SweepConfig(max_distinct_citations=2))
    c = _client(tmp_path)
    m = _matter(c, ("a.pdf", f"{GOOD} and {MISSING} and (2085) 2 SCC 7."))
    r = c.post(PATH, json={"matter_id": m})
    assert r.status_code == 422 and r.json()["error"] == "too_many_citations"
    assert (
        r.json()["detail"]
        == "This matter lists more than 2 different citations, so the sweep does not run on it. Check them in smaller sets, or refer to a lawyer."
    )


# ---- keys, orgs, the audit row -------------------------------------------------------------------------------------------------------


def _keyed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[TestClient, str, Path]:
    _on(monkeypatch)
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


def test_a_second_key_of_the_same_org_passes_and_another_orgs_key_gets_the_same_404_as_none(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    c, secret, root = _keyed(monkeypatch, tmp_path)
    second = tenancy.create_key(root, "acme", label="second").secret
    tenancy.create_org(root, "zorbland", "Zorbland")
    other = tenancy.create_key(root, "zorbland", label="o").secret
    h1, h2, h3 = ({tenancy.API_KEY_HEADER: k} for k in (secret, second, other))
    m = c.post(f"{A2}/matters", json={}, headers=h1).json()["matter_id"]
    assert (
        c.post(
            f"{A2}/documents", json={"matter_id": m, "text": f"zebra-secret {PAPER}", "name": "zebra-secret.pdf"}, headers=h1
        ).status_code
        == 200
    )
    assert c.post(PATH, json={"matter_id": m}, headers=h1).status_code == 200
    assert c.post(PATH, json={"matter_id": m}, headers=h2).status_code == 200  # the SAME org's other key
    foreign = c.post(PATH, json={"matter_id": m}, headers=h3)
    none = c.post(PATH, json={"matter_id": "d" * 32}, headers=h3)
    assert (
        foreign.status_code == none.status_code == 404 and foreign.content == none.content and "zebra-secret" not in foreign.text
    )
    assert c.post(PATH, json={"matter_id": m}).status_code in (401, 403)  # no key, no session


def test_each_status_leaves_a_sweep_prefixed_audit_row_and_a_run_id_with_nothing_of_the_request(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from datetime import UTC, datetime

    c, secret, root = _keyed(monkeypatch, tmp_path)
    h = {tenancy.API_KEY_HEADER: secret}
    m = c.post(f"{A2}/matters", json={}, headers=h).json()["matter_id"]
    c.post(f"{A2}/documents", json={"matter_id": m, "text": PAPER, "name": "marker-name.pdf"}, headers=h)
    assert c.post(PATH, json={"matter_id": m}, headers=h).status_code == 200
    assert c.post(PATH, json={"matter_id": "0" * 32}, headers=h).status_code == 404
    key_id = tenancy.keys_for_org(root, "acme")[0].key_id
    rows, _n = audit.page(root, key_id, offset=0, limit=100, retention_s=3600, now=lambda: datetime.now(UTC))
    sweeps = [r for r in rows if str(r.get("run_id", "")).startswith("sweep-")]
    assert len(sweeps) == 2 and {r["status_code"] for r in sweeps} == {200, 404} and all(r["mode"] == "intake" for r in sweeps)
    for r in sweeps:
        assert re.fullmatch(r"sweep-[0-9a-f]{10}", r["run_id"]) and m not in r["run_id"] and "marker" not in repr(r)
