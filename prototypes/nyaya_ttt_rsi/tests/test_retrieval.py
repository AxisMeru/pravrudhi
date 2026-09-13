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
    # Title (never truncated) is rendered ahead of the body -- F13.
    assert passages[0].title
    assert f"[Constitution of India, Article 17] {passages[0].title}. {passages[0].text}" in prompt
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


def test_from_law_files_attaches_title_from_cite_to_title_records(store):
    passage = store.lookup("Constitution of India", "Article 17")
    assert passage.title  # F13: every law_lookup passage gets a title
    assert passage.title != passage.text


def test_title_is_indexed_for_retrieval(tmp_path):
    """A query matching ONLY the title (not the body) must still retrieve
    the passage -- F13's whole point."""
    row_lookup = dict(kind="law_lookup", act="Act", section="Section 1", source_id="1",
                       target="Body text with no matching words.\n\nCitation: Act, Section 1.",
                       prompt="p")
    row_title = dict(kind="law_cite_to_title", act="Act", section="Section 1", source_id="1t",
                      target="Zephyr commencement heading", prompt="p2")
    train = tmp_path / "train.jsonl"
    heldout = tmp_path / "heldout.jsonl"
    train.write_text(json.dumps(row_lookup) + "\n" + json.dumps(row_title) + "\n")
    heldout.write_text("")
    store = PassageStore.from_law_files(train, heldout)
    hits = store.search("zephyr commencement", 1)
    assert hits and hits[0].section == "Section 1"


def test_title_never_truncated(tmp_path):
    from prototypes.nyaya_ttt_rsi import evaluate

    long_title = "A very long section heading " * 20  # far over any truncation budget
    p = Passage("Act", "Section 1", "short body", "Act, Section 1", None, title=long_title)
    truncated = evaluate.truncate_passages([p], max_bytes=50)
    assert truncated[0].title == long_title
    assert len(truncated[0].text.encode("utf-8")) <= 50
