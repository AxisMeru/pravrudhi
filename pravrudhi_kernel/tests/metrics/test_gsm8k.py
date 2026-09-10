import pytest

from pravrudhi_kernel.metrics.gsm8k import extract_prediction, gold_answer, score_completions, score_item


@pytest.mark.parametrize(
    ("text", "want"),
    [
        ("a\nb\n#### 18", "18"),
        ("#### 1,234", "1234"),
        ("#### $5.50", "5.5"),
        ("#### -3", "-3"),
        ("#### 7.0", "7"),
    ],
)
def test_gold_answer(text: str, want: str) -> None:
    assert gold_answer(text) == want


def test_gold_answer_requires_marker() -> None:
    with pytest.raises(ValueError):
        gold_answer("no marker")


@pytest.mark.parametrize(
    ("completion", "want"),
    [
        ("She has 3 + 4 = 7 eggs. Final answer: 7", "7"),
        ("... so 16 - 3 - 4 = 9, 9 * 2 = 18.\nFinal answer: $18", "18"),
        ("The result is \\boxed{1,200}.", "1200"),
        ("First 12 then 15 then finally 42", "42"),
        ("no digits here", None),
        ("Final answer: 3.50", "3.5"),
        ("Final Answer = -12", "-12"),
        ("some text 5 more text Final answer: 6 and afterthought 7", "6"),
    ],
)
def test_extract_prediction(completion: str, want: str | None) -> None:
    assert extract_prediction(completion) == want


def test_score_item_and_missing_completions_are_misses() -> None:
    assert score_item("Final answer: 18", "18") == 1
    assert score_item("Final answer: 18", "1,8") == 1  # gold normalises commas too
    assert score_item("Final answer: 18", "19") == 0
    assert score_item("Final answer: 18.0", "18") == 1
    scores = score_completions({"a": "Final answer: 1", "b": "Final answer: 5"}, {"a": "1", "b": "2", "c": "3"})
    assert scores == {"a": 1, "b": 0, "c": 0}
