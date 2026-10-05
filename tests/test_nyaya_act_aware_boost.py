"""Issue #132: a query that names an Act boosts only that Act's section; a bare "section N" keeps the old behaviour."""

from __future__ import annotations

import pytest

from pravrudhi.application import nyaya

BOOST = 90.0  # the section boost is +100; BM25 alone stays far below this


def _boosted(question: str) -> set[str]:
    c = nyaya.load_corpus()
    return {d.id for d, s in c.retrieve(question, k=400) if s >= BOOST}


@pytest.mark.parametrize(
    "question,expected",
    [
        ("What is the punishment for murder under BNS section 103?", {"BNS/Section 103"}),
        ("BNS section 69", {"BNS/Section 69"}),
        ("IPC section 302", {"IPC/Section 302"}),
        ("punishment under section 302 of the IPC", {"IPC/Section 302"}),
        ("section 103 of the Bharatiya Nyaya Sanhita, 2023", {"BNS/Section 103"}),
        ("section 302 of the Indian Penal Code", {"IPC/Section 302"}),
        ("BNSS section 103", {"BNSS/Section 103"}),
        ("under BSA section 103", {"BSA/Section 103"}),
        ("bns69", {"BNS/Section 69"}),
        ("BNS 69", {"BNS/Section 69"}),
        ("BNS s.69", {"BNS/Section 69"}),
        ("BNS s69", {"BNS/Section 69"}),
        ("B.N.S. section 69", {"BNS/Section 69"}),
        ("bnss103", {"BNSS/Section 103"}),
        ("sections 103 and 101 of the BNS", {"BNS/Section 103", "BNS/Section 101"}),
        ("Section 189(2) of the BNS", {"BNS/Section 189"}),
        ("sections.270 and 283 of the Bharatiya Nyaya Sanhita", {"BNS/Section 270", "BNS/Section 283"}),
        ("U/S.483 of the BNSS", {"BNSS/Section 483"}),
        ("Article 21 of the Constitution of India", {"COI/Article 21"}),
        ("COI article 14", {"COI/Article 14"}),
    ],
)
def test_a_named_act_receives_the_boost_alone(question: str, expected: set[str]) -> None:
    assert _boosted(question) == expected


def test_an_unqualified_section_number_keeps_the_act_blind_boost() -> None:
    got = _boosted("What is the punishment under section 302?")
    assert {"IPC/Section 302", "BNS/Section 302", "BNSS/Section 302"} <= got
    assert all(i.endswith("/Section 302") for i in got)


def test_two_acts_in_one_question_each_keep_their_own_section() -> None:
    got = _boosted("compare IPC section 302 with BNS section 103")
    assert got == {"IPC/Section 302", "BNS/Section 103"}


def test_a_qualified_mention_does_not_leak_into_the_bare_rule() -> None:
    got = _boosted("BNS section 103, and separately section 302")
    assert {"BNS/Section 103", "BNS/Section 302", "IPC/Section 302", "BNSS/Section 302"} <= got
    assert "BSA/Section 103" not in got and "BNSS/Section 103" not in got and "IPC/Section 103" not in got


def test_act_words_with_no_section_number_add_no_boost() -> None:
    assert _boosted("what does the BNS say about murder") == set()
    assert _boosted("BNS 2023 introduced changes") == set()


def test_the_named_section_ranks_first_and_above_the_relevance_floor() -> None:
    c = nyaya.load_corpus()
    for q, first in [
        ("bns69", "BNS/Section 69"),
        ("What is the punishment for murder under BNS section 103?", "BNS/Section 103"),
        ("IPC section 302", "IPC/Section 302"),
    ]:
        hits = c.retrieve(q, k=3)
        assert hits[0][0].id == first and hits[0][1] >= c.min_relevance_score


@pytest.mark.parametrize(
    "question,expected",
    [
        ("Bharatiya Nagarik Suraksha Sanhita, 2023 section 528", {("BNSS", "528")}),
        ("Bharatiya Nyaya Sanhita 2023, section 103", {("BNS", "103")}),
        ("BNSS 2023 s.187", {("BNSS", "187")}),
        ("BNS, 2023 sections 103", {("BNS", "103")}),
        ("IPC 1860 section 302", {("IPC", "302")}),
    ],
)
def test_an_acts_year_is_not_read_as_the_section_number(question: str, expected: set[tuple[str, str]]) -> None:
    """#169: "<Act>, 2023 section 528" named section 2023 of the Act and left 528 Act-blind."""
    pairs, bare = nyaya.named_sections(question)
    assert pairs == expected and bare == set()


def test_a_bare_act_and_four_digit_number_is_unchanged() -> None:
    assert nyaya.named_sections("BNS 2023 introduced changes") == ({("BNS", "2023")}, set())
