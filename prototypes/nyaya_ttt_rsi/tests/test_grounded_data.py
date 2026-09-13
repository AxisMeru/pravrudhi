"""Tests for grounded_data.py (Phase 1, harness-built grounded SFT set) --
pure python, tiny synthetic train/heldout files, no torch/model needed."""

from __future__ import annotations

import json

from prototypes.nyaya_ttt_rsi import grounded_data
from prototypes.nyaya_ttt_rsi.retrieval import ABSTAIN_PHRASE, PassageStore


def _row(id_, kind, act, section, prompt, target):
    return {"id": id_, "kind": kind, "act": act, "section": section, "prompt": prompt,
            "target": target, "source_id": id_}


def make_files(tmp_path):
    train_rows = [
        _row("cr1", "law_citation_retrieval", "Indian Penal Code", "Section 302",
             "murder punishment", "Indian Penal Code, Section 302."),
        _row("ct1", "law_cite_to_title", "Constitution of India", "Article 17",
             "what is article 17 about", "Untouchability abolished."),
        _row("lk1", "law_lookup", "Constitution of India", "Article 17",
             "what does article 17 provide",
             "Untouchability is abolished and its practice in any form is forbidden.\n\nCitation: Constitution of India, Article 17."),
        _row("ab1", "law_abstain", "Indian Penal Code", "Section 999",
             "nonexistent provision", ABSTAIN_PHRASE),
    ]
    heldout_rows = [
        _row("h1", "law_lookup", "Indian Penal Code", "Section 302", "murder",
             "Whoever commits murder.\n\nCitation: Indian Penal Code, Section 302."),
    ]
    train = tmp_path / "train.jsonl"
    heldout = tmp_path / "heldout.jsonl"
    train.write_text("\n".join(json.dumps(r) for r in train_rows) + "\n")
    heldout.write_text("\n".join(json.dumps(r) for r in heldout_rows) + "\n")
    return train, heldout


def test_build_lookup_target_keeps_citation_line():
    raw = "A" * 500 + "\n\nCitation: Indian Penal Code, Section 302."
    out = grounded_data.build_lookup_target(raw, "Indian Penal Code", "Section 302", max_text_bytes=50)
    assert out.endswith("Citation: Indian Penal Code, Section 302.")
    assert len(out.split("\n\n")[0].encode("utf-8")) <= 50


def test_build_lookup_target_synthesizes_citation_line_when_missing():
    out = grounded_data.build_lookup_target("some text with no citation line", "Act", "Section 1", 100)
    assert out.endswith("Citation: Act, Section 1.")


def test_build_dataset_grounds_every_non_abstain_citation_example(tmp_path):
    train, heldout = make_files(tmp_path)
    examples, stats = grounded_data.build_dataset(train, heldout, n=3, abstain_frac=0.0, seed=0)

    assert stats["n_rejected_ungrounded"] == 0
    kinds = {e["kind"] for e in examples}
    assert "law_citation_retrieval" in kinds
    assert "law_lookup" in kinds
    # the train split's own law_abstain record is always included
    assert any(e["kind"] == "law_abstain" for e in examples)
    for e in examples:
        assert e["prompt"].endswith("\nAnswer:")


def test_build_dataset_synthetic_abstain_excludes_gold_passage(tmp_path):
    train, heldout = make_files(tmp_path)
    # abstain_frac=1.0 forces every sampled citation-kind example to abstain
    examples, stats = grounded_data.build_dataset(train, heldout, n=3, abstain_frac=1.0, seed=0)

    synthetic = [e for e in examples if e["synthetic_abstain"]]
    assert synthetic
    for e in synthetic:
        assert e["target"] == ABSTAIN_PHRASE + grounded_data.TARGET_STOP_SUFFIX
        gold_citation = f"[{e['act']}, {e['section']}]"
        assert gold_citation not in e["prompt"]


def test_build_example_returns_none_without_gold_passage():
    store = PassageStore([])
    rec = _row("x", "law_citation_retrieval", "Indian Penal Code", "Section 1", "q", "t")
    assert grounded_data.build_example(rec, store, rng=__import__("random").Random(0)) is None


def test_cite_to_title_skips_citation_gate_but_requires_nonempty_title(tmp_path):
    train, heldout = make_files(tmp_path)
    examples, stats = grounded_data.build_dataset(train, heldout, n=3, abstain_frac=0.0, seed=1)
    title_examples = [e for e in examples if e["kind"] == "law_cite_to_title"]
    assert title_examples
    assert title_examples[0]["target"] == "Untouchability abolished." + grounded_data.TARGET_STOP_SUFFIX
