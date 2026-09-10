"""Extraction and scoring for the legal-hallucination tasks.

These functions run inside the external scorer image, where a mistake is invisible: a regex that fails to
recognise a citation looks exactly like a model that did not produce one. The first version of the reporter
pattern was `[A-Z][A-Za-z.]*`, which cannot match `F.2d` because of the digit -- so every circuit citation,
the ones this task is entirely about, was scored as no answer at all. It was found by reading logged model
outputs, not by any test, which is why these exist.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "ext_tasks"))

from nyaya_utils import (  # noqa: E402
    extract_affirm_reverse,
    extract_citation,
    extract_year,
    precision,
    process_citation,
)


@pytest.mark.parametrize(
    ("completion", "want"),
    [
        (" 655 F.2d 1233", "655 F.2d 1233"),          # the case the first pattern could not match
        ("470 F.3d 798", "470 F.3d 798"),
        ("The citation is 132 U.S. 161.", "132 U.S. 161"),
        ("117 F. 137", "117 F. 137"),
        ("627 F. Supp. 44 (1985)", "627 F. Supp. 44"),  # two-token reporter
        ("113 S.W.3d 101 (2004)", "113 S.W.3d 101"),
        ("148 L. Ed. 2d 1355", "148 L. Ed. 2d 1355"),   # series designator as its own token
        ("39 Fla. 536", "39 Fla. 536"),
    ],
)
def test_extract_citation(completion: str, want: str) -> None:
    assert extract_citation(completion) == want


@pytest.mark.parametrize(
    "completion",
    [
        "",
        "The case is about a defective product. The case was decided in 2015.",
        "I do not know the citation for this case.",
        "Unable to determine the citation.",
    ],
)
def test_no_citation_is_not_invented(completion: str) -> None:
    assert extract_citation(completion) is None


def test_the_first_citation_is_the_answer_not_the_last() -> None:
    # The model echoes the prompt's few-shot examples after answering, so the last citation in the completion
    # is usually one of the examples rather than its answer.
    text = " 655 F.2d 1233\nCase: United States v. One Book Called Ulysses\nAnswer: 72 F.2d 705"
    assert extract_citation(text) == "655 F.2d 1233"


def test_normalisation_makes_spacing_and_punctuation_irrelevant() -> None:
    gold = "470 F.2d 798"
    for variant in ("470 F.2d 798", "470 F. 2d 798", " 470  F.2d  798. ", "470 F.2d 798,"):
        assert process_citation({"example_correct_answer": gold}, [variant])["exact_match"] == 1.0


@pytest.mark.parametrize(
    "prose",
    [
        "decided in 2015 by 3 judges",
        "there were 12 counts and 4 defendants",
        "the 1990 act at 5 sections",
        "470 f.2d 798",
    ],
)
def test_the_reporter_must_be_capitalised_which_is_what_keeps_prose_out(prose: str) -> None:
    """The uppercase requirement is a guard, not an oversight, and it has a price.

    Digits-space-word-space-digits occurs constantly in ordinary sentences: "decided in 2015 by 3 judges"
    would parse as volume 2015, reporter "by", page 3, and a model that never cites anything would score a
    respectable precision on its own prose. Reporters are capitalised in every citation system, so requiring
    it costs only the unusual lower-case rendering in the last case here -- which is counted as no answer
    rather than risk crediting a sentence as a citation.
    """
    assert extract_citation(prose) is None


def test_a_wrong_citation_is_answered_and_wrong_not_abstained() -> None:
    # The distinction the whole metric exists for: inventing a citation is the failure, declining is not.
    out = process_citation({"example_correct_answer": "470 F.2d 798"}, ["655 F.2d 1233"])
    assert out["exact_match"] == 0.0
    assert out["citation_precision"] == (0.0, 1.0)     # answered, and wrong
    assert out["citation_abstention"] == 0.0
    assert out["citation_unparsed"] == 0.0


def test_an_explicit_refusal_is_abstention_and_not_a_failure_to_parse() -> None:
    out = process_citation({"example_correct_answer": "470 F.2d 798"}, ["I do not know."])
    assert out["citation_abstention"] == 1.0
    assert out["citation_unparsed"] == 0.0
    assert out["citation_precision"] == (0.0, 0.0)     # contributes to neither side of the ratio


def test_rambling_is_unparsed_and_never_counted_as_honesty() -> None:
    # Measured on 8 items at a 16-token budget, abstention read 0.5 while the logs showed no refusal at all.
    out = process_citation({"example_correct_answer": "470 F.2d 798"}, ["The answer must be in a single wo"])
    assert out["citation_unparsed"] == 1.0
    assert out["citation_abstention"] == 0.0


def test_precision_is_a_ratio_of_sums_so_declined_items_do_not_dilute_it() -> None:
    # Two answered, one right; three declined. Precision is 0.5, not 0.2.
    items = [(1.0, 1.0), (0.0, 1.0), (0.0, 0.0), (0.0, 0.0), (0.0, 0.0)]
    assert precision(items) == 0.5
    assert precision([(0.0, 0.0)]) == 0.0


@pytest.mark.parametrize(
    ("completion", "want"),
    [("1967", "1967"), ("The year was 1976.", "1976"), ("It was overruled in 2004", "2004")],
)
def test_extract_year(completion: str, want: str) -> None:
    assert extract_year(completion) == want


@pytest.mark.parametrize("completion", ["", "no year here", "in the 90s"])
def test_no_year_is_not_invented(completion: str) -> None:
    assert extract_year(completion) is None


@pytest.mark.parametrize(
    ("completion", "want"),
    [(" Reverse", "reverse"), ("affirm", "affirm"), ("The court AFFIRMED", "affirm")],
)
def test_extract_affirm_reverse(completion: str, want: str) -> None:
    assert extract_affirm_reverse(completion) == want


def test_affirm_reverse_reads_the_first_line_so_a_later_ramble_cannot_flip_it() -> None:
    assert extract_affirm_reverse(" Reverse\n``` The answer is: affirm\n") == "reverse"
