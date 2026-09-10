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
