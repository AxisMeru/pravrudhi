import pytest

from prototypes.nyaya_ttt_rsi.retrieval import Passage, PassageStore
from prototypes.nyaya_ttt_rsi.retrieval_tuning import (
    TunedStore, discover_abbreviations, eligible_rows,
)


def passages():
    return [
        Passage("Indian Penal Code", "Section 1", "ordinary body", "Indian Penal Code, Section 1", "1", "zephyr"),
        Passage("Indian Penal Code", "Section 2", "zephyr zephyr", "Indian Penal Code, Section 2", "2", "ordinary"),
        Passage("Other Act", "Section 1", "ordinary body", "Other Act, Section 1", "3", "zephyr"),
    ]


def test_default_matches_pooled_baseline():
    baseline, tuned = PassageStore(passages()), TunedStore(passages())
    for query in ("zephyr", "Indian Penal Code Section 2", "ordinary", "ZEPhyR?!", "unseen", ""):
        for k in (0, 1, 2, 5):
            assert tuned.search(query, k) == baseline.search(query, k)
    assert TunedStore([]).search("anything") == []
    with pytest.raises(ValueError):
        tuned.search("zephyr", -1)


def test_field_weights_change_ranking():
    title = TunedStore(passages(), w_title=10, w_header=0, w_body=0)
    body = TunedStore(passages(), w_title=0, w_header=0, w_body=10)
    assert title.search("zephyr", 1)[0] == passages()[0]
    assert body.search("zephyr", 1)[0] == passages()[1]


def test_observed_abbreviations_expand_only_at_boundaries():
    prompts = ["IPC Sec. 2", "S.1", "Art. 17", "BNSS Section 1"]
    aliases = discover_abbreviations(prompts, ["Indian Penal Code", "Bharatiya Nagarik Suraksha Sanhita"])
    assert aliases == {"Art.": "Article", "Sec.": "Section", "S.": "Section", "BNSS": "Bharatiya Nagarik Suraksha Sanhita", "IPC": "Indian Penal Code"}
    assert discover_abbreviations(["full words only"], ["Indian Penal Code"]) == {}
    store = TunedStore(passages(), expand_abbreviations=True, abbreviations=aliases)
    assert store.normalize_query("IPC Sec. 2") == "indian penal code section 2"
    assert store.search("IPC Sec. 2") == store.search("Indian Penal Code Section 2")
    assert store.normalize_query("chIPC S.1") == "chipc section 1"


def test_exact_boost_checks_section_and_explicit_act():
    ordinary = TunedStore(passages(), w_title=0, w_header=0, w_body=1)
    boosted = TunedStore(passages(), w_title=0, w_header=0, w_body=1, exact_boost=50)
    query = "zephyr Indian Penal Code Section 1"
    assert ordinary.search(query, 1)[0] == passages()[1]
    assert boosted.search(query, 1)[0] == passages()[0]
    assert boosted._exact_matches(query).tolist() == [1, 0, 0]
    assert boosted._exact_matches("Section 10").tolist() == [0, 0, 0]
    assert boosted._exact_matches("Article 1").tolist() == [0, 0, 0]


def test_normalization_options():
    p = Passage("Act", "Section 1", "UPPER !", "Act, Section 1", None)
    assert TunedStore([p]).search("upper") == [p]
    assert TunedStore([p], lowercase=False).search("upper") == []
    assert TunedStore([p], strip_punctuation=False).search("!") == [p]
    assert TunedStore([p]).search("!") == []


def test_full_body_exclusion_retains_unretrievable_rows():
    rows = [dict(act="Indian Penal Code", section="Section 1", prompt="Quote ORDINARY  body please"),
            dict(act="Indian Penal Code", section="Section 1", prompt="zephyr"),
            dict(act="Missing Act", section="Section 999", prompt="absent")]
    assert eligible_rows(rows, PassageStore(passages())) == rows[1:]
