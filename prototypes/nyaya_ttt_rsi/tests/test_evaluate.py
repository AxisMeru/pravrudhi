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


# ---------------------------------------------------------------------------
# Scoring mode (F12 greedy-decoding-trap fix)
# ---------------------------------------------------------------------------


def test_build_candidates_citation_kind():
    passages = make_store().passages
    candidates = evaluate.build_candidates(passages, "law_citation_retrieval")
    assert len(candidates) == 3  # 2 passages + abstain
    assert candidates[0] == ("Article 17 (Constitution of India).", passages[0])
    assert candidates[1] == ("Section 302 (Indian Penal Code).", passages[1])
    assert candidates[2] == (ABSTAIN_PHRASE, None)


def test_build_candidates_uniform_across_kinds():
    """F15 fix: every kind scores the same canonical citation candidate --
    `law_lookup` no longer gets a full-body candidate (that was the
    length-biased one)."""
    passages = make_store().passages[:1]
    for kind in ("law_lookup", "law_citation_retrieval", "law_cite_to_title"):
        candidates = evaluate.build_candidates(passages, kind)
        text, source = candidates[0]
        assert text == "Article 17 (Constitution of India)."
        assert source is passages[0]


def test_compose_answer_law_lookup_uses_full_untruncated_body_from_store():
    from prototypes.nyaya_ttt_rsi.retrieval import Passage, PassageStore

    full = Passage("Constitution of India", "Article 17", "Untouchability is abolished " * 20,
                   "Constitution of India, Article 17", "COI/17")
    store = PassageStore([full])
    truncated_copy = evaluate.truncate_passages([full], max_bytes=30)[0]

    answer = evaluate.compose_answer("law_lookup", truncated_copy, store)
    assert answer == f"{full.text}\n\nCitation: Constitution of India, Article 17."
    assert len(answer) > 30  # composed from the FULL body, not the truncated in-context copy


def test_compose_answer_cite_to_title_uses_title():
    from prototypes.nyaya_ttt_rsi.retrieval import Passage, PassageStore

    p = Passage("Act", "Section 1", "body", "Act, Section 1", None, title="A Fine Title")
    store = PassageStore([p])
    assert evaluate.compose_answer("law_cite_to_title", p, store) == "A Fine Title"


def test_grounded_data_shares_the_same_prompt_config_object():
    """F16: training (grounded_data.py) and eval (evaluate.py) must go
    through the exact same PromptConfig object, not merely an equal copy,
    so the two paths cannot silently drift apart."""
    from prototypes.nyaya_ttt_rsi import grounded_data

    assert grounded_data.PROMPT_CONFIG is evaluate.PROMPT_CONFIG


def test_render_prompt_drops_lowest_ranked_passage_when_over_budget(monkeypatch):
    from prototypes.nyaya_ttt_rsi.retrieval import Passage

    tiny_config = evaluate.PromptConfig(k=4, body_max_bytes=60, title_max_bytes=90,
                                        max_prompt_bytes=250, instruction="Cite the correct provision below.")
    passages = [Passage("Act", f"Section {i}", "body text " * 10, f"Act, Section {i}", None,
                        title=f"Title {i}") for i in range(4)]
    prompt, used, pb, dropped = evaluate.render_prompt("q?", passages, tiny_config)
    assert dropped is True
    assert len(used) < 4
    assert pb <= tiny_config.max_prompt_bytes


def test_render_prompt_no_drop_when_within_budget():
    from prototypes.nyaya_ttt_rsi.retrieval import Passage

    passages = [Passage("Act", "Section 1", "short body", "Act, Section 1", None, title="Title")]
    prompt, used, pb, dropped = evaluate.render_prompt("q?", passages)
    assert dropped is False
    assert len(used) == 1


def test_truncate_at_space_never_cuts_mid_word():
    text = "one two three four five"
    out = evaluate.truncate_at_space(text, max_bytes=10)
    assert out == "one two"
    assert len(out.encode("utf-8")) <= 10


def test_compose_answer_citation_kind_uses_canonical_format():
    from prototypes.nyaya_ttt_rsi.retrieval import Passage, PassageStore

    p = Passage("Act", "Section 1", "body", "Act, Section 1", None)
    store = PassageStore([p])
    assert evaluate.compose_answer("law_citation_retrieval", p, store) == "Section 1 (Act)."


def test_score_candidates_picks_lowest_nll(monkeypatch):
    nlls_by_text = {"a": 2.0, "b": 0.5, "c": 3.0}
    monkeypatch.setattr(evaluate, "_sequence_nll", lambda model, tok, prompt, text: nlls_by_text[text])
    candidates = [("a", "src_a"), ("b", "src_b"), ("c", "src_c")]
    result = evaluate.score_candidates(model=None, tok=None, prompt="p", candidates=candidates)
    assert result["best_idx"] == 1
    assert result["nlls"] == [2.0, 0.5, 3.0]
    assert result["margin"] == pytest.approx(1.5)  # 2.0 - 0.5


def test_parse_question_and_passages_round_trip():
    from prototypes.nyaya_ttt_rsi.retrieval import build_grounded_prompt

    passages = make_store().passages
    prompt = build_grounded_prompt("What is abolished?", passages)
    assert evaluate.parse_question_from_prompt(prompt) == "What is abolished?"
    recovered = evaluate.parse_passages_from_prompt(prompt)
    assert [(p.act, p.section) for p in recovered] == [(p.act, p.section) for p in passages]


def test_run_condition_scoring_B_picks_gold_and_reports_grounded_1(tmp_path, monkeypatch):
    fake_score_law_qa(monkeypatch)
    passages = make_store().passages

    def fake_nll(model, tok, prompt, text):
        # Lowest NLL for the correct citation of whichever passage is asked about.
        if "Article 17" in text:
            return 0.1
        if "Section 302" in text:
            return 0.2
        return 5.0  # abstain (and any wrong candidate) scores worst

    monkeypatch.setattr(evaluate, "_sequence_nll", fake_nll)
    items = make_items()  # q1 -> Article 17, q2 -> Section 302, q3 -> abstain kind
    report = evaluate.run_condition_scoring("B", model=None, tok=None, store=make_store(),
                                             items=items, out_dir=tmp_path / "Bscore")

    answers = {a["id"]: a for a in evaluate.load_jsonl(tmp_path / "Bscore" / "answers.jsonl")}
    assert answers["q1"]["gold_selected"] is True
    assert answers["q2"]["gold_selected"] is True
    assert report["grounded_rate"] == 1.0
    assert report["gold_selected_overall"]["rate"] == pytest.approx(2 / 3)


def test_run_condition_scoring_C_gate_rejection_falls_back_to_pre_score(tmp_path, monkeypatch):
    fake_score_law_qa(monkeypatch)
    # Pre-adapt scoring always prefers the correct passage; post-adapt scoring
    # is corrupted (prefers abstain) -- the gate must reject on probe
    # regression and fall back to the pre-adapt (correct) choice.
    calls = {"n": 0}

    def fake_nll(model, tok, prompt, text):
        calls["n"] += 1
        # First 3 calls = pre-adapt scoring of [passage_candidate, abstain]
        # for the single q1 item; subsequent 3 = post-adapt.
        pre = calls["n"] <= 3
        if pre:
            return 0.1 if "Article 17" in text else 5.0
        return 5.0 if "Article 17" in text else 0.1  # post-adapt now prefers abstain

    monkeypatch.setattr(evaluate, "_sequence_nll", fake_nll)
    monkeypatch.setattr(evaluate, "_adapt", lambda *a, **k: 0.0)
    monkeypatch.setattr(evaluate, "_snapshot", lambda loras: "SNAP")
    monkeypatch.setattr(evaluate, "_restore", lambda loras, snap: None)
    monkeypatch.setattr(evaluate, "_merged_delta_norm", lambda loras: 0.01)

    probe = FakeProbe([1.0, 2.0])  # probe_before, probe_after -- 100% relative rise, rejected
    ledger = GateLedger(str(tmp_path / "ledger"))
    items = [make_items()[0]]  # q1 only
    report = evaluate.run_condition_scoring(
        "C", model=None, tok=None, store=make_store(), items=items, out_dir=tmp_path / "Cscore",
        loras=["fake-lora"], probe=probe, ledger=ledger,
    )

    answers = {a["id"]: a for a in evaluate.load_jsonl(tmp_path / "Cscore" / "answers.jsonl")}
    assert answers["q1"]["gold_selected"] is True  # fell back to the correct pre-adapt choice
    assert answers["q1"]["gate"]["accepted"] is False
    assert report["gate_summary"]["accepted"] == 0
