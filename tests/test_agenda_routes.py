# ruff: noqa: E501
"""A5 review agenda route (POST /api/v1/review-agenda), registered only when wf_a5_ingredient_agenda is on; it needs A2 ON (its
flag, not the store folder). Both arms of both flags, sign-in, tenancy, the key-only gate, the refusal wording. Invented text."""

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
from pravrudhi.application import agenda_config as ac
from pravrudhi.application import chronology_config as cc
from pravrudhi.application import matter_spans_config as msc
from pravrudhi.application import s138_checklist_config as bc
from pravrudhi.application.agenda_config import AgendaConfig
from pravrudhi.application.matter_spans_config import MatterSpansConfig
from tests.flag_peek_util import peek_all_true
from tests.test_api_partner import _agent, _proof_script

USER = identity.User("u1", "user@example.test", "user")
USER2 = identity.User("u2", "two@example.test", "user")
CFG = PartnerApiConfig(rate_limit_per_minute=1000, max_concurrent=1, trust_proxy_header=False)
REPO = Path(__file__).resolve().parent.parent
MODULE = "pravrudhi.application.agenda_matter"
PATH = "/api/v1/review-agenda"
A2 = "/api/v1/matter-spans"
PAPER = "An invented paragraph about a cheque and a notice. Nothing else is stated here."


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv(msc.DIR_ENV, str(tmp_path / "store"))
    monkeypatch.setattr(identity, "PARTNER_KEY_ROUTES", identity.PARTNER_KEY_ROUTES)
    monkeypatch.setattr(identity, "PARTNER_KEY_PATHS", identity.PARTNER_KEY_PATHS)
    monkeypatch.setattr(roles, "USER_FACING", roles.USER_FACING)


def _flags(monkeypatch: pytest.MonkeyPatch, a5: AgendaConfig | None = None, a2: MatterSpansConfig | None = None) -> None:
    monkeypatch.setattr(ac, "load_agenda_config", lambda _root: a5)
    monkeypatch.setattr(msc, "load_matter_spans_config", lambda _root: a2)
    peek_all_true(monkeypatch)


def _on(monkeypatch: pytest.MonkeyPatch) -> None:
    _flags(monkeypatch, AgendaConfig(), MatterSpansConfig())


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


def _matter(c: TestClient, *names: str) -> tuple[str, list[str]]:
    m = c.post(f"{A2}/matters", json={}).json()["matter_id"]
    ids = []
    for n in names:
        r = c.post(f"{A2}/documents", json={"matter_id": m, "text": PAPER, "name": n})
        assert r.status_code == 200
        ids.append(r.json()["doc_id"])
    return m, ids


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


def test_a5_on_with_a2_off_refuses_and_reads_nothing_even_when_a_store_folder_holds_a_matter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _on(monkeypatch)
    m, _ = _matter(_client(tmp_path), "cheque.pdf")
    assert (tmp_path / "store").is_dir()
    _flags(monkeypatch, AgendaConfig(), None)
    r = _client(tmp_path).post(PATH, json={"matter_id": m})
    assert r.status_code == 409 and r.json() == {"detail": "Stored papers are not available.", "error": "store_off"}


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


def test_a_signed_in_caller_gets_the_agenda_for_their_own_matter_and_a_declared_type_is_used(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _on(monkeypatch)
    c = _client(tmp_path)
    m, ids = _matter(c, "scan_0034.pdf", "Demand notice.pdf")
    r = c.post(PATH, json={"matter_id": m, "paper_types": {ids[0]: "cheque"}})
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["run_id"].startswith("agenda-") and b["matter_id"] == m
    rows = {x["id"]: x for x in b["statute_names"]["rows"]}
    assert rows["cheque"]["status"] == "Added" and rows["notice_in_writing"]["status"].startswith("A file whose name suggests this paper was added")
    assert rows["notice_in_writing"]["papers"][0]["paper_type_basis"] == "Paper type taken from the file name"
    assert b["papers_read"]["heading"] == "Papers read (2)" and b["lawyer_listed"] is None


def test_another_tenants_matter_is_exactly_as_not_found_as_none(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _on(monkeypatch)
    m, _ = _matter(_client(tmp_path), "cheque.pdf")
    other = _client(tmp_path, user=USER2).post(PATH, json={"matter_id": m})
    none = _client(tmp_path, user=USER2).post(PATH, json={"matter_id": "b" * 32})
    assert other.status_code == none.status_code == 404 and other.json() == none.json()


def test_another_offence_is_refused_with_the_exact_words(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _on(monkeypatch)
    c = _client(tmp_path)
    m, _ = _matter(c, "cheque.pdf")
    r = c.post(PATH, json={"matter_id": m, "offence": "s420_ipc"})
    assert r.status_code == 422 and r.json()["error"] == "no_agenda"
    assert r.json()["detail"] == (
        "No agenda for this offence yet. For now this page covers cheque dishonour under s.138 only. For any other offence, a lawyer reads the papers."
    )


def test_a_paper_type_for_a_paper_that_is_not_in_the_matter_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _on(monkeypatch)
    c = _client(tmp_path)
    m, _ = _matter(c, "cheque.pdf")
    r = c.post(PATH, json={"matter_id": m, "paper_types": {"f" * 32: "cheque"}})
    assert r.status_code == 422 and r.json()["error"] == "bad_paper_type"


@pytest.mark.parametrize(
    "body",
    [{}, {"matter_id": 5}, {"matter_id": "x"}, {"matter_id": "a" * 32, "z": 1}, {"matter_id": "a" * 32, "paper_types": [1]}],
)
def test_a_bad_body_is_422_and_nothing_runs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: dict[str, Any]) -> None:
    _on(monkeypatch)
    assert _client(tmp_path).post(PATH, json=body).status_code == 422


def test_a_half_ingested_document_is_never_listed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import sqlite3

    from pravrudhi.application.matter_spans import DB_NAME

    _on(monkeypatch)
    c = _client(tmp_path)
    m, _ = _matter(c, "cheque.pdf")
    db = next((tmp_path / "store").rglob(DB_NAME))
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE document SET ingest_done = 0")
    assert c.post(PATH, json={"matter_id": m}).status_code == 409  # nothing committed: no papers


@pytest.mark.parametrize(("a3", "b3", "named"), [(True, True, True), (True, False, False), (False, True, False), (False, False, False)])
def test_the_ownership_line_appears_only_when_the_chronology_and_the_cheque_checklist_are_both_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, a3: bool, b3: bool, named: bool
) -> None:
    from pravrudhi.application.chronology_config import ChronologyConfig
    from pravrudhi.application.s138_checklist_config import S138ChecklistConfig

    _on(monkeypatch)
    monkeypatch.setattr(cc, "load_chronology_config", lambda _root: ChronologyConfig() if a3 else None)
    monkeypatch.setattr(bc, "load_s138_checklist_config", lambda _root: S138ChecklistConfig() if b3 else None)
    c = _client(tmp_path)
    m, _ = _matter(c, "cheque.pdf")
    line = c.post(PATH, json={"matter_id": m}).json()["ownership_line"]
    assert (line is not None) is named


def test_a_paper_held_without_a_file_is_declared_through_the_route(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _on(monkeypatch)
    c = _client(tmp_path)
    m, _ = _matter(c, "x.pdf")
    r = c.post(PATH, json={"matter_id": m, "papers_held_without_file": ["cheque"]})
    assert r.status_code == 200
    assert {x["id"]: x for x in r.json()["statute_names"]["rows"]}["cheque"]["status"] == "Declared by you; no file added"
    bad = c.post(PATH, json={"matter_id": m, "papers_held_without_file": ["zzz"]})
    assert bad.status_code == 422 and bad.json()["error"] == "bad_paper_type"
    assert c.post(PATH, json={"matter_id": m, "papers_held_without_file": [1]}).status_code == 422


def test_the_route_returns_a_file_name_raw_and_the_statute_words_with_their_footnote_marked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _on(monkeypatch)
    c = _client(tmp_path)
    m, _ = _matter(c, 'a<b>&"c" cheque.pdf')
    b = c.post(PATH, json={"matter_id": m}).json()
    assert b["papers_read"]["papers"][0]["name"] == 'a<b>&"c" cheque.pdf' and "&amp;" not in c.post(PATH, json={"matter_id": m}).text
    notice = next(i for i in b["ingredients"] if i["id"] == "notice")
    assert "[footnote 3:" in notice["statute_words"] and "3 [within thirty days]" in notice["statute_words_raw"]


def test_the_agenda_points_each_ingredient_at_the_stored_passages_that_use_its_words_and_leaks_nothing_across_tenants(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pravrudhi.application import agenda as G

    _on(monkeypatch)
    c = _client(tmp_path)
    m = c.post(f"{A2}/matters", json={}).json()["matter_id"]
    text = "The cheque was presented on 3 June and returned unpaid. A legal notice dated 12.04.2021 was sent by registered post."
    assert c.post(f"{A2}/documents", json={"matter_id": m, "text": text, "name": "complaint zq.pdf"}).status_code == 200
    b = c.post(PATH, json={"matter_id": m}).json()
    by = {i["id"]: i["where_mentioned"] for i in b["ingredients"]}
    assert by["notice"]["line"] == G.POINTER_LINE and by["notice"]["pointers"][0]["name"] == "complaint zq.pdf"
    assert by["returned_unpaid"]["pointers"][0]["cue"] == "returned unpaid" and text[by["notice"]["pointers"][0]["start"] : by["notice"]["pointers"][0]["end"]] == by["notice"]["pointers"][0]["quote"]
    assert by["non_payment"]["line"] == G.NO_POINTER_LINE
    other = _client(tmp_path, user=USER2).post(PATH, json={"matter_id": m})
    assert other.status_code == 404 and "zq" not in other.text and "legal notice" not in other.text  # another tenant sees nothing of the papers
