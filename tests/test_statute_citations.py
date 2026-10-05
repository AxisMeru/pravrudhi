"""A citation must never resolve to a different provision than the one the source names (354A is not 354)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from pravrudhi.application.statute_citations import contract_citations, parse_source


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("Indian Penal Code §354A", ("IPC", "354A")),
        ("Indian Penal Code, Section 498A", ("IPC", "498A")),
        ("indian penal code section 498a", ("IPC", "498A")),
        ("Negotiable Instruments Act, 1881 §138", ("Negotiable Instruments Act, 1881", "138")),
        ("Bharatiya Nyaya Sanhita §69", ("BNS", "69")),
        ("Constitution of India Article 21", ("COI", "21")),
    ],
)
def test_the_section_keeps_its_letter_suffix(source: str, expected: tuple[str, str]) -> None:
    assert parse_source(source) == expected


def _corpus(*ids: str) -> SimpleNamespace:
    return SimpleNamespace(by_id={i: SimpleNamespace(id=i, title=f"title of {i}") for i in ids})


def test_a_suffixed_section_resolves_to_itself_and_never_to_the_plain_number() -> None:
    out = contract_citations(["Indian Penal Code §354A", "Indian Penal Code §498A"], _corpus("IPC/Section 354", "IPC/Section 498A"))
    assert out[0] == {"act": "IPC", "section": "354A", "corpus_id": None, "in_corpus": False, "title": None}
    assert out[1] == {"act": "IPC", "section": "498A", "corpus_id": "IPC/Section 498A", "in_corpus": True,
                      "title": "title of IPC/Section 498A"}


def test_a_suffixed_section_that_is_in_the_corpus_resolves_exactly() -> None:
    out = contract_citations(["Indian Penal Code §354A"], _corpus("IPC/Section 354", "IPC/Section 354A"))
    assert out[0]["corpus_id"] == "IPC/Section 354A" and out[0]["in_corpus"] is True


def test_a_plain_section_still_resolves_and_an_unknown_act_keeps_its_name() -> None:
    out = contract_citations(["Negotiable Instruments Act, 1881 §138"], _corpus("IPC/Section 138"))
    assert out[0]["act"] == "Negotiable Instruments Act, 1881" and out[0]["section"] == "138"
    assert out[0]["in_corpus"] is False
    assert contract_citations(["Indian Penal Code §302"], _corpus("IPC/Section 302"))[0]["in_corpus"] is True
