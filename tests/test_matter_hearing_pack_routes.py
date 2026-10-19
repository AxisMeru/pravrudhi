# ruff: noqa: E501
"""A7 matter hearing pack route (POST /api/v1/matter-hearing-pack), registered only when wf_a7_matter_hearing_pack is on; it needs A2 ON and calls A3's
chronology and A5's agenda as functions, plus S1's sweep only when S1 is also on and the citation index is available. Invented papers."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from pravrudhi.api import identity, roles
from pravrudhi.api.partner import PartnerApiConfig, build_partner_router
from pravrudhi.api.server import create_app
from pravrudhi.application import matter_hearing_pack_config as hpc
from pravrudhi.application import matter_spans_config as msc
from pravrudhi.application import matter_sweep_config as swc
from pravrudhi.application.case_index import CaseRecord, insert_case, open_index
from pravrudhi.application.matter_hearing_pack_config import MatterHearingPackConfig
from pravrudhi.application.matter_spans_config import MatterSpansConfig
from pravrudhi.application.matter_sweep_config import SweepConfig
from tests.flag_peek_util import peek_all_true
from tests.test_api_partner import _agent, _proof_script

USER = identity.User("u1", "user@example.test", "user")
USER2 = identity.User("u2", "two@example.test", "user")
CFG = PartnerApiConfig(rate_limit_per_minute=1000, max_concurrent=1, trust_proxy_header=False)
REPO = Path(__file__).resolve().parent.parent
MODULE = "pravrudhi.application.matter_hearing_pack_route"
PATH = "/api/v1/matter-hearing-pack"
A2 = "/api/v1/matter-spans"
GOOD = "(2087) 4 SCC 512"
MISSING = "(2086) 1 SCC 99"
PAPER = f"The matter is listed for hearing on 14.11.2026. The applicant relied on {GOOD} and also on {MISSING} before the court. The notice dated 20.03.2013 was sent."


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


def _flags(monkeypatch: pytest.MonkeyPatch, a7: MatterHearingPackConfig | None = None, a2: MatterSpansConfig | None = None, s1: SweepConfig | None = None) -> None:
    monkeypatch.setattr(hpc, "load_matter_hearing_pack_config", lambda _root: a7)
    monkeypatch.setattr(msc, "load_matter_spans_config", lambda _root: a2)
    monkeypatch.setattr(swc, "load_sweep_config", lambda _root: s1)
    peek_all_true(monkeypatch)


def _on(monkeypatch: pytest.MonkeyPatch, *, s1: bool = False) -> None:
    _flags(monkeypatch, MatterHearingPackConfig(), MatterSpansConfig(), SweepConfig() if s1 else None)


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


def _matter(c: TestClient, *names: str) -> str:
    m = c.post(f"{A2}/matters", json={}).json()["matter_id"]
    for n in names:
        assert c.post(f"{A2}/documents", json={"matter_id": m, "text": PAPER, "name": n}).status_code == 200
    return m


def test_with_the_flag_off_the_route_is_absent_nothing_is_imported_and_the_lists_are_the_released_ones(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _flags(monkeypatch)
    for m in (MODULE, "pravrudhi.application.matter_hearing_pack"):
        monkeypatch.delitem(sys.modules, m, raising=False)
    before = (identity.PARTNER_KEY_ROUTES, identity.PARTNER_KEY_PATHS, roles.USER_FACING)
    c = _client(tmp_path)
    off, unknown = c.post(PATH, json={"matter_id": "a" * 32}), c.post("/api/v1/no-such-route", json={"matter_id": "a" * 32})
    assert PATH not in _paths(c) and off.status_code == unknown.status_code == 404 and off.content == unknown.content
    assert MODULE not in sys.modules and "pravrudhi.application.matter_hearing_pack" not in sys.modules
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


def test_a2_off_refuses_and_an_anonymous_caller_is_told_it_is_for_signed_in_users(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _on(monkeypatch)
    m = _matter(_client(tmp_path), "complaint.pdf")
    _flags(monkeypatch, MatterHearingPackConfig(), None)
    r = _client(tmp_path).post(PATH, json={"matter_id": m})
    assert r.status_code == 409 and r.json() == {"detail": "Stored papers are not available.", "error": "store_off"}
    _on(monkeypatch)
    a = _client(tmp_path, user=None).post(PATH, json={"matter_id": m})
    assert a.status_code == 401 and "signed-in" in a.json()["detail"]


def test_without_s1_the_page_has_dates_and_the_not_added_papers_and_says_the_sweep_is_not_on(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from pravrudhi.application import matter_hearing_pack as HP

    _on(monkeypatch)
    c = _client(tmp_path)
    m = _matter(c, "complaint.pdf")
    r = c.post(PATH, json={"matter_id": m, "hearing_date": "2026-11-14"})
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["run_id"].startswith("hearing-") and b["matter_id"] == m and b["statement"] == HP.STATEMENT
    assert b["hearing_date_given"] == HP.DECLARED_DATE_LINE.format(d="2026-11-14")
    assert b["citations"]["switched_on"] is False and b["citations"]["line"] == HP.CITES_OFF and b["citations"]["counts"] is None
    assert b["timeline"] and isinstance(b["not_assessed"], list) and b["not_assessed"]


def test_with_s1_on_and_the_index_available_the_sweeps_unmatched_citations_are_on_the_page(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from pravrudhi.application import matter_hearing_pack as HP

    _on(monkeypatch, s1=True)
    c = _client(tmp_path)
    m = _matter(c, "complaint.pdf")
    r = c.post(PATH, json={"matter_id": m})
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["citations"]["switched_on"] is True and HP.CITES_OFF not in str(b["citations"])
    assert MISSING in str(b["citations"]["needs_attention"])  # the citation the index does not hold is listed to check


def test_s1_on_but_no_index_the_page_still_comes_back_without_a_sweep(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from pravrudhi.application import matter_hearing_pack as HP

    _on(monkeypatch, s1=True)
    monkeypatch.delenv("PRAVRUDHI_CITATION_INDEX", raising=False)
    c = _client(tmp_path)
    m = _matter(c, "complaint.pdf")
    r = c.post(PATH, json={"matter_id": m})
    assert r.status_code == 200 and r.json()["citations"]["line"] == HP.CITES_OFF and r.json()["citations"]["switched_on"] is False


def test_another_tenants_matter_is_exactly_as_not_found_as_none_and_an_empty_matter_is_409(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _on(monkeypatch)
    c = _client(tmp_path)
    m = _matter(c, "zebra-secret.pdf")
    foreign = _client(tmp_path, user=USER2).post(PATH, json={"matter_id": m})
    none = _client(tmp_path, user=USER2).post(PATH, json={"matter_id": "b" * 32})
    assert foreign.status_code == none.status_code == 404 and foreign.content == none.content and "zebra" not in foreign.text
    empty = c.post(f"{A2}/matters", json={}).json()["matter_id"]
    r = c.post(PATH, json={"matter_id": empty})
    assert r.status_code == 409 and r.json()["error"] == "no_papers"


@pytest.mark.parametrize(
    "body",
    [{}, {"matter_id": "x"}, {"matter_id": "a" * 32, "extra": 1}, {"matter_id": "a" * 32, "hearing_date": "14-11-2026"}, {"matter_id": "a" * 32, "hearing_date": "2026-13-45"}, {"matter_id": "a" * 32, "paper_types": [1]}],
)
def test_every_malformed_body_gets_the_one_fixed_422(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: dict[str, Any]) -> None:
    _on(monkeypatch)
    r = _client(tmp_path).post(PATH, json=body)
    assert r.status_code == 422 and r.json() == {"error": "bad_request"} and "2026-13" not in r.text
