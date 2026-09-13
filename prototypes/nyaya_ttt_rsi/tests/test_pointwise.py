"""Tests for evaluate.py's pointwise grounded-judgment mode (TRIZ
"segmentation"): one short single-passage prompt per retrieved passage,
scored against exactly two continuations (citation vs abstain), fused with
an optional BM25-rank prior, and calibrated on a train dev slice by reusing
loop.py's balanced-accuracy grid search. No torch, no /trackB mount -- every
model dependency goes through the lazy `evaluate._batch_mean_nll` hook,
monkeypatched here exactly like `_sequence_nll` is stubbed in
tests/test_evaluate.py.
"""

from __future__ import annotations

import json
import math

import pytest

from prototypes.nyaya_ttt_rsi import evaluate
from prototypes.nyaya_ttt_rsi.retrieval import ABSTAIN_PHRASE, Passage, PassageStore


def make_passages():
    return [
        Passage("Constitution of India", "Article 17", "Untouchability is abolished and its "
                "practice in any form is forbidden.", "Constitution of India, Article 17",
                "COI/17", title="Abolition of untouchability"),
        Passage("Indian Penal Code", "Section 302", "Whoever commits murder shall be punished "
                "with death or imprisonment for life.", "Indian Penal Code, Section 302",
                "IPC/302", title="Punishment for murder"),
        Passage("Indian Contract Act", "Section 10", "All agreements are contracts if made by "
                "free consent of parties competent to contract.", "Indian Contract Act, Section 10",
                "ICA/10", title="What agreements are contracts"),
    ]


def make_store():
    return PassageStore(make_passages())


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------


def test_build_pointwise_prompt_holds_exactly_one_passage():
    passages = make_passages()
    config = evaluate.PointwiseConfig(k=3)
    prompt, trunc = evaluate.build_pointwise_prompt("Who may commit murder?", passages[1], config)
    assert trunc.act == "Indian Penal Code"
    # exactly one passage block: instruction line, one passage line, question line
    assert prompt.count("[Indian Penal Code, Section 302]") == 1
    assert "[Constitution of India" not in prompt
    assert "[Indian Contract Act" not in prompt
    assert "Question: Who may commit murder?" in prompt


def test_build_pointwise_prompt_truncates_body_and_title_to_config_budgets():
    long_body = "x " * 500
    long_title = "y " * 200
    passage = Passage("Act", "Section 1", long_body, "Act, Section 1", None, title=long_title)
    config = evaluate.PointwiseConfig(k=1, body_max_bytes=40, title_max_bytes=20)
    _prompt, trunc = evaluate.build_pointwise_prompt("q?", passage, config)
    assert len(trunc.text.encode("utf-8")) <= 40
    assert len(trunc.title.encode("utf-8")) <= 20


# ---------------------------------------------------------------------------
# Budget assertion
# ---------------------------------------------------------------------------


def test_assert_pointwise_budget_passes_when_within_budget():
    total = evaluate.assert_pointwise_budget("short prompt", ["cite", "abstain phrase"], max_bytes=500)
    assert total == len("short prompt".encode("utf-8")) + len("abstain phrase".encode("utf-8"))


def test_assert_pointwise_budget_raises_when_over_budget():
    with pytest.raises(AssertionError):
        evaluate.assert_pointwise_budget("x" * 490, ["y" * 20, "abstain"], max_bytes=500)


def test_assert_pointwise_budget_uses_the_longer_continuation():
    # 480 + len(short)=5 fits in 500; 480 + len(long)=30 does not.
    with pytest.raises(AssertionError):
        evaluate.assert_pointwise_budget("x" * 480, ["short", "y" * 30], max_bytes=500)
    total = evaluate.assert_pointwise_budget("x" * 480, ["short"], max_bytes=500)
    assert total == 485


def test_score_pointwise_passages_enforces_budget_end_to_end():
    passage = Passage("Constitution of India", "Article 17", "x" * 400, "Constitution of India, Article 17",
                       None, title="")
    config = evaluate.PointwiseConfig(k=1, body_max_bytes=400, title_max_bytes=0, max_prompt_bytes=50)
    with pytest.raises(AssertionError):
        evaluate.score_pointwise_passages(model=None, tok=None, question="q?", passages=[passage],
                                           kind="law_citation_retrieval", config=config)


# ---------------------------------------------------------------------------
# Margin / fusion / selection
# ---------------------------------------------------------------------------


def _stub_batch_mean_nll(monkeypatch, nll_by_pair):
    """`nll_by_pair`: dict mapping continuation text -> mean NLL, applied
    regardless of which passage's prompt it appears with (tests discriminate
    on continuation text, matching the `_sequence_nll` stubbing convention
    used throughout tests/test_evaluate.py)."""

    def fake(model, tok, pairs):
        return [nll_by_pair[cont] for _prompt, cont in pairs]

    monkeypatch.setattr(evaluate, "_batch_mean_nll", fake)


def test_score_pointwise_passages_computes_margin_as_abstain_minus_cite(monkeypatch):
    passages = make_passages()[:2]
    _stub_batch_mean_nll(monkeypatch, {
        "Article 17 (Constitution of India).": 3.0,
        "Section 302 (Indian Penal Code).": 0.5,
        ABSTAIN_PHRASE: 1.0,
    })
    config = evaluate.PointwiseConfig(k=2)
    scored = evaluate.score_pointwise_passages(None, None, "murder?", passages, "law_citation_retrieval", config)
    assert len(scored) == 2
    assert scored[0]["rank"] == 1
    assert scored[1]["rank"] == 2
    # passage 0: cite nll 3.0, abstain nll 1.0 -> margin = 1.0 - 3.0 = -2.0 (abstain preferred)
    assert scored[0]["margin_mean"] == pytest.approx(-2.0)
    # passage 1: cite nll 0.5, abstain nll 1.0 -> margin = 1.0 - 0.5 = 0.5 (cite preferred)
    assert scored[1]["margin_mean"] == pytest.approx(0.5)
    assert scored[0]["passage"] is passages[0]
    assert scored[1]["passage"] is passages[1]


def test_score_pointwise_passages_empty_list_returns_empty():
    assert evaluate.score_pointwise_passages(None, None, "q?", [], "law_lookup",
                                              evaluate.PointwiseConfig(k=4)) == []


def test_rank_prior_is_zero_at_rank_one_and_decreasing():
    assert evaluate.rank_prior(1) == 0.0
    assert evaluate.rank_prior(2) < 0.0
    assert evaluate.rank_prior(8) < evaluate.rank_prior(2)
    with pytest.raises(ValueError):
        evaluate.rank_prior(0)


def _fake_scored(margins):
    return [{"passage": f"p{i}", "rank": i + 1, "margin_mean": m} for i, m in enumerate(margins)]


def test_select_pointwise_picks_max_margin_when_lambda_zero():
    scored = _fake_scored([0.1, 0.9, 0.4])
    sel = evaluate.select_pointwise(scored, lambda_prior=0.0)
    assert sel["best_idx"] == 1
    assert sel["best_score"] == pytest.approx(0.9)
    assert sel["gap"] == pytest.approx(0.9 - 0.4)


def test_select_pointwise_lambda_prior_can_favor_higher_bm25_rank():
    # rank-1 candidate has a slightly lower margin than rank-3; a large
    # enough lambda should tip the fused score back to rank-1.
    scored = _fake_scored([0.5, 0.3, 0.55])
    sel_no_prior = evaluate.select_pointwise(scored, lambda_prior=0.0)
    assert sel_no_prior["best_idx"] == 2  # rank 3 wins on margin alone
    sel_with_prior = evaluate.select_pointwise(scored, lambda_prior=1.0)
    assert sel_with_prior["best_idx"] == 0  # rank-1 prior term flips the pick


def test_select_pointwise_single_passage_has_infinite_gap():
    scored = _fake_scored([0.42])
    sel = evaluate.select_pointwise(scored, lambda_prior=0.0)
    assert sel["best_idx"] == 0
    assert sel["gap"] == float("inf")


def test_select_pointwise_empty_scored_list():
    sel = evaluate.select_pointwise([], lambda_prior=0.0)
    assert sel["best_idx"] is None
    assert sel["gap"] == float("inf")


def test_fuse_pointwise_scores_matches_margin_plus_scaled_prior():
    scored = _fake_scored([1.0, 2.0])
    fused = evaluate.fuse_pointwise_scores(scored, lambda_prior=2.0)
    assert fused[0] == pytest.approx(1.0 + 2.0 * evaluate.rank_prior(1))
    assert fused[1] == pytest.approx(2.0 + 2.0 * evaluate.rank_prior(2))


# ---------------------------------------------------------------------------
# Calibration (reuses loop.choose_calibration_thresholds / build_calibration_examples)
# ---------------------------------------------------------------------------


def _write_train_jsonl(path, rows):
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def test_calibrate_pointwise_separates_shown_from_miss(tmp_path, monkeypatch):
    # Build a tiny train split: citation-kind rows whose gold body is
    # constructed so a stubbed batch-NLL backend can cleanly discriminate
    # "gold shown" (should cite) from "gold excluded" (should abstain).
    # Each row's question embeds its own numeric index ("offence {i}"); a
    # stubbed backend uses that to decide, per SINGLE-passage pointwise
    # prompt, whether the one passage it holds is that question's true
    # gold (low cite NLL) or an unrelated distractor also retrieved into
    # the k-set (high cite NLL) -- this is the thing per-passage margin is
    # actually supposed to discriminate, unlike a same-passage self-check
    # (every pointwise prompt trivially contains its own passage's text).
    rows = []
    for i in range(20):
        rows.append({
            "id": f"c{i}", "kind": "law_citation_retrieval", "act": "Indian Penal Code",
            "section": f"Section {100 + i}", "prompt": f"question about offence {i}",
            "target": "t", "source_id": f"c{i}",
        })
    train = tmp_path / "train.jsonl"
    _write_train_jsonl(train, rows)

    passages = [
        Passage("Indian Penal Code", f"Section {100 + i}", f"Provision body number {i}.",
                f"Indian Penal Code, Section {100 + i}", f"c{i}", title=f"Heading {i}")
        for i in range(20)
    ]
    store = PassageStore(passages)

    import re

    def fake_batch(model, tok, pairs):
        out = []
        for prompt, cont in pairs:
            if cont == ABSTAIN_PHRASE:
                out.append(2.0)
                continue
            qmatch = re.search(r"offence (\d+)", prompt)
            cmatch = re.search(r"Section (\d+)", cont)
            q_idx = qmatch.group(1) if qmatch else None
            c_idx = str(int(cmatch.group(1)) - 100) if cmatch else None
            out.append(0.5 if q_idx == c_idx else 5.0)  # low nll (confident) only for the true gold
        return out

    monkeypatch.setattr(evaluate, "_batch_mean_nll", fake_batch)

    result = evaluate.calibrate_pointwise(model=None, tok=None, store=store, train_path=train,
                                           k=4, n=20, seed=1, frac_miss=0.3,
                                           lambda_grid=(0.0, 0.5))
    assert result["k"] == 4
    assert 0.0 <= result["balanced_accuracy"] <= 1.0
    assert result["lambda_prior"] in (0.0, 0.5)
    assert "tau_m" in result and "delta_m" in result
    assert result["n_examples"] > 0
    assert result["confusion"]["n_shown"] + result["confusion"]["n_miss"] == result["n_examples"]


# ---------------------------------------------------------------------------
# run_condition_pointwise (end to end, stubbed backend)
# ---------------------------------------------------------------------------


def fake_score_law_qa(monkeypatch):
    monkeypatch.setattr(evaluate, "_score_law_qa", lambda heldout_path, answers_path: {"stub": True})


def test_run_condition_pointwise_abstains_below_threshold(tmp_path, monkeypatch):
    store = make_store()
    fake_score_law_qa(monkeypatch)
    _stub_batch_mean_nll(monkeypatch, {
        "Article 17 (Constitution of India).": 5.0,
        "Section 302 (Indian Penal Code).": 5.0,
        "Section 10 (Indian Contract Act).": 5.0,
        ABSTAIN_PHRASE: 1.0,
    })
    items = [{"id": "q1", "kind": "law_citation_retrieval", "prompt": "untouchability",
              "act": "Constitution of India", "section": "Article 17"}]
    config = evaluate.PointwiseConfig(k=3)
    report = evaluate.run_condition_pointwise(
        None, None, store, items, tmp_path / "out",
        config=config, tau_m=100.0, delta_m=0.0,  # impossibly high tau_m -> always abstain
    )
    answers = evaluate.load_jsonl(tmp_path / "out" / "answers.jsonl")
    assert answers[0]["abstained"] is True
    assert answers[0]["answer"] == ABSTAIN_PHRASE
    assert report["mode"] == "pointwise"
    assert report["calibration"]["tau_m"] == 100.0


def test_run_condition_pointwise_cites_gold_when_confident(tmp_path, monkeypatch):
    store = make_store()
    fake_score_law_qa(monkeypatch)
    _stub_batch_mean_nll(monkeypatch, {
        "Article 17 (Constitution of India).": 0.1,
        "Section 302 (Indian Penal Code).": 5.0,
        "Section 10 (Indian Contract Act).": 5.0,
        ABSTAIN_PHRASE: 3.0,
    })
    items = [{"id": "q1", "kind": "law_citation_retrieval", "prompt": "untouchability",
              "act": "Constitution of India", "section": "Article 17"}]
    config = evaluate.PointwiseConfig(k=3)
    report = evaluate.run_condition_pointwise(
        None, None, store, items, tmp_path / "out",
        config=config, tau_m=-10.0, delta_m=-10.0, lambda_prior=0.0,
    )
    answers = evaluate.load_jsonl(tmp_path / "out" / "answers.jsonl")
    assert answers[0]["abstained"] is False
    assert answers[0]["gold_selected"] is True
    assert answers[0]["answer"] == "Article 17 (Constitution of India)."
    assert report["gold_selected_overall"]["rate"] == 1.0
    assert report["pointwise_config"]["k"] == 3


def test_run_condition_pointwise_handles_no_retrieved_passages(tmp_path, monkeypatch):
    store = PassageStore([])
    fake_score_law_qa(monkeypatch)
    monkeypatch.setattr(evaluate, "_batch_mean_nll", lambda model, tok, pairs: [])
    items = [{"id": "q1", "kind": "law_citation_retrieval", "prompt": "anything",
              "act": "X", "section": "Y"}]
    report = evaluate.run_condition_pointwise(
        None, None, store, items, tmp_path / "out",
        config=evaluate.PointwiseConfig(k=4), tau_m=0.0, delta_m=0.0,
    )
    answers = evaluate.load_jsonl(tmp_path / "out" / "answers.jsonl")
    assert answers[0]["abstained"] is True
    assert report["n_items"] == 1


# ---------------------------------------------------------------------------
# Scoring mode + rank prior + selection-correctness calibration
# (2026-09-13 correction: degenerate gold_shown calibration under high
# retrieval recall)
# ---------------------------------------------------------------------------


def test_fuse_scoring_with_rank_prior_matches_neg_nll_plus_scaled_prior():
    nlls = [1.0, 2.0, 0.5]
    fused = evaluate.fuse_scoring_with_rank_prior(nlls, lambda_prior=2.0)
    assert fused[0] == pytest.approx(-1.0 + 2.0 * evaluate.rank_prior(1))
    assert fused[1] == pytest.approx(-2.0 + 2.0 * evaluate.rank_prior(2))
    assert fused[2] == pytest.approx(-0.5 + 2.0 * evaluate.rank_prior(3))


def test_select_scoring_with_rank_prior_picks_lowest_nll_when_lambda_zero():
    sel = evaluate.select_scoring_with_rank_prior([3.0, 0.2, 1.0], lambda_prior=0.0)
    assert sel["best_idx"] == 1
    assert sel["gap"] == pytest.approx((-0.2) - (-1.0))


def test_select_scoring_with_rank_prior_empty_list():
    sel = evaluate.select_scoring_with_rank_prior([], lambda_prior=0.0)
    assert sel["best_idx"] is None
    assert sel["gap"] == float("inf")


def test_pointwise_selection_correct_true_when_argmax_is_gold():
    scored = _fake_scored([0.1, 0.9, 0.4])  # index 1 wins on margin
    scored[1]["passage"] = Passage("Act", "Section 2", "body", "Act, Section 2", None)
    assert evaluate.pointwise_selection_correct(scored, "Act", "Section 2", lambda_prior=0.0) is True


def test_pointwise_selection_correct_false_when_argmax_is_not_gold():
    # The passage with the best margin (index 1) is a wrong distractor;
    # gold (index 2) has a WORSE margin, so a plain gold_shown label would
    # be positive (gold IS among the shown passages) while the corrected
    # selection_correct label must be negative (the argmax picked the
    # distractor, not gold) -- this is exactly the distinction the
    # 2026-09-13 degenerate-calibration fix depends on.
    scored = _fake_scored([0.1, 0.9, 0.4])
    scored[1]["passage"] = Passage("Act", "Section 999", "distractor", "Act, Section 999", None)
    scored[2]["passage"] = Passage("Act", "Section 2", "gold body", "Act, Section 2", None)
    assert evaluate.pointwise_selection_correct(scored, "Act", "Section 2", lambda_prior=0.0) is False


def test_pointwise_selection_correct_false_when_scored_empty():
    assert evaluate.pointwise_selection_correct([], "Act", "Section 1", lambda_prior=0.0) is False


def test_calibrate_pointwise_selection_correct_vs_legacy_gold_shown_label(monkeypatch, tmp_path):
    # Gold IS always shown (frac_miss=0), but the stub always makes a
    # DIFFERENT, fixed real passage (Section 105) look more confident than
    # any other -- so whenever a row's own gold is not Section 105, the
    # argmax picks the wrong passage. Under the legacy "gold_shown" label
    # every example is labeled positive (gold IS among the shown passages),
    # collapsing the negative class entirely (n_miss=0); under
    # "selection_correct" most examples are correctly labeled negative
    # (the argmax did not land on their own gold).
    # "codeword" is shared by every passage body and every question, so
    # plain BM25 (no forced exclusion needed) already retrieves all 10
    # passages for every query -- otherwise, with no shared vocabulary,
    # `store.search` would only ever surface the forced-in gold passage
    # itself (score 0 for everything else), making every dev example
    # trivially "correct" regardless of the label under test.
    rows = []
    for i in range(10):
        rows.append({
            "id": f"c{i}", "kind": "law_citation_retrieval", "act": "Indian Penal Code",
            "section": f"Section {100 + i}", "prompt": f"question codeword about offence {i}",
            "target": "t", "source_id": f"c{i}",
        })
    train = tmp_path / "train.jsonl"
    _write_train_jsonl(train, rows)
    passages = [
        Passage("Indian Penal Code", f"Section {100 + i}", f"Provision codeword body number {i}.",
                f"Indian Penal Code, Section {100 + i}", f"c{i}", title=f"Heading {i}")
        for i in range(10)
    ]
    store = PassageStore(passages)

    def fake_batch(model, tok, pairs):
        out = []
        for prompt, cont in pairs:
            if cont == ABSTAIN_PHRASE:
                out.append(2.0)
            elif "Section 105" in cont:  # one fixed, always-irresistible passage
                out.append(0.1)
            else:
                out.append(1.5)
        return out

    monkeypatch.setattr(evaluate, "_batch_mean_nll", fake_batch)

    result_correct = evaluate.calibrate_pointwise(model=None, tok=None, store=store, train_path=train,
                                                   k=10, n=10, seed=1, frac_miss=0.0,
                                                   lambda_grid=(0.0,), label_mode="selection_correct")
    result_legacy = evaluate.calibrate_pointwise(model=None, tok=None, store=store, train_path=train,
                                                  k=10, n=10, seed=1, frac_miss=0.0,
                                                  lambda_grid=(0.0,), label_mode="gold_shown")
    # Legacy label: every example is "gold_shown" (frac_miss=0, k=10 covers
    # the whole store) so there is no negative class at all.
    assert result_legacy["confusion"]["n_miss"] == 0
    # Corrected label: only the row whose own gold IS Section 105 is a
    # correct pick; every other row's argmax lands on the wrong passage.
    assert result_correct["confusion"]["n_shown"] == 1
    assert result_correct["confusion"]["n_miss"] == 9
    assert result_correct["label_mode"] == "selection_correct"


def test_calibrate_scoring_with_rank_prior_end_to_end(tmp_path, monkeypatch):
    rows = []
    for i in range(15):
        rows.append({
            "id": f"c{i}", "kind": "law_citation_retrieval", "act": "Indian Penal Code",
            "section": f"Section {100 + i}", "prompt": f"question about offence {i}",
            "target": "t", "source_id": f"c{i}",
        })
    train = tmp_path / "train.jsonl"
    _write_train_jsonl(train, rows)
    passages = [
        Passage("Indian Penal Code", f"Section {100 + i}", f"Provision body number {i}.",
                f"Indian Penal Code, Section {100 + i}", f"c{i}", title=f"Heading {i}")
        for i in range(15)
    ]
    store = PassageStore(passages)

    import re

    def fake_nll(model, tok, prompt, text):
        qmatch = re.search(r"offence (\d+)", prompt)
        cmatch = re.search(r"Section (\d+)", text)
        q_idx = qmatch.group(1) if qmatch else None
        c_idx = str(int(cmatch.group(1)) - 100) if cmatch else None
        return 0.5 if q_idx == c_idx else 5.0

    monkeypatch.setattr(evaluate, "_sequence_nll", fake_nll)

    result = evaluate.calibrate_scoring_with_rank_prior(model=None, tok=None, store=store, train_path=train,
                                                         k=4, n=15, seed=1, lambda_grid=(0.0, 0.5))
    assert result["k"] == 4
    assert result["label_mode"] == "selection_correct"
    assert 0.0 <= result["balanced_accuracy"] <= 1.0
    assert len(result["lambda_grid"]) == 2
    assert result["n_examples"] > 0
