"""The `set` answer kind: per-item Jaccard over statute labels.

ADR-REF: ADR-0038. The distinctions tested here are the measurement, not the implementation.
"""

from __future__ import annotations

import pytest

from pravrudhi_kernel.metrics import ANSWER_KINDS, SCORERS, is_binary, scorer_for
from pravrudhi_kernel.metrics import labels as L


def test_the_kind_is_registered_and_declared_non_binary() -> None:
    assert "set" in ANSWER_KINDS
    assert scorer_for("set") is L
    assert tuple(sorted(SCORERS)) == ANSWER_KINDS
    # The reason `is_binary` exists: a Wilson interval over a mean of Jaccard values is not a confidence
    # interval for anything, and `int(sum(scores))` type-checks its way past that.
    assert not is_binary("set")
    assert all(is_binary(k) for k in ANSWER_KINDS if k != "set")


def test_canonical_form_orders_numerically_not_lexicographically() -> None:
    """Lexicographic order puts `Section 107` before `Section 34`, which would make the canonical form depend
    on how a number happens to be spelled."""
    assert L.canonical({"Section 302", "Section 34", "Section 120B"}) == "Section 34|Section 120B|Section 302"
    assert L.canonical({"Section 498A", "Section 498"}) == "Section 498|Section 498A"
    assert L.canonical(set()) == ""


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Sections 302 and 34 apply.", {"Section 302", "Section 34"}),
        ("Section 120B", {"Section 120B"}),
        ("Sec. 498A read with s 34", {"Section 498A", "Section 34"}),
        ("section 511", {"Section 511"}),
    ],
)
def test_labels_are_parsed_from_the_forms_a_model_actually_writes(text: str, expected: set[str]) -> None:
    assert L.parse_labels(text) == expected


def test_a_bare_number_is_not_a_label() -> None:
    """A `302` in prose is as likely to be a page, a year or a paragraph number. Admitting those would credit
    a wandering completion with a label it never claimed."""
    assert L.parse_labels("The case ran 302 days across 34 hearings in 2019.") == set()
    assert L.extract_prediction("Decided in 2019 after 34 hearings.") is None


def test_gold_refuses_an_answer_naming_no_section() -> None:
    """A numeric or free-text gold arriving here means a mis-sealed pool. Scoring every item 0 would be
    indistinguishable from a model that knows no law -- the confound ADR-0035 was written about."""
    assert L.gold_answer("Sections 34 and 302") == "Section 34|Section 302"
    for bad in ("#### 18", "B", "", "no sections at all"):
        with pytest.raises(ValueError, match="names no statute section"):
            L.gold_answer(bad)


def test_jaccard_is_the_score_and_partial_credit_exists() -> None:
    """Exact-set match would keep every score binary and make the whole pool sit at zero, where every
    candidate ties with the incumbent -- which is how a bench looks when it is broken rather than hard."""
    gold = "Section 34, Section 302, Section 120B"
    assert L.score_item("Sections 34, 302 and 120B apply.", gold) == 1.0
    assert L.score_item("Sections 34 and 302 apply.", gold) == pytest.approx(2 / 3)
    assert L.score_item("Section 34 applies.", gold) == pytest.approx(1 / 3)
    assert L.score_item("Section 499 applies.", gold) == 0.0
    # A superset is penalised, or naming all 100 sections would score 1.
    assert L.score_item("Sections 34, 302, 120B and 499 apply.", gold) == pytest.approx(3 / 4)


def test_silence_and_an_explicit_empty_answer_are_different_answers() -> None:
    """"No section applies" is a claim that can be right or wrong; a completion that trailed off having named
    nothing is not an answer. Telling those apart is the whole of the abstention measurement this project's
    citation work exists to move, and collapsing them is what made a 512-token truncation look like a model
    that knew no law."""
    assert L.extract_prediction("No section of the IPC applies on these facts.") == L.EMPTY
    assert L.extract_prediction("Turning to the question of whether") is None
    # And a denial that carries an exception is not a denial.
    assert L.extract_prediction("No section other than Section 302 applies.") == "Section 302"


def test_a_non_answer_scores_zero_without_being_called_correct() -> None:
    assert L.score_item("I cannot tell from these facts.", "Section 302") == 0.0
    assert L.score_item("No section applies.", "Section 302") == 0.0


def test_score_completions_keys_on_the_gold_and_a_missing_completion_scores_zero() -> None:
    """A crash is a miss, not an exclusion: dropping the item would raise the pass rate by removing the
    evidence of the crash."""
    golds = {"a": "Section 34", "b": "Section 302"}
    got = L.score_completions({"a": "Section 34 applies."}, golds)
    assert got == {"a": 1.0, "b": 0.0}
    assert set(got) == set(golds)


def test_a_parenthesised_sub_clause_is_part_of_the_label() -> None:
    """Two of IL-TUR's hundred labels carry one -- `Section 294(b)` and `Section 376(2)` -- and without it
    both normalised to the bare section. A prediction of `Section 302` would then have been scored CORRECT
    against a gold of `Section 302(2)`: false credit, not a rounding error. It touched 22 of 400 dev rows,
    and only checking the real corpus against the published label list could have found it."""
    assert L.parse_labels("Section 294(b)") == {"Section 294(b)"}
    assert L.parse_labels("Section 376(2)") == {"Section 376(2)"}
    # The bare section and the sub-clause are DIFFERENT labels, so they must not score as each other.
    assert L.score_item("Section 376 applies.", "Section 376(2)") == 0.0
    assert L.score_item("Section 376(2) applies.", "Section 376(2)") == 1.0
    # And they order deterministically, or the canonical form is not canonical.
    assert L.canonical({"Section 376(2)", "Section 376"}) == "Section 376|Section 376(2)"
    # A sub-clause inside a list under one marker still parses.
    assert L.parse_labels("Sections 294(b) and 376(2)") == {"Section 294(b)", "Section 376(2)"}


def test_normalisation_honours_both_of_the_corpus_conventions() -> None:
    """IL-TUR writes the letter suffix UPPER (`498A`) and the sub-clause LOWER (`294(b)`). A blanket
    `.upper()` produced `Section 294(B)` and failed to match its own gold."""
    assert L.normalise_number("498a") == "498A"
    assert L.normalise_number("294(B)") == "294(b)"
    assert L.normalise_number("376(2)") == "376(2)"
    assert L.normalise_number("302") == "302"
    assert L.parse_labels("section 498a") == {"Section 498A"}
    assert L.parse_labels("Section 294(B)") == {"Section 294(b)"}


def test_every_published_il_tur_label_round_trips() -> None:
    """The check that found the sub-clause defect, kept as a test against the real corpus. A scorer built from
    a published spec and never run against the data is a scorer with unknown defects."""
    import pathlib

    src = pathlib.Path(".pravrudhi/corpus/iltur/statutes-00000-of-00001.parquet")
    if not src.exists():
        pytest.skip("IL-TUR statutes not fetched in this workspace")
    import pyarrow.parquet as pq

    names = [r["id"] for r in pq.read_table(src).to_pylist()]
    assert len(names) == 100, "IL-TUR's LSI label space is 100 IPC sections"
    for n in names:
        assert L.parse_labels(n) == {n}, f"{n!r} does not survive its own canonical form"
        assert L.gold_answer(n) == n
