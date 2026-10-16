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


def test_a_right_name_with_initials_matches_whatever_the_separator_sits_next_to(tmp_path: Path) -> None:
    conn = _db(tmp_path, "initials", [("Quillbert Sarma", "V.K.V. Sarma")], "Quillbert Sarma vs V K V Sarma")
    assert exists_in_index_detail(conn, f"Quillbert Sarma v. V. K. V. Sarma, {KEY}") == (VerifyResult.IN_INDEX, True)


def test_an_alias_group_with_one_generic_side_cannot_vouch_for_a_wrong_name(tmp_path: Path) -> None:
    conn = _db(tmp_path, "generic", [("State of Zorbland", "Union of India")], "State Of Zorbland vs Union Of India")
    assert exists_in_index_detail(conn, f"State of Zorbland Plains v. Brenwick Taplow, {KEY}") == (VerifyResult.NAME_MISMATCH, True)


@pytest.mark.parametrize(
    "typed",
    [
        "Per Aldric Vennoway v. State of Zorbland",
        "As held in Aldric Vennoway v. State of Zorbland",
        "Vide Aldric Vennoway v. State of Zorbland",
        "His Holiness Aldric Vennoway v. State of Zorbland",
        "Late Aldric Vennoway v. State of Zorbland",
        "State of Zorbland v. Aldric Vennoway",
    ],
)
def test_signal_words_honorifics_prose_and_order_do_not_make_a_right_name_wrong(tmp_path: Path, typed: str) -> None:
    conn = _db(tmp_path, "norm", ONE)
    assert exists_in_index_detail(conn, f"{typed}, {KEY}") == (VerifyResult.IN_INDEX, True), typed


def test_a_name_with_prose_around_it_is_found_but_not_claimed_as_compared(tmp_path: Path) -> None:
    conn = _db(tmp_path, "prose", ONE)
    assert exists_in_index_detail(conn, f"As discussed in the landmark ruling of Aldric Vennoway v. State of Zorbland, {KEY}") == (VerifyResult.IN_INDEX, False)


def test_uoi_is_union_of_india(tmp_path: Path) -> None:
    conn = _db(tmp_path, "uoi", [("Quillbert Marlowe", "Union of India")], "Quillbert Marlowe vs Union Of India")
    assert exists_in_index_detail(conn, f"Quillbert Marlowe v. UOI, {KEY}") == (VerifyResult.IN_INDEX, True)
    assert exists_in_index_detail(conn, f"Quillbert Marlowe v UOI, {KEY}") == (VerifyResult.IN_INDEX, True)
    assert exists_in_index_detail(conn, f"Brenwick Taplow v. UOI, {KEY}") == (VerifyResult.NAME_MISMATCH, True)


@pytest.mark.parametrize("typed", ["Mohammed", "Mohd.", "Muhammad", "Mohammad"])
def test_the_spellings_of_mohammad_are_one_name(tmp_path: Path, typed: str) -> None:
    conn = _db(tmp_path, "mohd", [("State of Zorbland", "Mohd. Yakubwane")], "State Of Zorbland vs Mohd Yakubwane")
    assert exists_in_index_detail(conn, f"State of Zorbland v. {typed} Yakubwane, {KEY}") == (VerifyResult.IN_INDEX, True)


def test_a_wrong_name_on_either_side_is_still_a_mismatch(tmp_path: Path) -> None:
    conn = _db(tmp_path, "oneside", ONE)
    assert exists_in_index(conn, f"Aldric Vennoway v. State of Qualdoria, {KEY}") == VerifyResult.NAME_MISMATCH
    assert exists_in_index(conn, f"Brenwick Taplow v. State of Zorbland, {KEY}") == VerifyResult.NAME_MISMATCH
    assert exists_in_index(conn, f"Per Brenwick Taplow v. State of Qualdoria, {KEY}") == VerifyResult.NAME_MISMATCH


def test_sharing_only_a_very_common_name_is_not_agreement(tmp_path: Path) -> None:
    conn = _db(tmp_path, "common_name", [("Jaswant Singh", "State of Zorbland")], "Jaswant Singh vs State Of Zorbland")
    assert exists_in_index_detail(conn, f"Jaswant Singh v. State of Zorbland, {KEY}") == (VerifyResult.IN_INDEX, True)
    assert exists_in_index_detail(conn, f"Nawal Singh v. State of Zorbland, {KEY}") == (VerifyResult.NAME_MISMATCH, True)
    assert exists_in_index_detail(conn, f"Ram Singh v. State of Zorbland, {KEY}") == (VerifyResult.NAME_MISMATCH, True)
    assert exists_in_index_detail(conn, f"Singh v. State of Zorbland, {KEY}") == (VerifyResult.IN_INDEX, False)  # not enough to claim a comparison


def test_an_initial_v_is_not_taken_for_the_separator(tmp_path: Path) -> None:
    conn = _db(tmp_path, "initial_v", [("K.V. Zorbrao", "L.H.V. Prasad"), ("K.V. Zorbrao", "L.H.V. Prasad & Ors.")], "K V Zorbrao vs L H V Prasad Ors")
    for typed in (
        "K V Zorbrao v. L H V Prasad Ors",
        "K V Zorbrao v L H V Prasad",
        "K.V. Zorbrao v. L.H.V. Prasad",
        "K V ZORBRAO V. L H V PRASAD",
        "Per K V Zorbrao vs. L H V Prasad and Ors.",
        "K. V. Zorbrao v. L. H. V. Prasad Ors",
        "K.V.Zorbrao v. L.H.V. Prasad",
        "As in K V Zorbrao v. L H V Prasad Ors",
        "Hon'ble K V Zorbrao v. L H V Prasad",
        "L H V Prasad Ors v. K V Zorbrao",
    ):
        assert exists_in_index_detail(conn, f"{typed}, {KEY}") == (VerifyResult.IN_INDEX, True), typed
    assert exists_in_index_detail(conn, f"K V Zorbrao v. L H V Mendelson, {KEY}") == (VerifyResult.NAME_MISMATCH, True)
    assert exists_in_index_detail(conn, f"K V Brenwick v. L H V Prasad, {KEY}") == (VerifyResult.NAME_MISMATCH, True)


def test_doubled_vowel_spellings_are_found_but_never_claimed_as_compared(tmp_path: Path) -> None:
    conn = _db(tmp_path, "vowels", [("Lalit Zorbland", "Om Kumar")], "Lalit Zorbland vs Om Kumar")
    assert exists_in_index_detail(conn, f"Laalit Zorbland v. Om Kumaar, {KEY}") == (VerifyResult.IN_INDEX, False)
    assert exists_in_index_detail(conn, f"Lalit Zorbland v. Om Kumari, {KEY}") == (VerifyResult.NAME_MISMATCH, True)


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
    assert mismatch["note"] == (
        "The citation resolves to an indexed case, but the case name given does not match that case. "
        "This does not show whether the citation or the name is right."
    )  # R1's wording, byte-exact
    assert "fake" not in mismatch["note"].lower()
    for citation in (KEY, "(2099) 1 SCC 1", f"The State v. Union of India, {KEY}"):
        assert "name_compared" not in _post(c, citation), citation  # no key at all: the reply is what it was before this field existed


def test_the_reply_keys_without_a_comparison_are_exactly_the_old_ones(tmp_path: Path) -> None:
    c = _client(tmp_path, "keys", ONE)
    assert set(_post(c, KEY)) == {"result", "note", "status", "label", "verified", "preview", "coverage"}


# ---- #509 three states: a comparison is claimed (name_compared) only when it was really made -----------------------------------

MARLOWE = [("Quillbert Marlowe", "Union of India")]


def _marlowe(tmp_path: Path) -> sqlite3.Connection:
    return _db(tmp_path, "marlowe", MARLOWE, "Quillbert Marlowe vs Union Of India")


def test_a_right_name_is_strong_and_claims_the_comparison(tmp_path: Path) -> None:
    conn = _marlowe(tmp_path)
    for typed in ("Quillbert Marlowe v. Union of India", "Quillbert Marlowe v. UOI", "Per Quillbert Marlowe v UOI", "Union of India v. Quillbert Marlowe"):
        assert exists_in_index_detail(conn, f"{typed}, {KEY}") == (VerifyResult.IN_INDEX, True), typed


@pytest.mark.parametrize(
    "typed",
    [
        "Quillbert Kapoor v. UOI",  # one shared given name
        "Mervyn Marlowe v. UOI",  # one shared surname
        "Quillbart Marlowe v. UOI",  # a one-letter slip only
        "Quillbert Marlowe v. State of Zorbland",  # the case's own words, with a different second party beside them
    ],
)
def test_a_half_matching_name_is_found_but_no_comparison_is_claimed(tmp_path: Path, typed: str) -> None:
    conn = _marlowe(tmp_path)
    assert exists_in_index_detail(conn, f"{typed}, {KEY}") == (VerifyResult.IN_INDEX, False), typed


def test_a_clearly_different_name_is_a_mismatch(tmp_path: Path) -> None:
    conn = _marlowe(tmp_path)
    for typed in ("Brenwick Taplow v. UOI", "Brenwick Taplow v. State of Qualdoria"):
        assert exists_in_index_detail(conn, f"{typed}, {KEY}") == (VerifyResult.NAME_MISMATCH, True), typed


def test_a_party_made_only_of_initials_is_never_agreement(tmp_path: Path) -> None:
    conn = _db(tmp_path, "inits", [("K.V. Zorbrao", "L.H.V. Prasad")], "K V Zorbrao vs L H V Prasad Ors")
    assert exists_in_index_detail(conn, f"L H V v. L H V Prasad, {KEY}") == (VerifyResult.IN_INDEX, False)
    assert exists_in_index_detail(conn, f"A B C v. L H V Prasad, {KEY}") == (VerifyResult.IN_INDEX, False)
    assert exists_in_index_detail(conn, f"P P Zorbrao v. L H V Prasad, {KEY}") == (VerifyResult.IN_INDEX, False)  # other initials
    assert exists_in_index_detail(conn, f"K V Zorbrao v. L H V Prasad, {KEY}") == (VerifyResult.IN_INDEX, True)


# ---- acronyms: a party typed as ONGC / LIC / DTC is never a disagreement by itself --------------------------------------------------

ZONGC = [("Zorbland Oil and Natural Gas Corporation Ltd.", "Quillfeather Pipes Ltd.")]
ZONGC_TITLE = "Zorbland Oil And Natural Gas Corporation Ltd vs Quillfeather Pipes Ltd"


@pytest.mark.parametrize(
    "typed",
    ["ZONGC v. Quillfeather Pipes", "ZONGC v. Quillfeather Pipes Ltd.", "Quillfeather Pipes v. ZONGC", "ZONGC Ltd v. Quillfeather Pipes"],
)
def test_an_acronym_that_is_the_initials_of_the_case_party_is_strong(tmp_path: Path, typed: str) -> None:
    conn = _db(tmp_path, "acro_ok", ZONGC, ZONGC_TITLE)
    assert exists_in_index_detail(conn, f"{typed}, {KEY}") == (VerifyResult.IN_INDEX, True), typed


def test_an_acronym_prefix_of_the_initials_counts_like_lic_for_life_insurance_corporation_of_india(tmp_path: Path) -> None:
    conn = _db(tmp_path, "acro_prefix", [("Aldric Vennoway Insurance Corporation of Zorbland", "Consumer Education Centre")], "Aldric Vennoway Insurance Corporation Of Zorbland vs Consumer Education Centre")
    assert exists_in_index_detail(conn, f"AVIC v. Consumer Education Centre, {KEY}") == (VerifyResult.IN_INDEX, True)
    assert exists_in_index_detail(conn, f"AVIC of India v. Consumer Education Centre, {KEY}") == (VerifyResult.IN_INDEX, True)


def test_a_wrong_acronym_is_not_a_mismatch_by_itself_and_not_a_comparison(tmp_path: Path) -> None:
    conn = _db(tmp_path, "acro_wrong", ZONGC, ZONGC_TITLE)
    assert exists_in_index_detail(conn, f"XYZW v. Quillfeather Pipes, {KEY}") == (VerifyResult.IN_INDEX, False)
    assert exists_in_index_detail(conn, f"ZONGC v. Other Pipes, {KEY}") == (VerifyResult.IN_INDEX, False)  # shares a word: the half-match state


def test_an_acronym_beside_a_clearly_different_other_party_is_a_mismatch(tmp_path: Path) -> None:
    conn = _db(tmp_path, "acro_other", ZONGC, ZONGC_TITLE)
    assert exists_in_index_detail(conn, f"ZONGC v. Brenwick Taplow, {KEY}") == (VerifyResult.NAME_MISMATCH, True)


@pytest.mark.parametrize("typed", ["Z.O.N.G.C. v. Quillfeather Pipes", "Z. O. N. G. C. v. Quillfeather Pipes Ltd."])
def test_a_dotted_acronym_is_never_a_mismatch(tmp_path: Path, typed: str) -> None:
    conn = _db(tmp_path, "acro_dots", ZONGC, ZONGC_TITLE)
    answer = exists_in_index_detail(conn, f"{typed}, {KEY}")
    assert answer.result == VerifyResult.IN_INDEX, typed


def test_a_dotted_acronym_beside_a_wrong_other_party_is_still_a_mismatch(tmp_path: Path) -> None:
    conn = _db(tmp_path, "acro_dots_wrong", ZONGC, ZONGC_TITLE)
    for typed in ("Z.O.N.G.C. v. Brenwick Taplow", "Z. O. N. G. C. v. Brenwick Taplow Ltd."):
        assert exists_in_index_detail(conn, f"{typed}, {KEY}") == (VerifyResult.NAME_MISMATCH, True), typed


def test_an_acronym_that_is_not_the_initials_of_a_leading_run_is_never_compared(tmp_path: Path) -> None:
    conn = _db(tmp_path, "acro_not_initials", ZONGC, ZONGC_TITLE)
    for typed in ("LIC v. Quillfeather Pipes", "NGC v. Quillfeather Pipes", "GCL v. Quillfeather Pipes", "XYZW v. Quillfeather Pipes"):
        answer = exists_in_index_detail(conn, f"{typed}, {KEY}")
        assert answer == (VerifyResult.IN_INDEX, False), typed  # found; not claimed as compared (NGC/GCL are not LEADING runs)
    other = exists_in_index_detail(conn, f"LIC v. Brenwick Taplow, {KEY}")
    assert other.result == VerifyResult.NAME_MISMATCH  # the other party decides


@pytest.mark.parametrize("title_word,typed_word", [("Hooda", "Hoda"), ("Dena", "Deena"), ("Deep", "Dep"), ("Hoda", "Hooda"), ("Deena", "Dena")])
def test_different_words_that_differ_in_a_doubled_vowel_are_never_strong(tmp_path: Path, title_word: str, typed_word: str) -> None:
    conn = _db(tmp_path, f"vow_{title_word}_{typed_word}", [(f"Quillbert {title_word}", "State of Zorbland")], f"Quillbert {title_word} vs State Of Zorbland")
    assert exists_in_index_detail(conn, f"Quillbert {title_word} v. State of Zorbland, {KEY}") == (VerifyResult.IN_INDEX, True)  # the right word is strong
    assert exists_in_index_detail(conn, f"Quillbert {typed_word} v. State of Zorbland, {KEY}") == (VerifyResult.IN_INDEX, False)  # the other word is not
