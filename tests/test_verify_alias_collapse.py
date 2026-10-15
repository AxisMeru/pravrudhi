"""A bare citation whose alias groups are SPELLINGS of one case (a leading filler word, a trailing "and Another", an
abbreviation, an OCR slip) is not a CONFLICT; a key shared by genuinely different cases still is. Invented parties only.
Both arms of each rule. The SCC supplementary-volume form parses and, being a reporter the index does not mine, answers
NOT_IN_INDEX (never MALFORMED)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from pravrudhi.api.partner import PartnerApiConfig, build_partner_router
from pravrudhi.application.case_index import CaseRecord, insert_case, open_index
from pravrudhi.application.citations import parse_citations
from pravrudhi.application.verify import VerifyResult, _same_word, exists_in_index, verify

KEY = "(2083) 4 SCC 512"
PASSAGE = "a wholly different holding about arbitration clauses and their severability"


def _db(tmp_path: Path, name: str, groups: list[tuple[str, str]], cases: list[tuple[str, str, int]]) -> sqlite3.Connection:
    conn = open_index(tmp_path / f"{name}.sqlite3")
    insert_case(conn, CaseRecord("citer", "Later vs Other", "Supreme Court", 2095, "sc_pdf", "", PASSAGE))
    for cid, title, year in cases:
        insert_case(conn, CaseRecord(cid, title, "Supreme Court", year, "sc_pdf", "", PASSAGE))
    for p1, p2 in groups:
        conn.execute(
            "INSERT INTO citation_aliases (party_1, party_2, citation, case_id) VALUES (?, ?, ?, 'citer')", (p1, p2, KEY)
        )
    conn.commit()
    return conn


def test_a_leading_filler_and_a_trailing_and_another_are_one_case_not_a_conflict(tmp_path: Path) -> None:
    conn = _db(
        tmp_path,
        "filler",
        [
            ("Aldric Vennoway", "State of Zorbland"),
            ("In Aldric Vennoway", "State of Zorbland and Another"),
            ("Aldric\nVennoway", "State of Zorbland"),
        ],
        [("real", "Aldric Vennoway vs State Of Zorbland Anr", 2083)],
    )
    assert exists_in_index(conn, KEY) == VerifyResult.IN_INDEX


def test_an_abbreviation_spelling_of_a_party_is_one_case(tmp_path: Path) -> None:
    conn = _db(
        tmp_path,
        "abbr",
        [
            ("Quillfeather Oil Corpn.", "Mendelsohn India Ltd."),
            ("Quillfeather Oil Corporation", "Mendelsohn India Ltd. and Others"),
        ],
        [("real", "M S Quillfeather Oil Corporation vs M S Mendelsohn India Ltd", 2083)],
    )
    assert exists_in_index(conn, KEY) == VerifyResult.IN_INDEX


def test_an_ocr_slip_group_that_resolves_to_no_case_folds_into_the_group_that_does(tmp_path: Path) -> None:
    conn = _db(
        tmp_path,
        "ocr",
        [("Brandelwick Quillson", "State of Zorbland"), ("Brandelwiack Quillson", "State of Zorbland")],
        [("real", "Brandelwick Quillson vs State Of Zorbland", 2083)],
    )
    assert exists_in_index(conn, KEY) == VerifyResult.IN_INDEX


def test_a_group_whose_matches_all_fail_the_title_test_is_dropped(tmp_path: Path) -> None:
    # the second group's first-word lookup finds other cases, none carries the cited parties: dropped, as _narrow_conflict does
    conn = _db(
        tmp_path,
        "swarm",
        [("Marlowenton Thackeray", "State of Zorbland"), ("His Holiness Marlowenton Thackeray", "State of Zorbland")],
        [
            ("real", "Marlowenton Thackeray vs State Of Zorbland", 2083),
            ("noise1", "His Holiness Somebody Else vs The State Of Zorbland", 2083),
        ],
    )
    assert exists_in_index(conn, KEY) == VerifyResult.IN_INDEX


# ---- the other arm: nothing here may turn a real ambiguity into an answer ----
def test_two_groups_that_resolve_to_two_different_checkable_cases_stay_a_conflict(tmp_path: Path) -> None:
    conn = _db(
        tmp_path,
        "two_cases",
        [("Aldric Vennoway", "State of Zorbland"), ("Zorbatha Quillfeather", "Mendelsohn Aerospace")],
        [("a", "Aldric Vennoway vs State Of Zorbland", 2083), ("b", "Zorbatha Quillfeather vs Mendelsohn Aerospace", 2083)],
    )
    assert exists_in_index(conn, KEY) == VerifyResult.CONFLICT


def test_an_unresolved_group_that_is_not_a_spelling_of_a_resolved_one_keeps_the_conflict(tmp_path: Path) -> None:
    conn = _db(
        tmp_path,
        "unrelated_unresolved",
        [("Aldric Vennoway", "State of Zorbland"), ("Zorbatha Quillfeather", "Mendelsohn Aerospace")],
        [("a", "Aldric Vennoway vs State Of Zorbland", 2083)],
    )
    assert exists_in_index(conn, KEY) == VerifyResult.CONFLICT


@pytest.mark.parametrize(
    ("indexed", "unindexed"),
    [
        ("Rajesh Kumar", "Rajesh Kumari"),  # different person's name, not a spelling
        ("Anil Sharma", "Anil Sharmila"),
        ("Mohan Lal", "Mohan Lall"),
        ("Rajesh Kumar Gupta", "Kumar"),  # a short form that drops words
        ("Rajesh Kumar Gupta", "Rajesh Kumar"),
    ],
)
def test_a_different_persons_name_is_not_a_spelling_of_an_indexed_one(tmp_path: Path, indexed: str, unindexed: str) -> None:
    conn = _db(
        tmp_path,
        "persons",
        [(indexed, "State of Uttarpur"), (unindexed, "State of Uttarpur")],
        [("a", f"{indexed} vs State Of Uttarpur", 2083)],
    )
    assert exists_in_index(conn, KEY) == VerifyResult.CONFLICT


def test_a_slip_in_a_long_name_is_a_spelling_but_a_short_name_slip_is_not(tmp_path: Path) -> None:
    for name, slip, want in [
        ("Marlowenton Thackeray", "Marlowenton Thackerey", VerifyResult.IN_INDEX),
        ("Anil Sharma", "Anil Sharna", VerifyResult.CONFLICT),
    ]:
        conn = _db(
            tmp_path,
            f"slip_{slip[-6:]}",
            [(name, "State of Zorbland"), (slip, "State of Zorbland")],
            [("a", f"{name} vs State Of Zorbland", 2083)],
        )
        assert exists_in_index(conn, KEY) == want, slip


def test_a_near_spelling_on_one_side_only_is_not_enough_to_fold_an_unresolved_group(tmp_path: Path) -> None:
    conn = _db(
        tmp_path,
        "one_side",
        [("Aldric Vennoway", "State of Zorbland"), ("Aldricx Vennoway", "Mendelsohn Aerospace")],
        [("a", "Aldric Vennoway vs State Of Zorbland", 2083)],
    )
    assert exists_in_index(conn, KEY) == VerifyResult.CONFLICT


def test_no_group_with_a_checkable_case_keeps_the_conflict(tmp_path: Path) -> None:
    conn = _db(
        tmp_path, "none", [("Aldric Vennoway", "State of Zorbland"), ("Zorbatha Quillfeather", "Mendelsohn Aerospace")], []
    )
    assert exists_in_index(conn, KEY) == VerifyResult.CONFLICT


def test_a_case_outside_the_year_window_is_not_a_survivor(tmp_path: Path) -> None:
    conn = _db(
        tmp_path,
        "year",
        [("Aldric Vennoway", "State of Zorbland"), ("In Aldric Vennoway", "State of Zorbland and Another")],
        [("old", "Aldric Vennoway vs State Of Zorbland", 2040)],
    )
    assert exists_in_index(conn, KEY) == VerifyResult.CONFLICT


def test_two_cases_between_the_same_parties_are_still_not_told_apart(tmp_path: Path) -> None:
    conn = _db(
        tmp_path,
        "twins",
        [("Aldric Vennoway", "State of Zorbland"), ("In Aldric Vennoway", "State of Zorbland and Another")],
        [("a", "Aldric Vennoway vs State Of Zorbland", 2083), ("b", "Aldric Vennoway vs State Of Zorbland", 2083)],
    )
    assert exists_in_index(conn, KEY) == VerifyResult.CONFLICT


def test_a_title_that_adds_words_no_group_names_keeps_the_conflict(tmp_path: Path) -> None:
    conn = _db(
        tmp_path,
        "extra",
        [("Aldric Vennoway", "State of Zorbland"), ("In Aldric Vennoway", "State of Zorbland and Another")],
        [("a", "Aldric Vennoway vs State Of Zorbland Wxyzwordaaa Wxyzwordbbb", 2083)],
    )
    assert exists_in_index(conn, KEY) == VerifyResult.CONFLICT  # no group names the title's extra words: nothing collapses


def test_verify_with_a_quote_is_unchanged_by_the_collapse(tmp_path: Path) -> None:
    conn = _db(
        tmp_path,
        "verify_same",
        [("Zorbatha Quillfeather", "Mendelsohn Aerospace"), ("Aldric Vennoway", "State of Zorbland")],
        [("a", "Aldric Vennoway vs State Of Zorbland", 2083), ("b", "Zorbatha Quillfeather vs Mendelsohn Aerospace", 2083)],
    )
    assert verify(conn, KEY, "nothing like it") == VerifyResult.CONFLICT


# ---- the supplementary-volume form ----
@pytest.mark.parametrize(
    "text",
    ["1992 Supp (1) SCC 335", "(1992) Supp (1) SCC 335", "1992 Supp(1) SCC 335", "1992 SUPP (1) SCC 335", "1992 Supp. (2) SCC 5"],
)
def test_the_supp_form_parses_as_one_citation(text: str) -> None:
    got = parse_citations(text)
    assert len(got) == 1 and got[0].reporter == "SCC Supp" and got[0].year == 1992


def test_a_plain_scc_citation_and_a_supp_one_are_told_apart() -> None:
    both = parse_citations("(2014) 8 SCC 273 and 1992 Supp (1) SCC 335")
    assert [(c.reporter, c.year) for c in both] == [("SCC", 2014), ("SCC Supp", 1992)] or [
        (c.reporter, c.year) for c in both
    ] == [("SCC Supp", 1992), ("SCC", 2014)]


def test_a_supp_citation_is_not_in_index_never_malformed(tmp_path: Path) -> None:
    conn = _db(
        tmp_path, "supp", [("Aldric Vennoway", "State of Zorbland")], [("a", "Aldric Vennoway vs State Of Zorbland", 2083)]
    )
    assert exists_in_index(conn, "1992 Supp (1) SCC 335") == VerifyResult.NOT_IN_INDEX
    assert verify(conn, "1992 Supp (1) SCC 335", "any words") == VerifyResult.NOT_IN_INDEX
    # two citations in one entry are still malformed
    assert exists_in_index(conn, "1992 Supp (1) SCC 335 and (2083) 4 SCC 512") == VerifyResult.MALFORMED


def test_the_route_answers_not_in_index_for_a_supp_citation(tmp_path: Path) -> None:
    p = tmp_path / "idx.sqlite3"
    conn = open_index(p)
    insert_case(conn, CaseRecord("a", "Aldric Vennoway vs State Of Zorbland", "Supreme Court", 2083, "sc_pdf", "", PASSAGE))
    conn.commit()
    conn.close()
    app = FastAPI()
    app.include_router(
        build_partner_router(
            tmp_path,
            config=PartnerApiConfig(rate_limit_per_minute=1000, max_concurrent=100, trust_proxy_header=False),
            citation_index_path=p,
        )
    )
    r = TestClient(app).post("/api/v1/verify-citations", json={"citation": "1992 Supp (1) SCC 335"})
    assert r.status_code == 200 and r.json()["result"] == "NOT_IN_INDEX"


@pytest.mark.parametrize(
    ("a", "b", "same"),
    [
        # a different person's name, never a spelling
        ("kumar", "kumari", False),
        ("sharma", "sharmila", False),
        ("lal", "lall", False),
        ("das", "dass", False),
        ("devi", "deva", False),
        ("rajesh", "rajash", False),
        ("sharma", "sharna", False),
        ("brandelwick", "xbrandelwick", False),
        ("brandelwick", "brandelwiqx", False),  # two letters changed
        # slips seen on real alias spellings of one case: a dropped or added letter, a changed letter, two letters swapped
        ("gujarat", "gujrat", True),
        ("vellorian", "velorian", True),
        ("tarmaset", "tharmaset", True),
        ("quillmara", "quillmar", True),
        ("brelwaith", "brelwiath", True),
        ("morvati", "morvti", True),
        ("zerovananda", "zerohvananda", True),
        ("thackeray", "thackerey", True),
        ("corpn", "corporation", True),
        ("zerovananda", "zerovananda", True),
    ],
)
def test_which_word_pairs_count_as_one_name(a: str, b: str, same: bool) -> None:
    assert _same_word(a, b) is same and _same_word(b, a) is same


def test_a_shortened_form_of_a_rare_name_is_folded_but_a_shortened_common_name_is_not(tmp_path: Path) -> None:
    rare = _db(
        tmp_path,
        "rare",
        [("Marlowenton Thackeray Sripadagalvaru", "State of Zorbland"), ("Marlowenton Thackeray", "State of Zorbland")],
        [("a", "Marlowenton Thackeray Sripadagalvaru vs State Of Zorbland", 2083)],
    )
    assert exists_in_index(rare, KEY) == VerifyResult.IN_INDEX
    common = _db(
        tmp_path,
        "common",
        [("Anil Sharma Gupta", "State of Zorbland"), ("Anil Sharma", "State of Zorbland")],
        [("a", "Anil Sharma Gupta vs State Of Zorbland", 2083)],
    )
    assert exists_in_index(common, KEY) == VerifyResult.CONFLICT


def test_a_shortened_form_that_keeps_only_a_generic_long_word_is_not_folded(tmp_path: Path) -> None:
    conn = _db(
        tmp_path,
        "generic",
        [("Hallowbrook Governor", "State of Zorbland"), ("Governor", "State of Zorbland")],
        [("a", "Hallowbrook Governor vs State Of Zorbland", 2083)],
    )
    assert exists_in_index(conn, KEY) == VerifyResult.CONFLICT


def test_an_unrelated_group_that_reaches_other_cases_keeps_conflict(tmp_path: Path) -> None:
    # the second group's lookup finds unrelated cases (none titled with its parties): as uncheckable as a group with no case
    conn = _db(
        tmp_path,
        "junk_rows",
        [("Aldric Vennoway", "State of Zorbland"), ("Zorbatha Quillfeather", "Mendelsohn Aerospace")],
        [("a", "Aldric Vennoway vs State Of Zorbland", 2083), ("noise", "Zorbatha Someone vs Mendelsohn Else", 2083)],
    )
    assert exists_in_index(conn, KEY) == VerifyResult.CONFLICT


def test_a_printed_prefix_is_ignored_when_a_shortened_rare_name_is_compared(tmp_path: Path) -> None:
    # without ignoring "His Holiness" neither word set contains the other, so the group would keep the conflict
    conn = _db(
        tmp_path,
        "prefix",
        [
            ("Marlowenton Thackeray Sripadagalvaru", "State of Zorbland"),
            ("His Holiness Marlowenton Thackeray", "State of Zorbland"),
        ],
        [("a", "Marlowenton Thackeray Sripadagalvaru vs State Of Zorbland", 2083)],
    )
    assert exists_in_index(conn, KEY) == VerifyResult.IN_INDEX
