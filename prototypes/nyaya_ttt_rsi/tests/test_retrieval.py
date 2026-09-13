import json

import pytest

from prototypes.nyaya_ttt_rsi.retrieval import (
    ABSTAIN_PHRASE, DEFAULT_HELDOUT, DEFAULT_TRAIN, Answer, Passage,
    PassageStore, build_grounded_prompt, grounded, parse_answer, retrieval_recall,
)


@pytest.fixture(scope="module")
def store():
    return PassageStore.from_law_files(DEFAULT_TRAIN, DEFAULT_HELDOUT)


def test_passage_count_and_text(store):
    keys = set()
    for path in (DEFAULT_TRAIN, DEFAULT_HELDOUT):
        with path.open() as stream:
            for line in stream:
                row = json.loads(line)
                # Abstention-only keys explicitly have no corpus provision.
                if row["kind"] == "law_lookup":
                    keys.add((row["act"], row["section"]))
    assert len(store) == len(keys) == 2269
    assert {(p.act, p.section) for p in store.passages} == keys
    assert all("\nCitation:" not in p.text for p in store.passages)


def test_search_and_lookup(store):
    passage = store.search("untouchability", 1)[0]
    assert (passage.act, passage.section) == ("Constitution of India", "Article 17")
    assert store.lookup("constitution OF india", "Art. 17.") == passage
    assert store.lookup("Constitution of India", "Article 278") is None
    assert store.search("untouchability", 0) == []
    assert store.search("zxqvnonexistent") == []
    with pytest.raises(ValueError):
        store.search("law", -1)


@pytest.mark.parametrize("text, expected", [
    ("Constitution of India Art. 17.", ("Constitution of India", "Article 17")),
    ("Indian Penal Code, Sec. 302.", ("Indian Penal Code", "Section 302")),
    ("Indian Evidence Act, 1872 s. 65B.", ("Indian Evidence Act, 1872", "Section 65B")),
    ("Section 10 (Indian Contract Act).", ("Indian Contract Act", "Section 10")),
    ("sec. 103 of the Bharatiya Nyaya Sanhita.", ("Bharatiya Nyaya Sanhita", "Section 103")),
    ("Bharatiya Nagarik Suraksha Sanhita Section 173.",
     ("Bharatiya Nagarik Suraksha Sanhita", "Section 173")),
])
def test_parse_six_acts(text, expected):
    assert parse_answer(text) == Answer([expected], False)


def test_parse_deduplicates_and_abstains():
    assert parse_answer(ABSTAIN_PHRASE.upper()) == Answer([], True)
    assert parse_answer("Constitution of India, Article 17. Article 17 (Constitution of India).") == Answer(
        [("Constitution of India", "Article 17")], False)
    assert parse_answer("Indian Evidence Act 1872 Sec. 2.").citations == [
        ("Indian Evidence Act, 1872", "Section 2")]


def test_grounded_and_prompt(store):
    passages = store.search("untouchability", 1)
    assert grounded(parse_answer("Constitution of India, Article 17."), passages)
    assert grounded(Answer([("CONSTITUTION OF INDIA", "art. 17.")], False), passages)
    assert not grounded(parse_answer("Constitution of India, Article 18."), passages)
    assert grounded(parse_answer(ABSTAIN_PHRASE), [])
    assert not grounded(parse_answer("An unsupported answer"), passages)
    assert not grounded(parse_answer(ABSTAIN_PHRASE + "; Indian Penal Code, Section 302"), passages)
    prompt = build_grounded_prompt("What is abolished?", passages)
    assert ABSTAIN_PHRASE in prompt.splitlines()[0]
    assert f"[Constitution of India, Article 17] {passages[0].text}" in prompt
    assert prompt.endswith("Question: What is abolished?\nAnswer:")


def test_deduplication_and_recall(tmp_path):
    row = dict(kind="law_lookup", act="Indian Penal Code", section="Section 302",
               source_id="IPC/302", target="punishment\n\nCitation: Indian Penal Code, Section 302.",
               prompt="punishment")
    train, heldout = tmp_path / "train.jsonl", tmp_path / "heldout.jsonl"
    train.write_text(json.dumps(row) + "\n")
    heldout.write_text(json.dumps(row) + "\n" + json.dumps(dict(
        row, kind="law_abstain", section="Section 999", prompt="absent")) + "\n")
    store = PassageStore.from_law_files(train, heldout)
    assert len(store) == 1
    assert store.passages[0].text == "punishment"
    assert retrieval_recall(store, heldout, 1) == 0.5
    assert PassageStore([]).search("anything") == []


def test_bm25_prefers_rare_term():
    store = PassageStore([
        Passage("Act", "Section 1", "common common", "Act, Section 1", "1"),
        Passage("Act", "Section 2", "common distinctive", "Act, Section 2", "2"),
    ])
    assert store.search("common distinctive", 1)[0].section == "Section 2"
