"""Free-text scorer: normalised exact match over citations (ADR-0036)."""

import pytest

from pravrudhi_kernel.metrics.citation import extract_prediction, gold_answer, score_completions, score_item


@pytest.mark.parametrize(
    ("text", "want"),
    [
        ("470 F.2d 798", "470 F.2d 798"),
        ("  470 F.2d 798  ", "470 F.2d 798"),
        ("132 U.S. 161", "132 U.S. 161"),
    ],
)
def test_gold_answer_keeps_the_citation_as_written(text: str, want: str) -> None:
    assert gold_answer(text) == want


@pytest.mark.parametrize("text", ["", "   ", "\n"])
def test_gold_answer_refuses_an_empty_gold(text: str) -> None:
    # The numeric scorer raises on a missing '####' and the choice scorer on anything but a letter. An empty
    # gold here would make every completion correct-by-vacuity, which is the worst direction to fail in.
    with pytest.raises(ValueError):
        gold_answer(text)


@pytest.mark.parametrize(
    ("completion", "want"),
    [
        (" 655 F.2d 1233", "655 F.2d 1233"),
        ("470 F.3d 798", "470 F.3d 798"),
        ("The citation is 132 U.S. 161.", "132 U.S. 161"),
        ("117 F. 137", "117 F. 137"),
        ("627 F. Supp. 44 (1985)", "627 F. Supp. 44"),
        ("113 S.W.3d 101 (2004)", "113 S.W.3d 101"),
        ("148 L. Ed. 2d 1355", "148 L. Ed. 2d 1355"),
        ("39 Fla. 536", "39 Fla. 536"),
    ],
)
def test_extract_prediction(completion: str, want: str) -> None:
    assert extract_prediction(completion) == want


@pytest.mark.parametrize(
    "completion",
    [
        "",
        "The case is about a defective product. The case was decided in 2015.",
        "I do not know the citation for this case.",
        "Unable to determine the citation.",
        "decided in 2015 by 3 judges",
        "there were 12 counts and 4 defendants",
        "470 f.2d 798",
    ],
)
def test_nothing_is_invented_from_prose_or_a_refusal(completion: str) -> None:
    """The uppercase reporter requirement is a guard with a price, and both halves are deliberate.

    Digits-space-word-space-digits occurs constantly in ordinary sentences: "decided in 2015 by 3 judges"
    would otherwise parse as volume 2015, reporter "by", page 3, and a model that cites nothing would score on
    its own prose. Reporters are capitalised in every citation system, so the cost is the unusual lower-case
    rendering in the last case, which is counted as no answer rather than risked as a citation.
    """
    assert extract_prediction(completion) is None


def test_the_first_citation_is_the_answer() -> None:
    # A completion that echoes the prompt's few-shot examples ends on one of THEM, not on its own answer.
    text = " 655 F.2d 1233\nCase: United States v. One Book Called Ulysses\nAnswer: 72 F.2d 705"
    assert extract_prediction(text) == "655 F.2d 1233"


@pytest.mark.parametrize(
    "variant", ["470 F.2d 798", "470 F. 2d 798", " 470  F.2d  798. ", "The answer is 470 F.2d 798,"]
)
def test_scoring_ignores_spacing_and_punctuation(variant: str) -> None:
    assert score_item(variant, "470 F.2d 798") == 1


def test_a_different_citation_is_wrong_and_a_refusal_is_not_right() -> None:
    assert score_item("655 F.2d 1233", "470 F.2d 798") == 0
    assert score_item("I do not know.", "470 F.2d 798") == 0


def test_score_completions_treats_a_missing_completion_as_a_miss() -> None:
    scores = score_completions(
        {"a": "470 F.2d 798", "b": "1 F.2d 1"}, {"a": "470 F.2d 798", "b": "2 F.2d 2", "c": "3 F.2d 3"}
    )
    assert scores == {"a": 1, "b": 0, "c": 0}
