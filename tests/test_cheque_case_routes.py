# ruff: noqa: E501
"""A6 cheque-case check route (POST /api/v1/cheque-case-check), registered only when wf_a6_cheque_case_check is on; it needs A2 ON and calls A3's
chronology, A5's agenda and (with confirmed dates) B3's checklist as functions. Invented papers and dates."""

from __future__ import annotations

import re
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from pravrudhi.api import identity, roles
from pravrudhi.api.partner import PartnerApiConfig, build_partner_router
from pravrudhi.api.server import create_app
from pravrudhi.application import audit, tenancy
from pravrudhi.application import cheque_case_config as ccc
from pravrudhi.application import matter_spans_config as msc
from pravrudhi.application.cheque_case_config import ChequeCaseConfig
from pravrudhi.application.matter_spans_config import MatterSpansConfig
from tests.flag_peek_util import peek_all_true
from tests.test_api_partner import _agent, _proof_script

USER = identity.User("u1", "user@example.test", "user")
USER2 = identity.User("u2", "two@example.test", "user")
CFG = PartnerApiConfig(rate_limit_per_minute=1000, max_concurrent=1, trust_proxy_header=False)
REPO = Path(__file__).resolve().parent.parent
MODULE = "pravrudhi.application.cheque_case_route"
PATH = "/api/v1/cheque-case-check"
A2 = "/api/v1/matter-spans"
PAPER = "The cheque dated 01.02.2013 was presented on 10.03.2013. The legal notice dated 20.03.2013 was sent. The cheque was returned unpaid."


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv(msc.DIR_ENV, str(tmp_path / "store"))
    monkeypatch.setattr(identity, "PARTNER_KEY_ROUTES", identity.PARTNER_KEY_ROUTES)
    monkeypatch.setattr(identity, "PARTNER_KEY_PATHS", identity.PARTNER_KEY_PATHS)
    monkeypatch.setattr(roles, "USER_FACING", roles.USER_FACING)


def _flags(monkeypatch: pytest.MonkeyPatch, a6: ChequeCaseConfig | None = None, a2: MatterSpansConfig | None = None) -> None:
    monkeypatch.setattr(ccc, "load_cheque_case_config", lambda _root: a6)
    monkeypatch.setattr(msc, "load_matter_spans_config", lambda _root: a2)
    peek_all_true(monkeypatch)


def _on(monkeypatch: pytest.MonkeyPatch) -> None:
    _flags(monkeypatch, ChequeCaseConfig(), MatterSpansConfig())


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


def _matter(c: TestClient, *names: str, text: str = PAPER) -> str:
    m = c.post(f"{A2}/matters", json={}).json()["matter_id"]
    for n in names:
        assert c.post(f"{A2}/documents", json={"matter_id": m, "text": text, "name": n}).status_code == 200
    return m


def test_with_the_flag_off_the_route_is_absent_nothing_is_imported_and_the_lists_are_the_released_ones(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _flags(monkeypatch)
    for m in (MODULE, "pravrudhi.application.cheque_case"):
        monkeypatch.delitem(sys.modules, m, raising=False)
    before = (identity.PARTNER_KEY_ROUTES, identity.PARTNER_KEY_PATHS, roles.USER_FACING)
    c = _client(tmp_path)
    off, unknown = c.post(PATH, json={"matter_id": "a" * 32}), c.post("/api/v1/no-such-route", json={"matter_id": "a" * 32})
    assert PATH not in _paths(c) and off.status_code == unknown.status_code == 404 and off.content == unknown.content
    assert MODULE not in sys.modules and "pravrudhi.application.cheque_case" not in sys.modules
    assert before == (identity.PARTNER_KEY_ROUTES, identity.PARTNER_KEY_PATHS, roles.USER_FACING)


def test_with_the_flag_on_the_route_exists_opens_the_gate_lists_and_is_not_in_openapi(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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


def test_a2_off_refuses_and_reads_nothing_even_when_a_store_folder_holds_a_matter(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _on(monkeypatch)
    m = _matter(_client(tmp_path), "cheque.pdf")
    _flags(monkeypatch, ChequeCaseConfig(), None)
    r = _client(tmp_path).post(PATH, json={"matter_id": m})
    assert r.status_code == 409 and r.json() == {"detail": "Stored papers are not available.", "error": "store_off"}


def test_an_anonymous_caller_is_told_it_is_for_signed_in_users(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _on(monkeypatch)
    r = _client(tmp_path, user=None).post(PATH, json={"matter_id": "a" * 32})
    assert r.status_code == 401 and "signed-in" in r.json()["detail"]


def test_without_b3_the_page_has_date_candidates_and_the_agenda_and_no_checklist(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from pravrudhi.application import cheque_case as CC

    _on(monkeypatch)
    c = _client(tmp_path)
    m = _matter(c, "complaint.pdf")
    r = c.post(PATH, json={"matter_id": m})
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["run_id"].startswith("cheque-") and b["matter_id"] == m and b["statement"] == CC.STATEMENT
    assert b["checklist"] is None and b["checklist_pending"] == CC.CHECKLIST_PENDING
    roles_ = b["candidates"]["roles"]  # Track A's shape; this route passes it through
    assert set(roles_) == set(CC.ROLES) and roles_["cheque"][0]["document_name"] == "complaint.pdf" and roles_["cheque"][0]["page"] == 1
    assert roles_["notice"][0]["value"] == "20.03.2013" and roles_["cheque"][0]["source"] == "complaint.pdf p.1"
    assert b["agenda"]["offence"]["id"] == "s138_ni_act" and b["agenda"]["papers_read"]["n"] == 1
    assert isinstance(b["not_assessed"], list) and b["not_assessed"]


def test_with_confirmed_dates_the_b3_checklist_is_added_and_a_bad_b3_body_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _on(monkeypatch)
    c = _client(tmp_path)
    m = _matter(c, "complaint.pdf")
    d = lambda v, iso: {"value": v, "source": "complaint.pdf p.1", "confirmed": True, "confirmed_as": iso}  # noqa: E731
    b3 = {
        "cheque_date": d("01.02.2013", "2013-02-01"), "validity_months": 3, "presentations": [d("10.03.2013", "2013-03-10")],
        "return_memos": [d("12.03.2013", "2013-03-12")], "information_received": [d("12.03.2013", "2013-03-12")],
        "notices": [d("20.03.2013", "2013-03-20")], "notice_receipts": [d("21.03.2013", "2013-03-21")],
    }
    r = c.post(PATH, json={"matter_id": m, "b3": b3})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["checklist"] is not None and out["checklist"]["run_id"] == out["run_id"] and out["checklist_pending"] is None
    assert [s["step"] for s in out["checklist"]["steps"]][:3] == ["presentation", "notice", "payment"]
    bad = c.post(PATH, json={"matter_id": m, "b3": {**b3, "extra": 1}})
    assert bad.status_code == 422 and bad.json() == {"error": "bad_request"}


def test_another_tenants_matter_is_exactly_as_not_found_as_none_and_a_matter_with_no_papers_is_409(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _on(monkeypatch)
    c = _client(tmp_path)
    m = _matter(c, "zebra-secret.pdf")
    foreign = _client(tmp_path, user=USER2).post(PATH, json={"matter_id": m})
    none = _client(tmp_path, user=USER2).post(PATH, json={"matter_id": "b" * 32})
    assert foreign.status_code == none.status_code == 404 and foreign.content == none.content and "zebra" not in foreign.text
    empty = c.post(f"{A2}/matters", json={}).json()["matter_id"]
    r = c.post(PATH, json={"matter_id": empty})
    assert r.status_code == 409 and r.json()["error"] == "no_papers"


@pytest.mark.parametrize("body", [{}, {"matter_id": "x"}, {"matter_id": "a" * 32, "extra": 1}, {"matter_id": "a" * 32, "paper_types": [1]}, {"matter_id": "a" * 32, "b3": "x"}])
def test_every_malformed_body_gets_the_one_fixed_422(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: dict[str, Any]) -> None:
    _on(monkeypatch)
    r = _client(tmp_path).post(PATH, json=body)
    assert r.status_code == 422 and r.json() == {"error": "bad_request"}


def test_a_bad_declared_paper_type_is_the_agendas_own_422(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _on(monkeypatch)
    c = _client(tmp_path)
    m = _matter(c, "complaint.pdf")
    r = c.post(PATH, json={"matter_id": m, "paper_types": {"f" * 32: "cheque"}})
    assert r.status_code == 422 and r.json()["error"] == "bad_paper_type"


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
    return TestClient(create_app(root), headers={"host": "127.0.0.1:8008"}, raise_server_exceptions=False), key.secret, root


def test_a_second_key_of_the_same_org_passes_another_orgs_key_gets_404_and_every_status_leaves_a_cheque_prefixed_audit_row(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    c, secret, root = _keyed(monkeypatch, tmp_path)
    second = tenancy.create_key(root, "acme", label="second").secret
    tenancy.create_org(root, "zorbland", "Zorbland")
    other = tenancy.create_key(root, "zorbland", label="o").secret
    h1, h2, h3 = ({tenancy.API_KEY_HEADER: k} for k in (secret, second, other))
    m = c.post(f"{A2}/matters", json={}, headers=h1).json()["matter_id"]
    assert c.post(f"{A2}/documents", json={"matter_id": m, "text": PAPER, "name": "marker-name.pdf"}, headers=h1).status_code == 200
    assert c.post(PATH, json={"matter_id": m}, headers=h1).status_code == 200
    assert c.post(PATH, json={"matter_id": m}, headers=h2).status_code == 200
    assert c.post(PATH, json={"matter_id": m}, headers=h3).status_code == 404
    assert c.post(PATH, json={"matter_id": m}).status_code in (401, 403)
    key_id = tenancy.keys_for_org(root, "acme")[0].key_id
    rows, _n = audit.page(root, key_id, offset=0, limit=100, retention_s=3600, now=lambda: datetime.now(UTC))
    mine = [r for r in rows if str(r.get("run_id", "")).startswith("cheque-")]
    assert len(mine) >= 1 and all(r["mode"] == "intake" and re.fullmatch(r"cheque-[0-9a-f]{10}", r["run_id"]) and "marker" not in repr(r) for r in mine)
