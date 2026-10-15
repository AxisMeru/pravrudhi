"""#509: a typed case name is compared with the ONE case a citation resolves to. A name that does not match answers NAME_MISMATCH, never IN_INDEX.
Both arms of every rule; the reply field `name_compared` is present (true) only when a comparison happened. Invented parties only."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from pravrudhi.api.partner import PartnerApiConfig, build_partner_router
from pravrudhi.application.case_index import CaseRecord, insert_case, open_index
from pravrudhi.application.verify import VerifyResult, exists_in_index, exists_in_index_detail

KEY = "(2083) 4 SCC 512"
PASSAGE = "a wholly different holding about arbitration clauses and their severability"


def _db(tmp_path: Path, name: str, groups: list[tuple[str, str]], title: str = "Aldric Vennoway vs State Of Zorbland Anr") -> sqlite3.Connection:
    conn = open_index(tmp_path / f"{name}.sqlite3")
    insert_case(conn, CaseRecord("citer", "Later vs Other", "Supreme Court", 2095, "sc_pdf", "", PASSAGE))
    insert_case(conn, CaseRecord("real", title, "Supreme Court", 2083, "sc_pdf", "", PASSAGE))
    for p1, p2 in groups:
        conn.execute("INSERT INTO citation_aliases (party_1, party_2, citation, case_id) VALUES (?, ?, ?, 'citer')", (p1, p2, KEY))
    conn.commit()
    return conn


ONE = [("Aldric Vennoway", "State of Zorbland")]
MANY = [("Aldric Vennoway", "State of Zorbland"), ("In Aldric Vennoway", "State of Zorbland and Another"), ("Aldric\nVennoway", "State of Zorbland")]


@pytest.mark.parametrize("groups", [ONE, MANY], ids=["single group", "several groups collapsing to one case"])
def test_the_right_name_is_compared_and_matches(tmp_path: Path, groups: list[tuple[str, str]]) -> None:
    conn = _db(tmp_path, "right", groups)
    assert exists_in_index_detail(conn, f"Aldric Vennoway v. State of Zorbland, {KEY}") == (VerifyResult.IN_INDEX, True)


@pytest.mark.parametrize("groups", [ONE, MANY], ids=["single group", "several groups collapsing to one case"])
def test_a_wrong_name_is_a_mismatch_not_found(tmp_path: Path, groups: list[tuple[str, str]]) -> None:
    conn = _db(tmp_path, "wrong", groups)
    assert exists_in_index_detail(conn, f"Brenwick Taplow v. State of Qualdoria, {KEY}") == (VerifyResult.NAME_MISMATCH, True)
    assert exists_in_index(conn, f"Brenwick Taplow v. State of Qualdoria, {KEY}") == VerifyResult.NAME_MISMATCH


def test_a_name_that_shares_only_one_word_with_the_case_is_a_mismatch(tmp_path: Path) -> None:
    conn = _db(tmp_path, "oneword", ONE)
    assert exists_in_index(conn, f"Brenwick Vennoway v. State of Qualdoria, {KEY}") == VerifyResult.NAME_MISMATCH


def test_a_name_written_with_an_alias_spelling_matches(tmp_path: Path) -> None:
    conn = _db(tmp_path, "alias", [("Quillfeather Oil Corpn.", "Mendelsohn India Ltd."), ("Quillfeather Oil Corporation", "Mendelsohn India Ltd. and Others")], "M S Quillfeather Oil Corporation vs M S Mendelsohn India Ltd")
    assert exists_in_index_detail(conn, f"Quillfeather Oil Corporation v. Mendelsohn India Ltd., {KEY}") == (VerifyResult.IN_INDEX, True)
    assert exists_in_index_detail(conn, f"Quillfeather Oil Corpn. v. Mendelsohn India, {KEY}") == (VerifyResult.IN_INDEX, True)


def test_a_shortened_right_name_matches(tmp_path: Path) -> None:
    conn = _db(tmp_path, "short", ONE)
    assert exists_in_index_detail(conn, f"Aldric Vennoway v. Zorbland, {KEY}") == (VerifyResult.IN_INDEX, True)


def test_no_name_answers_as_before_and_says_nothing_was_compared(tmp_path: Path) -> None:
    conn = _db(tmp_path, "bare", ONE)
    assert exists_in_index_detail(conn, KEY) == (VerifyResult.IN_INDEX, False)


def test_a_name_with_no_distinctive_word_is_not_compared(tmp_path: Path) -> None:
    conn = _db(tmp_path, "common", ONE)
    assert exists_in_index_detail(conn, f"The State v. Union of India, {KEY}") == (VerifyResult.IN_INDEX, False)


def test_other_results_never_claim_a_comparison(tmp_path: Path) -> None:
    conn = _db(tmp_path, "other", ONE)
    assert exists_in_index_detail(conn, "(2099) 1 SCC 1") == (VerifyResult.NOT_IN_INDEX, False)
    assert exists_in_index_detail(conn, "Brenwick Taplow v. Qualdoria, (2099) 1 SCC 1") == (VerifyResult.NOT_IN_INDEX, False)
    assert exists_in_index_detail(conn, "Aldric Vennoway v. State of Zorbland, not a citation") == (VerifyResult.MALFORMED, False)


def test_the_quote_path_is_unchanged(tmp_path: Path) -> None:
    from pravrudhi.application.verify import verify

    conn = _db(tmp_path, "quote", ONE)
    assert verify(conn, f"Brenwick Taplow v. State of Qualdoria, {KEY}", "nothing like it") != VerifyResult.NAME_MISMATCH


# ---- the route: the field is present only when a name was compared, and the old replies are byte-identical -------------------------

_CFG = PartnerApiConfig(rate_limit_per_minute=1000, max_concurrent=100, trust_proxy_header=False)


def _client(tmp_path: Path, name: str, groups: list[tuple[str, str]]) -> TestClient:
    conn = _db(tmp_path, name, groups)
    conn.close()
    app = FastAPI()
    app.include_router(build_partner_router(tmp_path, config=_CFG, citation_index_path=tmp_path / f"{name}.sqlite3"))
    return TestClient(app)


def _post(c: TestClient, citation: str) -> dict:
    r = c.post("/api/v1/verify-citations", json={"citation": citation})
    assert r.status_code == 200
    return r.json()


def test_the_route_reports_name_compared_only_when_a_name_was_compared(tmp_path: Path) -> None:
    c = _client(tmp_path, "route", ONE)
    matched = _post(c, f"Aldric Vennoway v. State of Zorbland, {KEY}")
    assert (matched["result"], matched["name_compared"]) == ("IN_INDEX", True)
    mismatch = _post(c, f"Brenwick Taplow v. State of Qualdoria, {KEY}")
    assert (mismatch["result"], mismatch["status"], mismatch["label"], mismatch["verified"], mismatch["name_compared"]) == (
        "NAME_MISMATCH", "name_mismatch", "Case name did not match", False, True,
    )
    assert "does not match" in mismatch["note"] and "fake" not in mismatch["note"].lower()
    for citation in (KEY, "(2099) 1 SCC 1", f"The State v. Union of India, {KEY}"):
        assert "name_compared" not in _post(c, citation), citation  # no key at all: the reply is what it was before this field existed


def test_the_reply_keys_without_a_comparison_are_exactly_the_old_ones(tmp_path: Path) -> None:
    c = _client(tmp_path, "keys", ONE)
    assert set(_post(c, KEY)) == {"result", "note", "status", "label", "verified", "preview", "coverage"}
