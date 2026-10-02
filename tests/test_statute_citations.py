"""#142: deterministic statute citations for a contract, resolved against the shipped corpus. Constructed
corpus and source strings only; no judge output is an input anywhere."""

from __future__ import annotations

from pravrudhi.application import nyaya
from pravrudhi.application.nyaya import Corpus, Document
from pravrudhi.application.statute_citations import contract_citations, parse_source


def _corpus() -> Corpus:
    return Corpus(
        [
            Document("BNS/Section 69", "Bharatiya Nyaya Sanhita, 2023", "Section 69", "Deceitful sexual intercourse", "t"),
            Document("IPC/Section 405", "Indian Penal Code, 1860", "Section 405", "Criminal breach of trust", "t"),
            Document("COI/Article 21", "Constitution of India", "Article 21", "Protection of life", "t"),
        ],
        [],
    )


def test_parse_source_reads_act_and_section() -> None:
    assert parse_source("Bharatiya Nyaya Sanhita §69") == ("BNS", "69")
    assert parse_source("Indian Penal Code §405") == ("IPC", "405")
    assert parse_source("Bharatiya Nagarik Suraksha Sanhita §187") == ("BNSS", "187")
    assert parse_source("Bharatiya Sakshya Adhiniyam § 63(2)") == ("BSA", "63")
    assert parse_source("Constitution of India Article 21") == ("COI", "21")


def test_resolved_citation_names_the_corpus_document() -> None:
    out = contract_citations(["Bharatiya Nyaya Sanhita §69"], _corpus())
    assert out == [{"act": "BNS", "section": "69", "corpus_id": "BNS/Section 69", "in_corpus": True,
                    "title": "Deceitful sexual intercourse"}]


def test_a_reference_absent_from_the_corpus_is_kept_not_dropped() -> None:
    out = contract_citations(["Bharatiya Nyaya Sanhita §999"], _corpus())
    assert out == [{"act": "BNS", "section": "999", "corpus_id": None, "in_corpus": False, "title": None}]


def test_an_unparseable_reference_is_kept_unresolved() -> None:
    out = contract_citations(["Some Unknown Act §1"], _corpus())
    assert out == [{"act": "Some Unknown Act", "section": "1", "corpus_id": None, "in_corpus": False, "title": None}]
    out = contract_citations(["no section here"], _corpus())
    assert out[0]["in_corpus"] is False and out[0]["corpus_id"] is None


def test_order_and_multiple_sources_are_preserved() -> None:
    out = contract_citations(["Indian Penal Code §405", "Bharatiya Nyaya Sanhita §69"], _corpus())
    assert [c["corpus_id"] for c in out] == ["IPC/Section 405", "BNS/Section 69"]


def test_every_in_corpus_citation_resolves_for_every_shipped_source_form() -> None:
    corpus = nyaya.load_corpus()
    ids = corpus.by_id
    for src in ["Bharatiya Nyaya Sanhita §85", "Bharatiya Nyaya Sanhita §86", "Indian Penal Code §405",
                "Bharatiya Nyaya Sanhita §316"]:
        (c,) = contract_citations([src], corpus)
        assert c["in_corpus"] == (c["corpus_id"] in ids)
        if c["in_corpus"]:
            assert ids[c["corpus_id"]].title == c["title"]
