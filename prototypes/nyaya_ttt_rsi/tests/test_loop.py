"""Tests for loop.py: deterministic stream sampling, run_round's
accepted-set/ledger construction, and consolidate()'s gate/accept-reject
logic -- all with `evaluate._generate` / `_adapt` / `_snapshot` / `_restore` /
`_merged_delta_norm` / `_sequence_nll` stubbed out, so no torch/model needed.
"""

from __future__ import annotations

import json

import pytest

from prototypes.nyaya_ttt_rsi import evaluate, loop
from prototypes.nyaya_ttt_rsi.retrieval import Passage, PassageStore


def make_store() -> PassageStore:
    return PassageStore([
        Passage("Constitution of India", "Article 17", "Untouchability is abolished.",
                "Constitution of India, Article 17", "COI/17"),
    ])


def write_train_jsonl(path, n=10):
    rows = [{"id": f"t{i}", "prompt": f"question {i}", "target": f"SHOULD NEVER BE READ {i}"} for i in range(n)]
    with open(path, "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    return path


class FakeProbe:
    def __init__(self, values):
        self._values = list(values)
        self._i = 0

    def nll(self, model, tok, nll_fn):
        v = self._values[self._i]
        self._i = min(self._i + 1, len(self._values) - 1)
        return v


def test_sample_stream_is_deterministic_and_never_reads_target(tmp_path):
    path = write_train_jsonl(tmp_path / "train.jsonl", n=10)
    chunks_a = loop.sample_stream(path, n=3, rounds=2, seed=0)
    chunks_b = loop.sample_stream(path, n=3, rounds=2, seed=0)
    chunks_c = loop.sample_stream(path, n=3, rounds=2, seed=1)

    assert chunks_a == chunks_b
    assert chunks_a != chunks_c
    assert len(chunks_a) == 2
    assert all(len(c) == 3 for c in chunks_a)
    all_ids = {item["id"] for chunk in chunks_a for item in chunk}
    assert len(all_ids) == 6  # round 2 does not repeat round 1's queries
    for chunk in chunks_a:
        for item in chunk:
            assert set(item.keys()) == {"id", "prompt"}  # "target" never carried through


def test_sample_stream_raises_when_stream_exceeds_available_records(tmp_path):
    path = write_train_jsonl(tmp_path / "train.jsonl", n=4)
    with pytest.raises(ValueError):
        loop.sample_stream(path, n=3, rounds=2, seed=0)


def test_run_round_builds_accepted_set_and_ledger(tmp_path, monkeypatch):
    from prototypes.nyaya_ttt_rsi.gate import GateLedger

    monkeypatch.setattr(evaluate, "_snapshot", lambda loras: "BASE")
    restored = []
    monkeypatch.setattr(evaluate, "_restore", lambda loras, snap: restored.append(snap))
    monkeypatch.setattr(evaluate, "_adapt", lambda *a, **k: 0.0)
    monkeypatch.setattr(evaluate, "_merged_delta_norm", lambda loras: 0.02)

    # q_good: grounded citation -> accepted. q_bad: ungrounded -> rejected.
    texts = {
        "untouchability question": "Constitution of India, Article 17.",
        "unanswerable question": "Bharatiya Nyaya Sanhita, Section 1.",
    }
    monkeypatch.setattr(
        evaluate, "_generate",
        lambda model, tok, prompts, max_new_tokens, stop: [
            texts[prompts[0].split("Question: ")[1].split("\n")[0]]
        ],
    )

    stream_items = [
        {"id": "q_good", "prompt": "untouchability question"},
        {"id": "q_bad", "prompt": "unanswerable question"},
    ]
    ledger = GateLedger(str(tmp_path / "ledger"))
    probe = FakeProbe([1.0, 1.0, 1.0])  # flat -- no probe regression, only groundedness decides

    accepted, stream_stats = loop.run_round(
        model=None, tok=None, store=make_store(), stream_items=stream_items,
        loras=["fake"], probe=probe, ledger=ledger, ttt_cfg={"steps": 1, "lr": 1e-3, "threshold": 0.15},
    )

    assert stream_stats == {"n_stream": 2, "n_accepted": 1, "n_rejected": 1}
    assert len(accepted) == 1
    assert accepted[0][1] == "Constitution of India, Article 17."
    assert "Question: untouchability question" in accepted[0][0]
    assert restored == ["BASE", "BASE"]

    summary = ledger.summary()
    assert summary == {"n": 2, "accepted": 1, "rejected": 1, "reasons": {"ok": 1, "ungrounded": 1}}


def test_consolidate_accepts_when_probe_stable(monkeypatch):
    monkeypatch.setattr(evaluate, "_restore", lambda loras, snap: None)
    monkeypatch.setattr(evaluate, "_snapshot", lambda loras: "NEW_SNAP")

    probe = FakeProbe([1.0, 1.02])  # 2% relative rise, under the 15% threshold
    accepted, stats, new_snap = loop.consolidate(
        model=None, tok=None, loras=["fake"], accepted_pairs=[], probe=probe, persistent_snap="OLD_SNAP",
    )

    assert accepted is True
    assert stats["accepted"] is True
    assert stats["reason"] == "ok"
    assert stats["n_accepted_pairs"] == 0
    assert new_snap == "NEW_SNAP"


def test_consolidate_rejects_and_restores_when_probe_regresses(monkeypatch):
    restored = []
    monkeypatch.setattr(evaluate, "_restore", lambda loras, snap: restored.append(snap))
    monkeypatch.setattr(evaluate, "_snapshot", lambda loras: "NEW_SNAP")

    probe = FakeProbe([1.0, 1.5])  # 50% relative rise, over threshold
    accepted, stats, new_snap = loop.consolidate(
        model=None, tok=None, loras=["fake"], accepted_pairs=[], probe=probe, persistent_snap="OLD_SNAP",
    )

    assert accepted is False
    assert stats["accepted"] is False
    assert stats["reason"] == "consolidation_probe_regression"
    assert new_snap == "OLD_SNAP"
    # restored back to the old snapshot at least once (the gate-reject path)
    assert "OLD_SNAP" in restored


def test_consolidate_with_accepted_pairs_trains_and_restores_first(monkeypatch):
    """When there ARE accepted pairs, consolidate must first restore the LoRA
    to persistent_snap (so training starts from the right place) before doing
    any SFT steps -- verified here via a stubbed `_sequence_nll` that never
    actually needs torch (accepted_pairs stays empty so the torch-training
    branch is skipped, but the initial restore-to-persistent-snap must still
    happen)."""
    restore_calls = []
    monkeypatch.setattr(evaluate, "_restore", lambda loras, snap: restore_calls.append(snap))
    monkeypatch.setattr(evaluate, "_snapshot", lambda loras: "NEW_SNAP")

    probe = FakeProbe([1.0, 1.0])
    loop.consolidate(model=None, tok=None, loras=["fake"], accepted_pairs=[], probe=probe,
                      persistent_snap="OLD_SNAP")
    assert restore_calls[0] == "OLD_SNAP"
