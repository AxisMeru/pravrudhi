# ruff: noqa: E501
"""S2: every fixed string R1 ruled is pinned by its sha256[:16] (R1 reads the PR head against these hashes); nothing the table says is barred."""

from __future__ import annotations

import hashlib

import pytest

from pravrudhi.application import tabular as T

H = {
    "TYPE_NOTE": "1cab3d74e633c8d9",
    "STANDING_LINE": "da5fa7808f159b9f",
    "UNDER_TABLE": "3154a2b0c7c97951",
    "EMPTY_CELL": "610791f151cd5b78",
    "NOT_ASSESSED": "763a42067b809b0a",
    "IPC_BNS_LINE": "545da734f339b018",
    "NON_ENGLISH_LINE": "4e05b5042124dfc5",
    "DATE_NOTE": "440823c3552274c1",
    "PAPER_TOO_LARGE": "f1da5a4e7f05c1b9",
    "TOO_MANY_PAPERS": "4853214ee1290a26",
    "TOO_MANY_CELLS": "8fb564d236739cc9",
    "TOO_BIG": "cec4fc5ceac97a8a",
    "TOO_SLOW": "ef4a17e5c3511c3d",
    "LABEL_TOO_LONG": "8619e1d9e44ebbc3",
    "LABEL_UNUSABLE": "307d026a469908d2",
    "TOO_MANY_PASSAGES": "0ff72f91e50584c7",
    "TOO_LONG_TEXT": "6b9252fcb559b260",
    "BUSY": "724eb3dc834d0209",
}
HEADING_HASHES = {
    "paper": "35045eff549e30b5",
    "type": "fdedbbdade1b61a7",
    "dates": "730d6fdcfb1e75a9",
    "sections": "7b51439118d2b979",
    "places": "51ddd620524e1e4d",
}
NOT_READ_HASHES = {
    ("act_not_named", 0): "46fd91ab7f11d9b4", ("act_not_named", 1): "593e7c372b0f404d", ("act_not_on_list", 0): "f74acbbb5dfa0e60",
    ("cut_at_storage_boundary", 0): "c0247844e0a177c7", ("name_not_clear", 0): "b0b00292ff6feaca", ("name_not_clear", 1): "0e86e2a201cb2cba",
    ("form_not_supported", 0): "870e04bc1cb9fce3", ("form_not_supported", 1): "d4c7edced2410f65",
}  # fmt: skip


def _h(t: str) -> str:
    return hashlib.sha256(t.encode()).hexdigest()[:16]


@pytest.mark.parametrize(("name", "expected"), sorted(H.items()))
def test_each_ruled_string_has_the_ruled_hash(name: str, expected: str) -> None:
    assert _h(getattr(T, name)) == expected


def test_the_headings_and_the_not_read_lines_have_the_ruled_hashes() -> None:
    assert {k: _h(v) for k, v in T.HEADINGS.items()} == HEADING_HASHES
    for (reason, i), h in NOT_READ_HASHES.items():
        assert _h(T.NOT_READ_LINES[reason][i]) == h, (reason, i)
    assert "named" not in " ".join(T.HEADINGS.values()).lower()  # R1: "named" is gone from every heading


def test_the_date_not_read_line_is_a3s_own_ruled_string() -> None:
    assert _h(T.DATE_NOT_READ_LINE) == "e45f99a1a4bcb443"


def test_the_empty_cell_is_one_string_in_code_and_never_none() -> None:
    assert T.EMPTY_CELL == "Not found in the papers you added." and "none" not in T.EMPTY_CELL.lower()


def test_the_state_tokens_hold_no_complete_or_all() -> None:
    assert T.STATES == ("read_papers_listed", "partly_read", "nothing_found")
    for tok in T.STATES:
        assert "complete" not in tok and "all" not in tok.split("_")


def test_the_cap_renders_as_a_size_never_raw_bytes() -> None:
    assert T.size_text(4_000_000) == "4 MB" and T.size_text(1_500_000) == "1500 KB"


def test_the_not_read_line_has_the_count_first_and_is_singular_for_one() -> None:
    assert T.not_read_line("act_not_named", 1) == "1 not read: a section number with no Act named beside it. Check it yourself."
    assert T.not_read_line("name_not_clear", 3) == "3 not read: police station names with no clear end. Check them yourself."


def test_no_ruled_string_outside_a_negation_holds_a_barred_word() -> None:
    quote_ok = {
        T.STANDING_LINE,
        T.UNDER_TABLE,
        T.NOT_ASSESSED,
        T.DATE_NOTE,
    }  # these name a barred word only to deny it ("does not say what ... means")
    own = [
        T.TYPE_NOTE,
        T.EMPTY_CELL,
        T.IPC_BNS_LINE,
        T.NON_ENGLISH_LINE,
        T.NO_PAPERS,
        T.STORE_OFF,
        *[x for pair in T.NOT_READ_LINES.values() for x in pair],
    ]
    own += [
        T.PAPER_TOO_LARGE,
        T.TOO_MANY_PAPERS,
        T.TOO_MANY_CELLS,
        T.TOO_BIG,
        T.TOO_SLOW,
        T.LABEL_TOO_LONG,
        T.LABEL_UNUSABLE,
    ]
    own += [T.TOO_MANY_PASSAGES, T.TOO_LONG_TEXT, T.BUSY, *T.HEADINGS.values()]
    for s in own:
        assert T.barred_in(s) == [], (s, T.barred_in(s))
    assert quote_ok  # R1 ruled these exact strings


@pytest.mark.parametrize("word", T.BARRED)
def test_each_barred_word_is_found_by_the_scan(word: str) -> None:
    assert T.barred_in(f"The page says {word} here.") == [word.lower()]
