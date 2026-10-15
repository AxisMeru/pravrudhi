"""A paragraph word made only of number-like characters (a vulgar fraction, a superscript digit, a circled digit, a Roman numeral)
is not a letter word and must never raise: the numbered-prose check ignores it. Invented text only."""

import pytest

from pravrudhi.application.a0_assist import intake

NUMBER_LIKE = ["½", "¼", "¾", "²", "³", "①", "Ⅶ", "⅕", "⑧", "Ⅼ", "٣"]


@pytest.mark.parametrize("w", NUMBER_LIKE)
def test_a_number_like_word_never_raises_in_the_numbered_prose_check(w: str) -> None:
    assert intake._numbered_prose(f"1. aa bb {w}") is True  # two lowercase words: prose, the number-like word is ignored
    assert intake._numbered_prose(f"1. {w} {w} {w}") is False  # no letter word at all
    assert intake._numbered_prose(f"1. Aa Bb {w}") is False  # capitalised words: a party line
    assert intake._numbered_prose(f"1. aa {w}") is False  # one lowercase word only


def test_ordinary_words_are_counted_as_before() -> None:
    assert intake._numbered_prose("1. aa bb 12") is True
    assert intake._numbered_prose("1. aa bb x²") is True  # a letter with a superscript digit: 'x' is the first letter
    assert intake._numbered_prose("1. AMARESH") is False
    assert intake._numbered_prose("1. Ranjeet Narayan Mishra") is False
    assert intake._numbered_prose("not numbered aa bb cc") is False
    assert intake._numbered_prose("1. अब कख") is True  # Devanagari letters count as lowercase-start words


def test_first_letter_is_a_letter_or_empty() -> None:
    assert intake._first_letter("½ab") == "a"
    assert intake._first_letter("½") == ""
    assert intake._first_letter("12") == ""
    assert intake._first_letter("Ab") == "A"


@pytest.mark.parametrize("w", NUMBER_LIKE)
def test_the_whole_intake_accepts_a_paragraph_with_a_number_like_word(w: str) -> None:
    text = f"1. The first fact is aa bb {w} and nothing else.\n\n2. The second fact follows here {w}.\n\n3. {w}\n"
    bundle = intake.build_bundle(text)
    assert bundle["facts"]


def test_the_route_answers_200_not_500(tmp_path) -> None:  # noqa: ANN001
    from tests.test_intake_routes import PLAIN, _client

    text = "1. The first fact is aa bb ½ and nothing else.\n\n2. The second fact follows here ① Ⅶ.\n"
    r = _client(tmp_path).post(PLAIN, json={"text": text})
    assert r.status_code == 200 and r.json()["bundle"]["facts"]
