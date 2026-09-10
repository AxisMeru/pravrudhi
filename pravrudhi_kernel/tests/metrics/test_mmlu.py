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


def test_the_pronoun_i_is_not_option_i() -> None:
    """ADR-0042. `Answer: I do not know` parsed as a confident vote for option I, because `_EXPLICIT` takes
    the letter after `answer:` and the I is followed by a space. Found on casehold-val, where a candidate
    prompted to abstain returned letters {A:21, B:11, C:8, D:17, E:13, I:5} on a FIVE-option pool.

    It corrupts no score -- an out-of-range letter is wrong exactly as an unparsed answer is -- but it counts
    refusals as wrong answers, and refusal-versus-wrong is the distinction `citation_abstention` exists to
    measure. A scorer that reads "I do not know" as a letter cannot measure abstention at all.

    This is the same failure the module docstring already warns about for the article "a"; the article was
    guarded and the pronoun was not."""
    for refusal in (
        "Answer: I do not know",
        "Answer: I don't know",
        "Answer: I cannot determine which holding applies",
        "ANSWER: I am not able to say",
    ):
        assert extract_prediction(refusal) is None, refusal


def test_option_i_survives_where_it_really_is_option_i() -> None:
    """Deliberately narrow: on a ten-option pool (MMLU-Pro) `Answer: I` is exactly a vote for option I, and
    guessing otherwise would need the scorer to know the option count, which the protocol does not carry."""
    assert extract_prediction("Answer: I") == "I"
    assert extract_prediction("ANSWER: I.") == "I"
    assert extract_prediction("The answer is I") == "I"
    assert extract_prediction("answer = I") == "I"


def test_a_refusal_followed_by_a_real_answer_yields_the_answer() -> None:
    """The guard must not lose a closing commitment. `findall` became `finditer` so the guard can see what
    follows a match, and the last surviving match still wins."""
    assert extract_prediction("Answer: I do not know, but the answer is C") == "C"
    assert extract_prediction("I do not know. Answer: B") == "B"


def test_no_other_letter_is_affected_by_the_pronoun_guard() -> None:
    """Only I is a pronoun. A following lowercase word must not disqualify any other letter."""
    for letter in "ABCDEFGHJ":
        assert extract_prediction(f"Answer: {letter} because it follows from the statute") == letter
