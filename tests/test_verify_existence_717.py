"""#717 (#908): the existence-only answer (`exists_in_index`, a request with no quote) is stricter than `verify`'s candidate test.
Without a quote nothing else stands between a different case and IN_INDEX, so: exactly ONE distinct case may survive the year and
party-title tests, and its title may carry at most EXISTENCE_MAX_EXTRA_TITLE_TOKENS distinctive words the cited
parties do not have;
anything else answers NOT_IN_INDEX. Invented parties only. Both arms of each rule."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from pravrudhi.application import verify as V
from pravrudhi.application.case_index import CaseRecord, insert_case, open_index
from pravrudhi.application.verify import VerifyResult, exists_in_index, verify

N = V.EXISTENCE_MAX_EXTRA_TITLE_TOKENS
KEY = "(2083) 4 SCC 512"
CITER = "This Court in Aldric Vennoway v. B.R. Telmark and Anr. (2083) 4 SCC 512 clarified the position."
PASSAGE = "a wholly different holding about arbitration clauses and their severability"


def _db(tmp_path: Path, name: str, cases: list[tuple[str, str, int]], citer: str = CITER) -> sqlite3.Connection:
    conn = open_index(tmp_path / f"{name}.sqlite3")
    insert_case(conn, CaseRecord("citer", "Later vs Other", "Supreme Court", 2005, "sc_pdf", "", citer))
    for cid, title, year in cases:
        insert_case(conn, CaseRecord(cid, title, "Supreme Court", year, "sc_pdf", "", PASSAGE))
    conn.commit()
    return conn


def _filler(n: int) -> str:
    return " ".join(f"Wxyzword{chr(97 + i) * 3}" for i in range(n))


def test_the_exact_party_match_is_found(tmp_path: Path) -> None:
    conn = _db(tmp_path, "exact", [("real", "Aldric Vennoway vs B R Telmark", 2083)])
    assert exists_in_index(conn, KEY) == VerifyResult.IN_INDEX


def test_a_title_that_adds_up_to_the_limit_is_still_found(tmp_path: Path) -> None:
    conn = _db(tmp_path, "adds_n", [("real", f"Aldric Vennoway vs B R Telmark {_filler(N)}".strip(), 2083)])
    assert exists_in_index(conn, KEY) == VerifyResult.IN_INDEX


def test_a_title_that_adds_one_word_more_than_the_limit_is_not_found(tmp_path: Path) -> None:
    conn = _db(tmp_path, "adds_n1", [("wrong", f"Aldric Vennoway vs B R Telmark {_filler(N + 1)}", 2083)])
    assert exists_in_index(conn, KEY) == VerifyResult.NOT_IN_INDEX


def test_the_documented_telmark_shape_is_still_caught_by_the_verbatim_check_with_a_quote(tmp_path: Path) -> None:
    """The old limit (`test_verify.py`): with a QUOTE the case holding it can still verify (the verbatim check
    stands); this test pins that
    `verify` is unchanged, so only the existence-only path got stricter."""
    conn = _db(tmp_path, "telmark", [("wrong", "Aldric Vennoway vs B R Telmark Industries Ltd", 2083)])
    assert verify(conn, KEY, PASSAGE) == VerifyResult.VERIFIED


def test_two_distinct_surviving_cases_cannot_be_told_apart_and_are_not_found(tmp_path: Path) -> None:
    conn = _db(tmp_path, "two", [("a", "Aldric Vennoway vs B R Telmark", 2083), ("b", "Aldric Vennoway vs B R Telmark", 2082)])
    assert exists_in_index(conn, KEY) == VerifyResult.NOT_IN_INDEX


def test_a_second_case_outside_the_year_window_does_not_count(tmp_path: Path) -> None:
    conn = _db(
        tmp_path, "yearwin", [("a", "Aldric Vennoway vs B R Telmark", 2083), ("b", "Aldric Vennoway vs B R Telmark", 2090)]
    )
    assert exists_in_index(conn, KEY) == VerifyResult.IN_INDEX


def test_a_case_indexed_twice_under_one_id_counts_once(tmp_path: Path) -> None:
    """1,307 case ids have two rows in the 7 Oct 2026 index: one case, not two."""
    conn = _db(tmp_path, "dup", [("a", "Aldric Vennoway vs B R Telmark", 2083)])
    insert_case(conn, CaseRecord("a", "Aldric Vennoway vs B R Telmark", "Supreme Court", None, "sc_pdf", "", PASSAGE))
    conn.commit()
    assert exists_in_index(conn, KEY) == VerifyResult.IN_INDEX


def test_a_party_sharing_case_of_unrelated_subject_does_not_resolve_as_the_cited_case(tmp_path: Path) -> None:
    """The Q155-c1 shape: four cases whose names share the cited parties' words, none the cited case (all add words):
    never IN_INDEX."""
    conn = _db(
        tmp_path,
        "q155",
        [(f"c{i}", f"Aldric Vennoway vs B R Telmark {_filler(N + 1)}x{i}", 2083) for i in range(4)],
    )
    assert exists_in_index(conn, KEY) == VerifyResult.NOT_IN_INDEX


def test_a_spelling_variant_of_one_party_is_found_when_the_extra_is_within_the_limit(tmp_path: Path) -> None:
    conn = _db(
        tmp_path,
        "spell",
        [("real", "Hollis Quenell vs Pardoe", 2083)],
        citer="In Hollis Quennell v. Pardoe (2083) 5 SCC 100 the Court held",
    )
    assert exists_in_index(conn, "(2083) 5 SCC 100") == (VerifyResult.IN_INDEX if N >= 1 else VerifyResult.NOT_IN_INDEX)


def test_the_extra_token_count_is_zero_without_an_alias_group_and_uses_the_best_fitting_group(tmp_path: Path) -> None:
    conn = _db(tmp_path, "tok", [("real", "Aldric Vennoway vs B R Telmark", 2083)])
    assert V._extra_title_tokens(conn, "(2099) 1 SCC 1", "Whatever Title") == 0
    assert V._extra_title_tokens(conn, KEY, "Aldric Vennoway vs B R Telmark") == 0
    assert V._extra_title_tokens(conn, KEY, "Aldric Vennoway vs B R Telmark Industries") == 1
    # two alias groups on one key: the group that fits the title best decides
    conn.execute(
        "INSERT INTO citation_aliases (party_1, party_2, citation, case_id) "
        "VALUES ('Zorbatha Quillfeather', 'Mendelsohn Aerospace', ?, 'x')",
        (KEY,),
    )
    assert V._extra_title_tokens(conn, KEY, "Aldric Vennoway vs B R Telmark") == 0


def test_the_resolution_statuses_are_unchanged(tmp_path: Path) -> None:
    conn = _db(tmp_path, "unchanged", [("real", "Aldric Vennoway vs B R Telmark", 2083)])
    assert exists_in_index(conn, "(2099) 9 SCC 999") == VerifyResult.NOT_IN_INDEX  # no alias
    assert exists_in_index(conn, "not a citation") == VerifyResult.MALFORMED
    conn.execute(
        "INSERT INTO citation_aliases (party_1, party_2, citation, case_id) VALUES ('Zorbatha', 'Quillfeather', ?, 'x')", (KEY,)
    )
    conn.commit()
    assert exists_in_index(conn, KEY) == VerifyResult.CONFLICT  # two alias groups on one key stay a CONFLICT


@pytest.mark.parametrize("limit", [0, 1, 2])
def test_the_limit_is_the_module_constant(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, limit: int) -> None:
    monkeypatch.setattr(V, "EXISTENCE_MAX_EXTRA_TITLE_TOKENS", limit)
    conn = _db(tmp_path, f"lim{limit}", [("real", f"Aldric Vennoway vs B R Telmark {_filler(1)}", 2083)])
    assert exists_in_index(conn, KEY) == (VerifyResult.IN_INDEX if limit >= 1 else VerifyResult.NOT_IN_INDEX)


# ---- the non-distinctive token list (config): listed words do not count as extra title words --------------------------------
def test_a_state_name_spelled_out_in_the_title_is_not_an_extra_word(tmp_path: Path) -> None:
    conn = _db(
        tmp_path,
        "mp",
        [("real", "Marlow Tenwick Ors vs State Of Madhya Pradesh", 2091)],
        citer="In Marlow Tenwick & Ors. v. State of M.P. (2091) 6 SCC 100 the Court held",
    )
    assert exists_in_index(conn, "(2091) 6 SCC 100") == VerifyResult.IN_INDEX


def test_party_status_and_suffix_words_are_not_extra_words(tmp_path: Path) -> None:
    conn = _db(
        tmp_path,
        "suffix",
        [("real", "Orlan Pettibone Anr vs Wren Haddock Dead By Lrs Ors", 2090)],
        citer="In Orlan Pettibone v. Wren Haddock (2090) 4 SCC 462 the Court held",
    )
    assert exists_in_index(conn, "(2090) 4 SCC 462") == VerifyResult.IN_INDEX


def test_the_telmark_shape_still_answers_not_in_index_with_the_list(tmp_path: Path) -> None:
    """An entity word ('industries') is NOT on the list: the different case is still refused."""
    conn = _db(tmp_path, "telmark2", [("wrong", "Aldric Vennoway vs B R Telmark Industries Ltd", 2083)])
    assert "industries" not in V._nondistinctive_tokens()
    assert exists_in_index(conn, KEY) == VerifyResult.NOT_IN_INDEX


def test_a_listed_word_plus_an_unlisted_extra_word_is_still_refused(tmp_path: Path) -> None:
    conn = _db(
        tmp_path,
        "mixed",
        [("wrong", "Marlow Tenwick Ors vs State Of Madhya Pradesh Enterprises", 2091)],
        citer="In Marlow Tenwick & Ors. v. State of M.P. (2091) 6 SCC 100 the Court held",
    )
    assert exists_in_index(conn, "(2091) 6 SCC 100") == VerifyResult.NOT_IN_INDEX


def test_the_list_is_config_a_missing_unreadable_or_malformed_file_is_the_strict_rule(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for content in (None, "not json", '{"tokens": "madhya"}', '{"tokens": [1, 2]}', "{}"):
        bad = tmp_path / "bad.json"
        if content is None:
            bad = tmp_path / "absent.json"
        else:
            bad.write_text(content)
        monkeypatch.setattr(V, "_NONDISTINCTIVE_FILE", bad)
        V._nondistinctive_tokens.cache_clear()
        assert V._nondistinctive_tokens() == frozenset()
    V._nondistinctive_tokens.cache_clear()
    monkeypatch.undo()
    V._nondistinctive_tokens.cache_clear()
    assert {"madhya", "pradesh", "dead", "lrs", "private"} <= V._nondistinctive_tokens()


def test_the_shipped_list_holds_no_entity_word_that_could_tell_two_cases_apart() -> None:
    free = V._nondistinctive_tokens()
    for word in (
        "industries",
        "enterprises",
        "traders",
        "bank",
        "mills",
        "works",
        "association",
        "committee",
        "society",
        "trust",
        "board",
        "university",
    ):
        assert word not in free


def test_the_list_is_in_the_wheel_assets_tree() -> None:
    assert V._NONDISTINCTIVE_FILE.parts[-4:] == (
        "assets",
        "citation",
        "nondistinctive_tokens.json",
    ) or V._NONDISTINCTIVE_FILE.parts[-3:] == ("assets", "citation", "nondistinctive_tokens.json")
    assert V._NONDISTINCTIVE_FILE.is_file()
