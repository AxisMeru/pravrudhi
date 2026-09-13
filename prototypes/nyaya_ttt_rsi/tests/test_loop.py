"""Tests for loop.py: deterministic stream sampling, run_round's
accepted-set/ledger construction, and consolidate()'s gate/accept-reject
logic -- all with `evaluate._generate` / `_adapt` / `_snapshot` / `_restore` /
`_merged_delta_norm` / `_sequence_nll` stubbed out, so no torch/model needed.
"""

from __future__ import annotations

import json

import pytest

from prototypes.nyaya_ttt_rsi import evaluate, loop
from prototypes.nyaya_ttt_rsi.retrieval import ABSTAIN_PHRASE, Passage, PassageStore


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


def test_consolidate_from_dataset_accepts_and_saves_new_snapshot(tmp_path, monkeypatch):
    dataset = tmp_path / "grounded_sft.jsonl"
    with open(dataset, "w") as f:
        for i in range(3):
            f.write(json.dumps({"prompt": f"p{i}", "target": f"t{i}"}) + "\n")

    monkeypatch.setattr(evaluate, "_restore", lambda loras, snap: None)
    monkeypatch.setattr(evaluate, "_snapshot", lambda loras: "NEW_SNAP")
    trained_pairs = []
    monkeypatch.setattr(loop, "sft_train",
                         lambda model, tok, loras, pairs, **k: trained_pairs.extend(pairs) or [])

    probe = FakeProbe([1.0, 1.02])  # small rise, under threshold
    accepted, stats, new_snap = loop.consolidate_from_dataset(
        model=None, tok=None, loras=["fake"], dataset_path=dataset, probe=probe,
        persistent_snap="OLD_SNAP", lr=3e-4, epochs=1, threshold=0.15,
    )

    assert accepted is True
    assert new_snap == "NEW_SNAP"
    assert stats["n_examples"] == 3
    assert stats["retried_at_half_lr"] is False
    assert trained_pairs == [("p0", "t0"), ("p1", "t1"), ("p2", "t2")]


def test_consolidate_from_dataset_retries_at_half_lr_then_gives_up(tmp_path, monkeypatch):
    dataset = tmp_path / "grounded_sft.jsonl"
    dataset.write_text(json.dumps({"prompt": "p", "target": "t"}) + "\n")

    monkeypatch.setattr(evaluate, "_restore", lambda loras, snap: None)
    monkeypatch.setattr(evaluate, "_snapshot", lambda loras: "NEW_SNAP")
    monkeypatch.setattr(loop, "sft_train", lambda *a, **k: [])

    # Both the first attempt and the halved-lr retry regress -- both rejected.
    probe = FakeProbe([1.0, 2.0, 1.0, 2.0])
    accepted, stats, new_snap = loop.consolidate_from_dataset(
        model=None, tok=None, loras=["fake"], dataset_path=dataset, probe=probe,
        persistent_snap="OLD_SNAP", lr=3e-4, epochs=1, threshold=0.15, retry_halved_lr=True,
    )

    assert accepted is False
    assert stats["retried_at_half_lr"] is True
    assert stats["lr"] == 1.5e-4
    assert new_snap == "OLD_SNAP"


def test_sft_train_runs_one_shared_optimizer_across_all_pairs_and_epochs(monkeypatch):
    """Uses a tiny real torch model (skipped if torch is unavailable, e.g. on
    the host) to check `sft_train` actually reduces loss and logs at the
    requested cadence -- the closest thing to an integration test we can run
    without the real 370M checkpoint."""
    torch = __import__("pytest").importorskip("torch")
    from torch import nn

    class TinyModel(nn.Module):
        def __init__(self):
            super().__init__()
            self.embed = nn.Embedding(256, 8)
            self.proj = nn.Linear(8, 256)

        def forward(self, tokens, boundary, roles):
            return self.proj(self.embed(tokens))

    model = TinyModel()

    class ByteTok:
        def encode(self, s):
            return list(s.encode("utf-8"))

    tok = ByteTok()

    class FakeLora:
        def __init__(self):
            self.lora_A = nn.Parameter(torch.zeros(1))
            self.lora_B = nn.Parameter(torch.zeros(1))

    loras = [FakeLora()]
    # Route the tensor loss straight at the tiny model instead of the real
    # evaluate._tensor_nll_loss (which assumes a NemotronH-shaped forward).
    from prototypes.nyaya_ttt_rsi import evaluate as evaluate_mod

    def fake_tensor_nll_loss(m, t, prompt, continuation):
        ids = torch.tensor(t.encode(prompt + continuation), dtype=torch.long).unsqueeze(0)
        logits = m(ids, None, None)
        target = ids[:, 1:].reshape(-1)
        pred = logits[:, :-1].reshape(-1, 256)
        return torch.nn.functional.cross_entropy(pred, target) + loras[0].lora_A.sum() * 0

    monkeypatch.setattr(evaluate_mod, "_tensor_nll_loss", fake_tensor_nll_loss)

    from prototypes.nyaya_ttt_rsi import loop as loop_mod

    logged = loop_mod.sft_train(model, tok, loras, [("ab", "cd"), ("ef", "gh")], lr=1e-2, epochs=1, log_every=1)
    assert len(logged) == 2
    assert model.training is False


def test_in_sample_sanity_check_computes_hit_and_spurious_rates(tmp_path, monkeypatch):
    dataset = tmp_path / "grounded_sft.jsonl"
    rows = [
        {"id": "c1", "kind": "law_citation_retrieval", "act": "Indian Penal Code", "section": "Section 302",
         "prompt": "p1", "target": "t1", "synthetic_abstain": False},
        {"id": "c2", "kind": "law_lookup", "act": "Constitution of India", "section": "Article 17",
         "prompt": "p2", "target": "t2", "synthetic_abstain": False},
        {"id": "a1", "kind": "law_abstain", "act": "X", "section": "Y", "prompt": "p3", "target": "abstain"},
    ]
    with open(dataset, "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")

    answers = {"p1": "Indian Penal Code, Section 302.", "p2": ABSTAIN_PHRASE, "p3": ABSTAIN_PHRASE}
    monkeypatch.setattr(evaluate, "_generate", lambda model, tok, prompts, mnt, stop: [answers[prompts[0]]])
    monkeypatch.setattr(evaluate, "_citation_correct", lambda text, act, section: text == "Indian Penal Code, Section 302.")

    result = loop.in_sample_sanity_check(model=None, tok=None, dataset_path=dataset, n_citation=2, n_abstain=1, seed=0)

    assert result["n_citation"] == 2
    assert result["citation_hit_rate"] == 0.5  # c1 hit, c2 spuriously abstained
    assert result["spurious_abstain_rate"] == 0.5
    assert result["n_abstain"] == 1
    assert result["abstain_correct_rate"] == 1.0
    assert result["passed"] is False  # 0.5 >= 0.5 hit-rate OK, but spurious 0.5 > 0.2 fails


def test_in_sample_sanity_check_scoring_uses_reconstructed_passages(tmp_path, monkeypatch):
    from prototypes.nyaya_ttt_rsi.retrieval import Passage, build_grounded_prompt

    passages = [Passage("Indian Penal Code", "Section 302", "Punishment for murder.", "x", None)]
    citation_prompt = build_grounded_prompt("murder", passages)
    abstain_prompt = build_grounded_prompt("something else", passages)

    dataset = tmp_path / "grounded_sft.jsonl"
    rows = [
        {"id": "c1", "kind": "law_citation_retrieval", "act": "Indian Penal Code", "section": "Section 302",
         "prompt": citation_prompt, "target": "t", "synthetic_abstain": False},
        {"id": "a1", "kind": "law_abstain", "act": "X", "section": "Y", "prompt": abstain_prompt, "target": "abstain"},
    ]
    with open(dataset, "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")

    def fake_nll(model, tok, prompt, text):
        # Correct citation wins for c1 (question "murder"); abstain wins for
        # a1 (question "something else") -- discriminate on the QUESTION,
        # not the shared passage text (both prompts' passage block mentions
        # "murder" regardless of which question was asked).
        if "Question: murder" in prompt:
            return 0.1 if "Section 302" in text else 5.0
        return 5.0 if "Section 302" in text else 0.1

    monkeypatch.setattr(evaluate, "_sequence_nll", fake_nll)

    result = loop.in_sample_sanity_check_scoring(model=None, tok=None, dataset_path=dataset,
                                                  n_citation=1, n_abstain=1, seed=0)
    assert result["citation_hit_rate"] == 1.0
    assert result["spurious_abstain_rate"] == 0.0
    assert result["abstain_correct_rate"] == 1.0
    assert result["passed"] is True
