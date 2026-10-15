"""`_fuzzy_match` is the plain `SequenceMatcher.ratio() >= _FUZZY_THRESHOLD` test with difflib's two cheap upper bounds
tried first.
The bounds can only skip a title whose ratio is below the floor, so the answer must equal the plain test on every pair; and the
expensive ratio() must run for far fewer titles than the scan visits (the scan was the 5 to 17 s of a many-alias CONFLICT key)."""

from __future__ import annotations

import random
import sqlite3
from difflib import SequenceMatcher
from pathlib import Path

import pytest

from pravrudhi.application import verify as V
from pravrudhi.application.case_index import CaseRecord, insert_case, open_index

_WORDS = [
    "aldric",
    "vennoway",
    "state",
    "of",
    "zorbland",
    "quillfeather",
    "mendelsohn",
    "aerospace",
    "marlowenton",
    "thackeray",
    "ltd",
]


def _plain(target: str, title: str) -> bool:
    return SequenceMatcher(None, target, V.normalize_party_name(title)).ratio() >= V._FUZZY_THRESHOLD


def _name(rng: random.Random) -> str:
    return " ".join(rng.choice(_WORDS) for _ in range(rng.randint(1, 5)))


def test_the_prefiltered_match_equals_the_plain_ratio_test_on_random_pairs() -> None:
    rng = random.Random(20261015)
    agree = near = 0
    for _ in range(4000):
        target = V.normalize_party_name(_name(rng))
        title = _name(rng) if rng.random() < 0.5 else target.title().replace(" ", "  ")[:-1] + rng.choice("xyz")
        want = _plain(target, title)
        assert V._fuzzy_match(target, title) is want, (target, title)
        agree += 1
        near += want
    assert agree == 4000 and near > 100  # both outcomes are exercised


@pytest.mark.parametrize(
    "title", ["Aldric Vennoway vs State Of Zorbland", "Aldric Vennoway vs State Of Zorblands", "Zorbatha vs Other"]
)
def test_a_near_spelling_still_confirms_and_a_different_title_does_not(title: str) -> None:
    target = V.normalize_party_name("Aldric Vennoway State of Zorbland")
    assert V._fuzzy_match(target, title) is _plain(target, title)


def test_the_costly_ratio_runs_for_a_small_part_of_the_titles(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    conn: sqlite3.Connection = open_index(tmp_path / "idx.sqlite3")
    rng = random.Random(7)
    for i in range(400):
        insert_case(conn, CaseRecord(f"c{i}", f"{_name(rng)} {i} vs {_name(rng)}", "Supreme Court", 2083, "sc_pdf", "", "x"))
    insert_case(conn, CaseRecord("real", "Aldric Vennoway vs State Of Zorbland", "Supreme Court", 2083, "sc_pdf", "", "x"))
    conn.commit()
    conn.row_factory = sqlite3.Row
    calls = {"ratio": 0}
    original = SequenceMatcher.ratio

    def counting(self: SequenceMatcher) -> float:  # type: ignore[type-arg]
        calls["ratio"] += 1
        return original(self)

    monkeypatch.setattr(SequenceMatcher, "ratio", counting)
    found = V._fuzzy_confirm(conn, "Aldric Vennowayy", "State of Zorbland")
    assert [r["case_id"] for r in found] == ["real"]
    assert calls["ratio"] < 40  # of 401 titles


def test_a_title_the_cheap_bounds_pass_but_whose_ratio_is_under_the_floor_is_rejected() -> None:
    target = "aldric vennoway state of zorbland quillfeather"
    title = "aldric vennoway state of quillfeather zorbland"  # same letters (bounds pass), ratio about 0.80
    matcher = SequenceMatcher(None, target, title)
    assert matcher.quick_ratio() >= V._FUZZY_THRESHOLD > matcher.ratio() >= 0.8
    assert V._fuzzy_match(target, title) is False


# Edge families for the equivalence, from Track-B's adversarial re-attack of this change (456,544 pairs, 0 violations): the bounds
# must agree with the plain test on each, and neither bound may ever be below the ratio.
_EDGE_PAIRS = [
    ("", ""),
    ("", "a"),
    ("a", ""),
    ("a", "a"),
    ("a", "b"),
    ("ab", "ba"),
    (" ", "  "),
    (".", "..."),  # empty and one-character strings
    ("abcdefghij", "abcdefghix"),  # ratio exactly 0.9: on the floor, so it confirms
    ("abcdefghij", "abcdefgxyz"),  # ratio 0.7: under it
    (
        "aldric vennoway state of zorbland",
        "state of zorbland aldric vennoway",
    ),  # the same words reordered (bounds pass, ratio low)
    ("ab" * 150, ("ab" * 150)[:-1] + "c"),  # 300 characters (difflib's autojunk applies from 200)
    ("a" * 250, "a" * 249),  # one repeated character
    ("abcde " * 40, ("abcde " * 40).replace("c", "x", 3)),
    ("अआइ ईउ", "अआइ ईऊ"),  # a non-Latin script
    ("s a kamtam", "s.a. kamtam vs other"),  # initials, punctuation and extra words
    ("dr r ramachandran", "ramachandran dr r"),
]


@pytest.mark.parametrize(("target", "title"), _EDGE_PAIRS)
def test_edge_pairs_agree_with_the_plain_test_and_the_bounds_never_undercut_the_ratio(target: str, title: str) -> None:
    normalised = V.normalize_party_name(title)
    matcher = SequenceMatcher(None, target, normalised)
    ratio = matcher.ratio()
    assert matcher.real_quick_ratio() >= ratio and matcher.quick_ratio() >= ratio
    assert V._fuzzy_match(target, title) is (ratio >= V._FUZZY_THRESHOLD) is _plain(target, title)


def test_the_edge_table_covers_both_outcomes_and_the_exact_floor() -> None:
    outcomes = {V._fuzzy_match(t, s) for t, s in _EDGE_PAIRS}
    assert outcomes == {True, False}
    assert SequenceMatcher(None, "abcdefghij", "abcdefghix").ratio() == V._FUZZY_THRESHOLD
