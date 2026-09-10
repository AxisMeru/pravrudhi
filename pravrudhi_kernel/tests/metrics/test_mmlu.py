import pytest

from pravrudhi_kernel.metrics.mmlu import extract_prediction, gold_answer, score_completions, score_item


@pytest.mark.parametrize(
    ("text", "want"),
    [("A", "A"), ("d", "D"), ("(C)", "C"), (" B ", "B"), ("J", "J")],
)
def test_gold_answer(text: str, want: str) -> None:
    assert gold_answer(text) == want


@pytest.mark.parametrize("text", ["18", "", "AB", "K", "#### 18", "Answer: A", "1"])
def test_gold_answer_refuses_anything_that_is_not_one_option_letter(text: str) -> None:
    # The numeric scorer raises when the gold lacks '####'. This one must be equally loud: a numeric gold
    # reaching the choice scorer is a mis-sealed pool, and silently scoring it 0 for every item would look
    # exactly like a model that knows nothing.
    with pytest.raises(ValueError):
        gold_answer(text)


@pytest.mark.parametrize(
    ("completion", "want"),
    [
        ("Answer: C", "C"),
        ("**Answer:** B", "B"),
        ("The answer is (D).", "D"),
        ("Final answer: A", "A"),
        ("I think B, but on reflection the final answer is C.", "C"),
        ("The result is \\boxed{D}.", "D"),
        ("Therefore the correct option is D.", "D"),
        ("Choice A is the only one consistent with the statute.", "A"),
        ("...so both are barred.\n\nC", "C"),
        ("...so both are barred.\n\nC.", "C"),
        ("Answer: J", "J"),
        ("answer: c", "C"),
    ],
)
def test_extract_prediction(completion: str, want: str) -> None:
    assert extract_prediction(completion) == want


@pytest.mark.parametrize(
    "completion",
    [
        "",
        "I cannot determine this from the facts given.",
        "The answer is a matter of settled precedent.",
        "42",
        "The answer is Z",
    ],
)
def test_extract_prediction_returns_none_rather_than_guessing(completion: str) -> None:
    # "The answer is a matter of..." is the trap: a case-insensitive letter class turns the article "a" into a
    # confident vote for option A. An unparseable completion must stay unparseable.
    assert extract_prediction(completion) is None


def test_a_later_explicit_answer_beats_an_earlier_aside() -> None:
    assert extract_prediction("Option A looks plausible.\nAnswer: B") == "B"


def test_score_item_and_missing_completions_are_misses() -> None:
    assert score_item("Answer: C", "C") == 1
    assert score_item("Answer: c", "C") == 1
    assert score_item("Answer: C", "(c)") == 1  # gold normalises the same way
    assert score_item("Answer: D", "C") == 0
    assert score_item("no letter anywhere", "C") == 0
    scores = score_completions({"a": "Answer: A", "b": "Answer: B"}, {"a": "A", "b": "C", "c": "D"})
    assert scores == {"a": 1, "b": 0, "c": 0}
