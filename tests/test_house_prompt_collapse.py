"""#369 / #815: serve-time fact collapse in the house prompt, behind `collapse_facts` (default OFF).

Toy text only. OFF leaves the legacy prompt byte-identical; ON collapses ONLY the fact texts in
`Available facts:` (Statute and Scenario are never touched), as Track A's training-side `fact_rep.collapse`.
"""

from __future__ import annotations

import pytest

from pravrudhi.application.nyaya_judges import (
    CompletionResult,
    HouseJudge,
    JudgeRequest,
    build_house_prompt,
    collapse_fact_text,
)

RAW = "line one\n  indented line\twith tab\x0cform feed   and   spaces\n"
COLLAPSED = "line one indented line with tab form feed and spaces"


def _req(facts=(("F1", RAW), ("F2", "plain fact."))) -> JudgeRequest:
    return JudgeRequest("c1", "el one", False, "TOY statute\ntext.", "TOY narrative\nwith break.", facts)


def test_off_is_byte_identical_to_the_legacy_prompt() -> None:
    legacy = (
        "Statute: TOY statute\ntext.\nScenario: TOY narrative\nwith break.\nElement to judge: el one\n"
        f"Available facts:\n[F1] {RAW}\n[F2] plain fact.\nAnswer:"
    )
    assert build_house_prompt(_req(), statute_chars=600) == legacy
    assert build_house_prompt(_req(), statute_chars=600, collapse_facts=False) == legacy


def test_on_collapses_only_the_fact_texts() -> None:
    on = build_house_prompt(_req(), statute_chars=600, collapse_facts=True)
    assert on == (
        "Statute: TOY statute\ntext.\nScenario: TOY narrative\nwith break.\nElement to judge: el one\n"
        f"Available facts:\n[F1] {COLLAPSED}\n[F2] plain fact.\nAnswer:"
    )


def test_collapse_matches_the_training_side_representation_and_is_idempotent() -> None:
    assert collapse_fact_text(RAW) == COLLAPSED == " ".join(RAW.split())
    assert collapse_fact_text(COLLAPSED) == COLLAPSED and collapse_fact_text("") == ""


def test_the_narrative_row_is_still_excluded_and_collapse_does_not_change_ids_or_order() -> None:
    facts = (("F_narrative", "background\n text"), ("F1", RAW), ("F2", "x"))
    on = build_house_prompt(_req(facts), statute_chars=600, collapse_facts=True)
    assert "F_narrative" not in on and on.index("[F1]") < on.index("[F2]")


def test_the_quote_claim_still_uses_the_raw_fact_text() -> None:
    """The model sees collapsed text but answers a fact id; the request keeps the RAW fact text."""
    captured = {}

    j = HouseJudge.__new__(HouseJudge)
    j.statute_chars, j.prompt_template, j.collapse_facts, j.max_input_chars = 600, "legacy", True, None

    def fake_complete(prompt):
        captured["prompt"] = prompt
        raise RuntimeError("stop after the prompt is built")

    j._complete = fake_complete
    with pytest.raises(RuntimeError):
        j.judge(_req())
    assert f"[F1] {COLLAPSED}\n" in captured["prompt"] and RAW not in captured["prompt"]
    assert dict(_req().facts)["F1"] == RAW


def test_collapse_facts_must_be_a_bool_and_defaults_off() -> None:
    with pytest.raises(ValueError):
        HouseJudge(tau=0.7, statute_chars=600, base_url="http://x", complete=lambda p: None, collapse_facts="yes")  # type: ignore[arg-type]
    assert HouseJudge(tau=0.7, statute_chars=600, base_url="http://x", complete=lambda p: None).collapse_facts is False


def test_with_collapse_on_the_established_quote_is_the_raw_fact_and_whole_fact() -> None:
    """The model sees the collapsed fact and answers a fact id; the judgment's quote is the RAW fact text."""
    seen: list[str] = []

    def complete(prompt: str) -> CompletionResult:
        seen.append(prompt)
        top = [{" established": -0.05, " not": -3.0}]
        return CompletionResult(text=" established F1:0:51", model="m", top_logprobs=top, wall_s=0.0, finish_reason="stop")

    j = HouseJudge(complete=complete, tau=0.74, statute_chars=600, collapse_facts=True).judge(_req())
    assert j.status == "established" and j.fact_id == "F1"
    assert j.quote == RAW and j.quote_source == "whole_fact"
    assert f"[F1] {COLLAPSED}\n" in seen[0] and RAW not in seen[0]


def test_the_input_length_check_runs_on_the_collapsed_prompt() -> None:
    """The one non-re-spelling change: a request over max_input_chars on RAW text but within it COLLAPSED is judged."""
    from pravrudhi.application.nyaya_judges import JudgeInputTooLong

    raw_len = len(build_house_prompt(_req(), statute_chars=600))
    col_len = len(build_house_prompt(_req(), statute_chars=600, collapse_facts=True))
    assert col_len < raw_len
    limit = col_len  # fits collapsed, not raw

    def complete(prompt: str) -> CompletionResult:
        top = [{" not": -0.05, " established": -3.0}]
        return CompletionResult(text=" not", model="m", top_logprobs=top, wall_s=0.0, finish_reason="stop")

    off = HouseJudge(complete=complete, tau=0.74, statute_chars=600, max_input_chars=limit)
    on = HouseJudge(complete=complete, tau=0.74, statute_chars=600, max_input_chars=limit, collapse_facts=True)
    with pytest.raises(JudgeInputTooLong):
        off.judge(_req())
    assert on.judge(_req()).status == "not_established"
