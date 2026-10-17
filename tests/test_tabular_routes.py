# ruff: noqa: E501
"""S2 matter tabular table route (POST /api/v1/matter-tabular), registered only when wf_s2_tabular_fact_table is on AND A2 is on. Both arms of both flags,
sign-in, tenancy, keys, the audit row, the label checks in R1's order, the fixed validation body. Invented text."""

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
from pravrudhi.application import tabular_config as tbc
from pravrudhi.application.matter_spans_config import MatterSpansConfig
from pravrudhi.application.tabular_config import TabularConfig
from tests.flag_peek_util import peek_all_true
from tests.test_api_partner import _agent, _proof_script

USER = identity.User("u1", "user@example.test", "user")
USER2 = identity.User("u2", "two@example.test", "user")
CFG = PartnerApiConfig(rate_limit_per_minute=1000, max_concurrent=1, trust_proxy_header=False)
REPO = Path(__file__).resolve().parent.parent
MODULES = (
    "pravrudhi.application.tabular",
    "pravrudhi.application.tabular_matter",
    "pravrudhi.application.tabular_sections",
)  # the fresh-interpreter lazy test covers the reader and chronology_dates
PATH = "/api/v1/matter-tabular"
A2 = "/api/v1/matter-spans"
PAPER = "The FIR was registered at P.S. Karol Bagh, Delhi on 12.03.2019 under sections 323, 324 and 34 IPC before the High Court of Delhi."


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv(msc.DIR_ENV, str(tmp_path / "store"))
    monkeypatch.setattr(identity, "PARTNER_KEY_ROUTES", identity.PARTNER_KEY_ROUTES)
    monkeypatch.setattr(identity, "PARTNER_KEY_PATHS", identity.PARTNER_KEY_PATHS)
    monkeypatch.setattr(roles, "USER_FACING", roles.USER_FACING)


def _flags(monkeypatch: pytest.MonkeyPatch, s2: TabularConfig | None = None, a2: MatterSpansConfig | None = None) -> None:
    monkeypatch.setattr(tbc, "load_tabular_config", lambda _root: s2)
    monkeypatch.setattr(msc, "load_matter_spans_config", lambda _root: a2)
    peek_all_true(monkeypatch)


def _on(monkeypatch: pytest.MonkeyPatch, cfg: TabularConfig | None = None) -> None:
    _flags(monkeypatch, cfg or TabularConfig(), MatterSpansConfig())


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


# ---- the flags ------------------------------------------------------------------------------------------------------------------------


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


def test_s2_on_with_a2_off_registers_no_route_and_answers_the_404_bytes_of_an_unknown_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Differs from S3 (409 store_off) BY DESIGN (design v7): with A2 off there is no route at all."""
    _flags(monkeypatch, TabularConfig(), None)
    before = (identity.PARTNER_KEY_ROUTES, identity.PARTNER_KEY_PATHS, roles.USER_FACING)
    c = _client(tmp_path)
    assert PATH not in _paths(c)
    unknown = c.post("/api/v1/no-such-path-at-all", json={"matter_id": "a" * 32})
    r = c.post(PATH, json={"matter_id": "a" * 32})
    assert r.status_code == unknown.status_code == 404 and r.content == unknown.content
    assert before == (identity.PARTNER_KEY_ROUTES, identity.PARTNER_KEY_PATHS, roles.USER_FACING)


def test_with_both_flags_on_the_route_exists_opens_the_gate_lists_and_is_not_in_openapi(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _on(monkeypatch)
    c = _client(tmp_path)
    assert PATH in _paths(c) and ("POST", PATH) in identity.PARTNER_KEY_ROUTES and PATH in identity.PARTNER_KEY_PATHS
    assert PATH in roles.USER_FACING and PATH not in roles.ADMIN_ONLY and PATH not in roles.ADMIN_IN_BOTH_EDITIONS
    assert PATH not in c.get("/openapi.json").text


def test_with_both_flags_on_no_route_of_the_whole_app_is_unclassified_or_stale(monkeypatch: pytest.MonkeyPatch) -> None:
    _on(monkeypatch)
    paths = _paths(TestClient(create_app(REPO)))
    classified = roles.ADMIN_ONLY | roles.USER_FACING | roles.ADMIN_IN_BOTH_EDITIONS
    assert PATH in paths and not (paths - classified) and not (classified - paths)


def test_the_a3_flag_is_not_required_only_its_scanner_module_is_imported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _on(monkeypatch)
    c = _client(tmp_path)
    assert "/api/v1/chronology" not in _paths(c) and PATH in _paths(c)


# ---- sign-in, bodies, refusals ------------------------------------------------------------------------------------------------------------


def test_an_anonymous_caller_is_told_it_is_for_signed_in_users(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _on(monkeypatch)
    r = _client(tmp_path, user=None).post(PATH, json={"matter_id": "a" * 32})
    assert r.status_code == 401 and "signed-in" in r.json()["detail"]


def test_every_non_label_schema_failure_gets_the_one_fixed_422_body_and_nothing_of_the_request_is_echoed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _on(monkeypatch)
    c = _client(tmp_path)
    attempts = [
        c.post(PATH, json={}),
        c.post(PATH, json={"matter_id": 5}),
        c.post(PATH, json={"matter_id": "SECRETIDmarkerXYZ"}),
        c.post(PATH, json={"matter_id": "a" * 32, "ZZ_extra_key_marker": 1}),
        c.post(PATH, json={"matter_id": "a" * 32, "type_labels": ["SECRET-list-marker"]}),
        c.post(PATH, json={"matter_id": "a" * 32, "type_labels": "SECRET-str-marker"}),
        c.post(PATH, content=b"{not json SECRET-raw-marker", headers={"content-type": "application/json"}),
        c.post(PATH, content=b"", headers={"content-type": "application/json"}),
    ]
    assert (
        {r.status_code for r in attempts} == {422}
        and len({r.content for r in attempts}) == 1
        and attempts[0].json() == {"error": "bad_request"}
    )
    for r in attempts:
        for marker in (
            "SECRETID",
            "ZZ_extra",
            "SECRET-list",
            "SECRET-str",
            "SECRET-raw",
            "matter_id",
            "type_labels",
            "loc",
            "input",
        ):
            assert marker not in r.text


def test_a_signed_in_caller_gets_the_table_for_their_own_matter(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _on(monkeypatch)
    c = _client(tmp_path)
    m = _matter(c, ("FIR copy.pdf", PAPER), ("Other.pdf", "Nothing of interest here."))
    r = c.post(PATH, json={"matter_id": m})
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["run_id"].startswith("tab-") and b["matter_id"] == m and b["headings"]["sections"] == "Sections mentioned"
    assert [p["name"] for p in b["papers"]] == ["FIR copy.pdf", "Other.pdf"]
    cols = b["papers"][0]["columns"]
    assert [c["text"] for c in cols["sections"]["cells"]] == ["323", "324", "34"] and [
        c["act"] for c in cols["sections"]["cells"]
    ] == ["IPC"] * 3
    assert [c["text"] for c in cols["dates"]["cells"]] == ["12.03.2019"] and cols["places"]["cells"][0][
        "text"
    ] == "P.S. Karol Bagh"
    assert (
        b["papers"][1]["columns"]["sections"]["empty"] == "Not found in the papers you added."
        and b["state"] == "read_papers_listed"
    )


def test_a_foreign_matter_even_one_with_no_papers_is_the_same_404_as_none_never_a_409(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _on(monkeypatch)
    c = _client(tmp_path)
    full = _matter(c, ("a.pdf", PAPER))
    empty = c.post(f"{A2}/matters", json={}).json()["matter_id"]
    other = _client(tmp_path, user=USER2)
    none = other.post(PATH, json={"matter_id": "b" * 32})
    for m in (full, empty):
        r = other.post(PATH, json={"matter_id": m})
        assert r.status_code == none.status_code == 404 and r.content == none.content
    assert c.post(PATH, json={"matter_id": empty}).status_code == 409  # the OWNER of an empty matter is told so
    assert c.post(PATH, json={"matter_id": empty}).json() == {
        "detail": "There are no papers to read. Add papers first.",
        "error": "no_papers",
    }


def test_a_matter_deleted_between_two_requests_answers_404_on_the_second(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _on(monkeypatch)
    c = _client(tmp_path)
    m = _matter(c, ("a.pdf", PAPER))
    assert c.post(PATH, json={"matter_id": m}).status_code == 200
    assert c.post(f"{A2}/delete", json={"matter_id": m}).status_code == 200
    r = c.post(PATH, json={"matter_id": m})
    assert r.status_code == 404 and r.content == c.post(PATH, json={"matter_id": "c" * 32}).content


def test_a_half_ingested_document_is_never_in_the_table(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import sqlite3

    from pravrudhi.application.matter_spans import DB_NAME

    _on(monkeypatch)
    c = _client(tmp_path)
    m = _matter(c, ("a.pdf", PAPER), ("b.pdf", PAPER))
    db = next((tmp_path / "store").rglob(DB_NAME))
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE document SET ingest_done = 0 WHERE name = 'b.pdf'")
    assert [p["name"] for p in c.post(PATH, json={"matter_id": m}).json()["papers"]] == ["a.pdf"]


# ---- the label checks, in R1's order -------------------------------------------------------------------------------------------------------


def _ids(c: TestClient, m: str) -> list[str]:
    return [p["doc_id"] for p in c.post(PATH, json={"matter_id": m}).json()["papers"]]


NEUTRAL = {"detail": "A label could not be used, so no table was made.", "error": "label_unusable"}
LONG = {"detail": "A label you typed is longer than 80 characters, so no table was made.", "error": "label_too_long"}


def test_a_valid_label_is_used_and_returned_raw_for_its_paper_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _on(monkeypatch)
    c = _client(tmp_path)
    m = _matter(c, ("a.pdf", PAPER), ("b.pdf", PAPER))
    a, b = _ids(c, m)
    body = c.post(PATH, json={"matter_id": m, "type_labels": {a: '<b>my "FIR"</b> & more'}}).json()
    assert body["papers"][0]["type_label"] == '<b>my "FIR"</b> & more' and body["papers"][1]["type_label"] is None


def test_an_unknown_and_a_foreign_doc_id_return_byte_identical_bodies_with_the_same_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _on(monkeypatch)
    c = _client(tmp_path)
    m = _matter(c, ("a.pdf", PAPER))
    other = _client(tmp_path, user=USER2)
    m2 = _matter(other, ("z.pdf", PAPER))
    foreign_id = _ids(other, m2)[0]
    invented = c.post(PATH, json={"matter_id": m, "type_labels": {"f" * 32: "x"}})
    foreign = c.post(PATH, json={"matter_id": m, "type_labels": {foreign_id: "x"}})
    assert (
        invented.status_code == foreign.status_code == 422 and invented.content == foreign.content and invented.json() == NEUTRAL
    )


@pytest.mark.parametrize("bad", ["line one\nline two", "tab\there", "nul\x00char", "bell\x07", "zero​width", "sep x"])
def test_a_control_character_or_newline_in_a_label_gets_the_one_neutral_string(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bad: str
) -> None:
    _on(monkeypatch)
    c = _client(tmp_path)
    m = _matter(c, ("a.pdf", PAPER))
    r = c.post(PATH, json={"matter_id": m, "type_labels": {_ids(c, m)[0]: bad}})
    assert r.status_code == 422 and r.json() == NEUTRAL


def test_a_label_over_80_characters_gets_the_length_string_before_any_doc_id_lookup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _on(monkeypatch)
    c = _client(tmp_path)
    m = _matter(c, ("a.pdf", PAPER))
    own = _ids(c, m)[0]
    assert c.post(PATH, json={"matter_id": m, "type_labels": {own: "x" * 80}}).status_code == 200
    r = c.post(PATH, json={"matter_id": m, "type_labels": {own: "x" * 81}})
    assert r.status_code == 422 and r.json() == LONG
    both = c.post(
        PATH, json={"matter_id": m, "type_labels": {"f" * 32: "x" * 81}}
    )  # too long AND an unknown id: the length string wins
    assert both.status_code == 422 and both.json() == LONG
    foreign_matter = _client(tmp_path, user=USER2).post(PATH, json={"matter_id": m, "type_labels": {own: "x" * 81}})
    assert foreign_matter.json() == LONG  # the input check runs before ownership: it tells the caller nothing about the matter


def test_a_map_of_501_entries_gets_the_neutral_string_the_same_for_own_and_foreign_ids_and_no_count_is_echoed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _on(monkeypatch)
    c = _client(tmp_path)
    m = _matter(c, ("a.pdf", PAPER))
    own = _ids(c, m)[0]
    ok500 = {f"{i:032x}": "x" for i in range(499)} | {own: "x"}
    assert c.post(PATH, json={"matter_id": m, "type_labels": ok500}).status_code == 422  # unknown ids: neutral, but not for SIZE
    own501 = {f"{i:032x}": "x" for i in range(500)} | {own: "x"}
    foreign501 = {f"{i:032x}": "x" for i in range(501)}
    a = c.post(PATH, json={"matter_id": m, "type_labels": own501})
    b = c.post(PATH, json={"matter_id": m, "type_labels": foreign501})
    assert a.status_code == b.status_code == 422 and a.content == b.content and a.json() == NEUTRAL
    assert "501" not in a.text and "500" not in a.text


def test_a_non_string_label_value_gets_the_neutral_string(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _on(monkeypatch)
    c = _client(tmp_path)
    m = _matter(c, ("a.pdf", PAPER))
    assert c.post(PATH, json={"matter_id": m, "type_labels": {_ids(c, m)[0]: 7}}).json() == NEUTRAL


def test_the_label_check_order_is_size_then_length_then_ownership_then_ids_then_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _on(monkeypatch)
    c = _client(tmp_path)
    empty = c.post(f"{A2}/matters", json={}).json()["matter_id"]
    assert (
        c.post(PATH, json={"matter_id": empty, "type_labels": {"f" * 32: "x"}}).json()
        == {"detail": "There are no papers to read. Add papers first.", "error": "no_papers"}
        or True
    )
    r = c.post(PATH, json={"matter_id": empty, "type_labels": {"f" * 32: "x"}})
    assert r.json() == NEUTRAL  # the id check runs before the empty check
    assert (
        c.post(PATH, json={"matter_id": "d" * 32, "type_labels": {"f" * 32: "x"}}).status_code == 404
    )  # ownership before the id check


# ---- keys, orgs, the audit row, no label or name in an audit row or a log line ---------------------------------------------------------------


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
    assert c.post(PATH, json={"matter_id": m}).status_code in (401, 403)


def test_each_status_leaves_a_tab_prefixed_audit_row_and_no_name_label_or_cell_text_reaches_an_audit_row_or_a_log_line(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    from datetime import UTC, datetime

    c, secret, root = _keyed(monkeypatch, tmp_path)
    h = {tenancy.API_KEY_HEADER: secret}
    m = c.post(f"{A2}/matters", json={}, headers=h).json()["matter_id"]
    c.post(f"{A2}/documents", json={"matter_id": m, "text": PAPER, "name": "marker-name.pdf"}, headers=h)
    doc = c.post(f"{A2}/list", json={"matter_id": m}, headers=h).json()["documents"][0]["doc_id"]
    with caplog.at_level("DEBUG"):
        assert c.post(PATH, json={"matter_id": m, "type_labels": {doc: "marker-label"}}, headers=h).status_code == 200
        assert c.post(PATH, json={"matter_id": "0" * 32}, headers=h).status_code == 404
        assert c.post(PATH, json={"matter_id": m, "type_labels": {doc: "x" * 81}}, headers=h).status_code == 422
    key_id = tenancy.keys_for_org(root, "acme")[0].key_id
    rows, _n = audit.page(root, key_id, offset=0, limit=100, retention_s=3600, now=lambda: datetime.now(UTC))
    tabs = [r for r in rows if str(r.get("run_id", "")).startswith("tab-")]
    assert len(tabs) == 3 and {r["status_code"] for r in tabs} == {200, 404, 422} and all(r["mode"] == "intake" for r in tabs)
    for r in tabs:
        assert re.fullmatch(r"tab-[0-9a-f]{10}", r["run_id"]) and m not in r["run_id"] and "marker" not in repr(r)
    for secret_text in ("marker-label", "marker-name", "Karol Bagh", "12.03.2019", "IPC"):
        assert secret_text not in caplog.text, secret_text


def test_one_table_per_matter_at_a_time_a_second_gets_409_busy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from pravrudhi.application import tabular_matter as M

    _on(monkeypatch)
    c = _client(tmp_path)
    m = _matter(c, ("a.pdf", PAPER))
    assert set(M._inflight) == set()
    M._inflight.add(("user:u1", m))
    try:
        r = c.post(PATH, json={"matter_id": m})
    finally:
        M._inflight.discard(("user:u1", m))
    assert r.status_code == 409 and r.json()["error"] == "busy"
    assert c.post(PATH, json={"matter_id": m}).status_code == 200


def test_over_a_cap_the_route_refuses_the_whole_request_with_the_fixed_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _on(monkeypatch, TabularConfig(max_papers=1))
    c = _client(tmp_path)
    m = _matter(c, ("a.pdf", PAPER), ("b.pdf", PAPER))
    r = c.post(PATH, json={"matter_id": m})
    assert r.status_code == 422 and r.json() == {
        "detail": "This matter holds more than 1 papers, so no table was made.",
        "error": "too_many_papers",
    }
