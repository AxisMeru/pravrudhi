"""Tests for the RSI round-3+ pure-Python data-construction helpers in
grounded_data.py (sample_round_stream, shuffle_passages_copy,
gate_round_accepts). No torch, no /trackB mount -- shuffle_passages_copy's
only model-touching dependency (evaluate.render_prompt/parse_question_from_prompt)
is pure string/passage manipulation, same as the rest of evaluate.py's
host-testable surface.
"""

from __future__ import annotations

import random

from prototypes.nyaya_ttt_rsi import evaluate, grounded_data
from prototypes.nyaya_ttt_rsi.retrieval import Passage


def make_records():
    records = []
    for i in range(6):
        records.append({"id": f"cr{i}", "kind": "law_citation_retrieval", "act": "A", "section": f"S{i}",
                         "prompt": f"q{i}", "target": "t"})
    for i in range(6):
        records.append({"id": f"ct{i}", "kind": "law_cite_to_title", "act": "A", "section": f"T{i}",
                         "prompt": f"q{i}", "target": "t"})
    for i in range(6):
        records.append({"id": f"ll{i}", "kind": "law_lookup", "act": "A", "section": f"L{i}",
                         "prompt": f"q{i}", "target": "t"})
    return records


# ---------------------------------------------------------------------------
# sample_round_stream
# ---------------------------------------------------------------------------


def test_sample_round_stream_returns_n_distinct_eligible_records():
    records = make_records()
    out = grounded_data.sample_round_stream(records, n=10, seed=0)
    assert len(out) == 10
    ids = [r["id"] for r in out]
    assert len(set(ids)) == len(ids)
    assert all(r["kind"] in grounded_data.CITATION_KINDS for r in out)


def test_sample_round_stream_excludes_given_ids():
    records = make_records()
    exclude = {f"cr{i}" for i in range(6)}
    out = grounded_data.sample_round_stream(records, n=12, exclude_ids=exclude, seed=1)
    assert not any(r["id"] in exclude for r in out)
    # only 12 non-excluded records remain (6 title + 6 lookup)
    assert len(out) == 12


def test_sample_round_stream_oversamples_target_kind():
    records = make_records()
    # Draw a large sample from the whole pool (18 eligible) many times and
    # check the oversampled kind is picked proportionally more often across
    # seeds (statistical, not exact-count, since sampling is without
    # replacement and n < pool size forces a real selection).
    counts = {"law_citation_retrieval": 0, "law_cite_to_title": 0, "law_lookup": 0}
    trials = 30
    for seed in range(trials):
        out = grounded_data.sample_round_stream(records, n=6, oversample_factor=2.0, seed=seed)
        for r in out:
            counts[r["kind"]] += 1
    assert counts["law_citation_retrieval"] > counts["law_cite_to_title"]
    assert counts["law_citation_retrieval"] > counts["law_lookup"]


def test_sample_round_stream_deterministic_given_seed():
    records = make_records()
    a = grounded_data.sample_round_stream(records, n=8, seed=42)
    b = grounded_data.sample_round_stream(records, n=8, seed=42)
    assert [r["id"] for r in a] == [r["id"] for r in b]


def test_sample_round_stream_rejects_negative_n():
    import pytest

    with pytest.raises(ValueError):
        grounded_data.sample_round_stream(make_records(), n=-1)


# ---------------------------------------------------------------------------
# shuffle_passages_copy
# ---------------------------------------------------------------------------


def make_four_passages():
    return [
        Passage("Act X", f"Section {i}", f"Body text number {i} about the provision.",
                f"Act X, Section {i}", f"px{i}", title=f"Heading {i}")
        for i in range(4)
    ]


def test_shuffle_passages_copy_changes_order_keeps_target_and_passage_set():
    passages = make_four_passages()
    question = "What does Act X, Section 2 provide?"
    prompt, trunc, _pb, _dropped = evaluate.render_prompt(question, passages)
    target = "Section 2 (Act X).\n\n"

    rng = random.Random(0)
    copy = grounded_data.shuffle_passages_copy(prompt, target, trunc, rng)

    assert copy is not None
    assert copy["target"] == target
    orig_keys = sorted((p.act, p.section) for p in trunc)
    copy_keys = sorted((p.act, p.section) for p in copy["passages"])
    assert orig_keys == copy_keys
    # order actually changed (not just re-rendered identically)
    assert [(p.act, p.section) for p in copy["passages"]] != [(p.act, p.section) for p in trunc]
    assert copy["prompt"] != prompt


def test_shuffle_passages_copy_returns_none_for_single_passage():
    passages = make_four_passages()[:1]
    question = "What does Act X, Section 0 provide?"
    prompt, trunc, _pb, _dropped = evaluate.render_prompt(question, passages)
    rng = random.Random(0)
    assert grounded_data.shuffle_passages_copy(prompt, "t", trunc, rng) is None


# ---------------------------------------------------------------------------
# gate_round_accepts
# ---------------------------------------------------------------------------


def test_gate_round_accepts_rejects_on_probe_regression():
    ok, reason = grounded_data.gate_round_accepts(
        0.20, {"law_lookup": 0.8}, {"law_lookup": 0.8}, probe_threshold=0.15)
    assert ok is False
    assert "probe_regression" in reason


def test_gate_round_accepts_rejects_on_kind_drop():
    ok, reason = grounded_data.gate_round_accepts(
        0.01, {"law_lookup": 0.80, "law_cite_to_title": 0.10},
        {"law_lookup": 0.70, "law_cite_to_title": 0.30}, max_kind_drop=0.05)
    assert ok is False
    assert "law_lookup" in reason


def test_gate_round_accepts_accepts_small_drop_within_tolerance():
    ok, reason = grounded_data.gate_round_accepts(
        0.05, {"law_lookup": 0.80}, {"law_lookup": 0.77}, probe_threshold=0.15, max_kind_drop=0.05)
    assert ok is True
    assert reason == "ok"


def test_gate_round_accepts_ignores_kind_missing_from_after():
    ok, reason = grounded_data.gate_round_accepts(
        0.0, {"law_lookup": 0.8, "law_abstain": 0.5}, {"law_lookup": 0.8})
    assert ok is True
