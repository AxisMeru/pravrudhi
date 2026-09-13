"""Tests for evaluate.py: answer-record construction, run_condition (B and
C with a stubbed adapt/generate/probe), and compare(). No torch, no /trackB
mount -- every model/torch/Track-B-script dependency is monkeypatched onto
the small lazy `evaluate._generate` / `_adapt` / `_snapshot` / `_restore` /
`_merged_delta_norm` / `_score_law_qa` / `_citation_correct` /
`_is_abstention_answer` hooks.
"""

from __future__ import annotations

import json

import pytest

from prototypes.nyaya_ttt_rsi import evaluate
from prototypes.nyaya_ttt_rsi.gate import GateLedger
from prototypes.nyaya_ttt_rsi.retrieval import ABSTAIN_PHRASE, Passage, PassageStore


def make_store() -> PassageStore:
    return PassageStore([
        Passage("Constitution of India", "Article 17", "Untouchability is abolished.",
                "Constitution of India, Article 17", "COI/17"),
        Passage("Indian Penal Code", "Section 302", "Punishment for murder.",
                "Indian Penal Code, Section 302", "IPC/302"),
    ])


def make_items() -> list[dict]:
    return [
        {"id": "q1", "kind": "law_citation_retrieval", "prompt": "untouchability",
         "act": "Constitution of India", "section": "Article 17"},
        {"id": "q2", "kind": "law_citation_retrieval", "prompt": "murder",
         "act": "Indian Penal Code", "section": "Section 302"},
        {"id": "q3", "kind": "law_abstain", "prompt": "nonexistent statute",
         "act": "Indian Penal Code", "section": "Section 999"},
    ]


def fake_score_law_qa(monkeypatch, rate=1.0):
    monkeypatch.setattr(evaluate, "_score_law_qa", lambda heldout_path, answers_path: {"stub": True})


def fake_citation_correct(monkeypatch, correct_ids=frozenset()):
    """Stub `_citation_correct` to succeed only for ids we say did (test
    controls truth directly rather than parsing model text)."""


class FakeProbe:
    def __init__(self, values):
        self._values = iter(values)

    def nll(self, model, tok, nll_fn):
        return next(self._values)


def test_build_answer_record_grounded_and_cited():
    passages = make_store().passages
    record = evaluate.build_answer_record("q1", "Constitution of India, Article 17.", 42, passages)
    assert record == {
        "id": "q1", "answer": "Constitution of India, Article 17.", "prompt_bytes": 42,
        "grounded": True, "cited": True, "abstained": False,
    }


def test_build_answer_record_abstain():
    passages = make_store().passages
    record = evaluate.build_answer_record("q3", ABSTAIN_PHRASE, 10, [])
    assert record["grounded"] is True
    assert record["cited"] is False
    assert record["abstained"] is True


def test_truncate_passage_text_respects_boundary():
    text = "First sentence here. Second sentence continues on and on and on. Third."
    out = evaluate.truncate_passage_text(text, max_bytes=30)
    assert len(out.encode("utf-8")) <= 30
    assert out == "First sentence here."


def test_truncate_passage_text_noop_when_short():
    assert evaluate.truncate_passage_text("short", max_bytes=100) == "short"


def test_adapt_text_from_passages_caps_bytes():
    passages = make_store().passages
    text = evaluate.adapt_text_from_passages(passages, max_bytes=20)
    assert len(text.encode("utf-8")) <= 20


def test_run_condition_B_writes_answers_and_report(tmp_path, monkeypatch):
    fake_score_law_qa(monkeypatch)
    canned = {
        "untouchability": "Constitution of India, Article 17.",
        "murder": "Indian Penal Code, Section 302.",
        "nonexistent statute": ABSTAIN_PHRASE,
    }

    def fake_generate(model, tok, prompts, max_new_tokens, stop):
        return [canned[p.split("Question: ")[1].split("\n")[0]] for p in prompts]

    monkeypatch.setattr(evaluate, "_generate", fake_generate)
    monkeypatch.setattr(evaluate, "_citation_correct",
                         lambda text, act, section: evaluate.retrieval_mod.grounded(
                             evaluate.retrieval_mod.parse_answer(text), make_store().passages))

    items = make_items()
    out_dir = tmp_path / "B"
    report = evaluate.run_condition("B", model=None, tok=None, store=make_store(), items=items, out_dir=out_dir)

    answers = evaluate.load_jsonl(out_dir / "answers.jsonl")
    assert {a["id"] for a in answers} == {"q1", "q2", "q3"}
    assert all(a["grounded"] for a in answers)
    assert report["grounded_rate"] == 1.0
    assert report["abstention_correct"] == {"successes": 1, "total": 1, "rate": 1.0}
    assert (out_dir / "report.json").exists()
    assert report["n_items"] == 3


def test_run_condition_requires_loras_and_probe_for_C():
    with pytest.raises(ValueError):
        evaluate.run_condition("C", model=None, tok=None, store=make_store(), items=make_items(), out_dir="x")


def test_run_condition_C_gate_accept_and_reject(tmp_path, monkeypatch):
    fake_score_law_qa(monkeypatch)
    monkeypatch.setattr(evaluate, "_citation_correct", lambda text, act, section: True)

    # pre-adapt text is always the (wrong) abstain phrase; post-adapt text is
    # the correct grounded citation for q1 but an UNGROUNDED citation for q2
    # -- so q1's gate should accept and q2's should reject (fall back to pre).
    pre_text = "PRE:" + ABSTAIN_PHRASE
    post_by_id = {
        "untouchability": "Constitution of India, Article 17.",
        "murder": "Bharatiya Nyaya Sanhita, Section 1.",  # not among shown passages
    }
    calls = {"generate": 0}

    def fake_generate(model, tok, prompts, max_new_tokens, stop):
        calls["generate"] += 1
        prompt = prompts[0]
        question = prompt.split("Question: ")[1].split("\n")[0]
        if calls["generate"] % 2 == 1:
            return [pre_text]
        return [post_by_id[question]]

    probe_values = [1.0, 1.0, 1.5]  # probe_before, q1's probe_after (ok), q2's probe_after (also ok -- rejection is ungroundedness)
    monkeypatch.setattr(evaluate, "_generate", fake_generate)
    monkeypatch.setattr(evaluate, "_adapt", lambda *a, **k: 0.0)
    monkeypatch.setattr(evaluate, "_snapshot", lambda loras: "SNAP")
    restored = []
    monkeypatch.setattr(evaluate, "_restore", lambda loras, snap: restored.append(snap))
    monkeypatch.setattr(evaluate, "_merged_delta_norm", lambda loras: 0.01)

    items = [make_items()[0], make_items()[1]]
    ledger = GateLedger(str(tmp_path / "ledger"))
    report = evaluate.run_condition(
        "C", model=None, tok=None, store=make_store(), items=items, out_dir=tmp_path / "C",
        loras=["fake-lora"], probe=FakeProbe(probe_values), ledger=ledger,
    )

    answers = {a["id"]: a for a in evaluate.load_jsonl(tmp_path / "C" / "answers.jsonl")}
    assert answers["q1"]["answer"] == post_by_id["untouchability"]
    assert answers["q1"]["gate"]["accepted"] is True
    assert answers["q2"]["answer"] == pre_text  # fell back: post was ungrounded
    assert answers["q2"]["gate"]["accepted"] is False
    assert answers["q2"]["gate"]["reason"] == "ungrounded"
    assert restored == ["SNAP", "SNAP"]  # restored to base snapshot after every query
    assert report["gate_summary"]["n"] == 2
    assert report["gate_summary"]["accepted"] == 1


def test_compare_writes_paired_json(tmp_path, monkeypatch):
    heldout = tmp_path / "heldout.jsonl"
    rows = [
        {"id": "q1", "act": "Constitution of India", "section": "Article 17"},
        {"id": "q2", "act": "Indian Penal Code", "section": "Section 302"},
        {"id": "q3", "act": "Indian Penal Code", "section": "Section 999"},
    ]
    heldout.write_text("\n".join(json.dumps(r) for r in rows) + "\n")

    a_dir, b_dir = tmp_path / "A", tmp_path / "B"
    evaluate.write_jsonl(a_dir / "answers.jsonl", [
        {"id": "q1", "answer": "wrong", "grounded": False},
        {"id": "q2", "answer": "wrong", "grounded": False},
        {"id": "q3", "answer": "wrong", "grounded": False},
    ])
    evaluate.write_jsonl(b_dir / "answers.jsonl", [
        {"id": "q1", "answer": "right", "grounded": True},
        {"id": "q2", "answer": "wrong", "grounded": False},
        {"id": "q3", "answer": "right", "grounded": True},
    ])
    # "right" is correct, "wrong" is not -- deterministic stub standing in for
    # score_law_qa's real substring matcher.
    monkeypatch.setattr(evaluate, "_citation_correct", lambda text, act, section: text == "right")

    out_path = tmp_path / "paired.json"
    result = evaluate.compare(a_dir, b_dir, heldout_path=heldout, out_path=out_path)

    assert result["n"] == 3
    assert result["gold_citation_present"]["a_rate"] == 0.0
    assert result["gold_citation_present"]["b_rate"] == pytest.approx(2 / 3)
    assert result["gold_citation_present"]["mcnemar_b"] == 2  # b succeeds, a fails: q1, q3
    assert result["gold_citation_present"]["mcnemar_c"] == 0
    assert result["grounded"]["b_rate"] == pytest.approx(2 / 3)
    assert out_path.exists()
    assert json.loads(out_path.read_text()) == result
