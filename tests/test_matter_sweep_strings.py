"""S1: every fixed string R1 ruled is pinned by its sha256[:16] (R1 reads the PR head against these hashes).

The engine's own labels feed the summary clauses."""

from __future__ import annotations

import hashlib

import pytest

from pravrudhi.application import matter_sweep as S

H = {
    "DATA_STATEMENT": "4dc56a664cdf8906",
    "SNAPSHOT_LINE": "4600cbd0738b2a1e",
    "DUPLICATES_LINE": "9b9596d4b10cf8d0",
    "NOT_READY_ONE": "17221898fcb6e2e6",
    "NOT_READY_MANY": "7a8f6710a6da742f",
    "NOT_SEARCHED_LINE": "4c4c52e34243bae4",
    "BUSY": "4981b757184e31e1",
    "MISMATCH_CLAUSE": "d9dbebd3c6c42c0e",
    "CONFLICT_CLAUSE": "c4390486c3d3e700",
}


def _h(t: str) -> str:
    return hashlib.sha256(t.encode()).hexdigest()[:16]


@pytest.mark.parametrize(("name", "expected"), sorted(H.items()))
def test_each_ruled_string_has_the_ruled_hash(name: str, expected: str) -> None:
    text = getattr(S, name)
    if name == "MISMATCH_CLAUSE":  # R1's template names the count {M} and quotes the engine's label for NAME_MISMATCH
        text = '{M} marked "Case name did not match".'
    if name == "CONFLICT_CLAUSE":  # {C} and the engine's CONFLICT label
        text = '{C} marked "{conflict_label}".'
    assert _h(text) == expected


def test_the_labels_in_the_clauses_are_read_from_the_engine_not_typed() -> None:
    assert S._engine_label("NAME_MISMATCH") == "Case name did not match"
    assert S._engine_label("CONFLICT").startswith("conflict:")


def test_the_not_ready_line_is_singular_for_one() -> None:
    assert (
        S.not_ready_line(1) == "1 paper was not ready and is not included."
        and S.not_ready_line(3) == "3 papers were not ready and are not included."
    )
