"""#738: the canonical prompt layout behind a flag that defaults OFF.

Frozen build_prompt (research val_threshold_analysis.py, copied below): the narrative sits in `Scenario:`, the element
facts (never the narrative row) in `Available facts:`. The engine's production request today leaves `Scenario:` empty.
The flag (config `judge_prompt.canonical_layout`, env `NYAYA_JUDGE_CANONICAL_LAYOUT`, default OFF) derives the narrative
when the caller sent none: (1) request.narrative verbatim; (2) else the `F_narrative` fact row; (3) else the FIRST SENTENCE
of F1 capped at 401 characters (NARRATIVE-DEFINITION-738-ADDENDUM, primary N1), the facts left untouched.
Nothing here is evidence about coverage."""

from __future__ import annotations

import json
import os
import random
from pathlib import Path
from typing import Any

import pytest

from pravrudhi.application.nyaya_judges import (
    AndGateJudge,
    HouseJudge,
    JudgeRequest,
    build_house_prompt,
    canonical_narrative,
)
from pravrudhi.models.openai_compat import CompletionResult

REPO = Path(__file__).resolve().parent.parent


# -- the references: copies, so the engine can never silently redefine them ----------------------------------------------------
def frozen_build_prompt(statute: str, element: str, facts: list[dict[str, str]]) -> str:
    """The frozen scorer's `build_prompt`, verbatim in effect (val_threshold_analysis.py:61-71)."""
    statute = statute[:600]
    narrative = next((f["text"] for f in facts if f["id"] == "F_narrative"), "")
    facts_block = "\n".join(f"[{f['id']}] {f['text']}" for f in facts if f["id"] != "F_narrative")
    return f"Statute: {statute}\nScenario: {narrative}\nElement to judge: {element}\nAvailable facts:\n{facts_block}\nAnswer:"


def reference_production_prompt(request: JudgeRequest, *, statute_chars: int, standard_line: str = "") -> str:
    """build_house_prompt as it was on main before the flag (the legacy template), copied."""
    facts_block = "\n".join(f"[{fid}] {text}" for fid, text in request.facts if fid != "F_narrative")
    return (
        f"Statute: {request.statute[:statute_chars]}\nScenario: {request.narrative}\nElement to judge: {request.element}\n"
        f"{standard_line}Available facts:\n{facts_block}\nAnswer:"
    )


def req(
    facts: list[tuple[str, str]], narrative: str = "", statute: str = "S" * 900, element: str = "the accused did X"
) -> JudgeRequest:
    return JudgeRequest("c1", element, False, statute, narrative, tuple(facts))


def _random_requests(n: int = 60) -> list[JudgeRequest]:
    rnd = random.Random(738)
    words = ["the", "accused", "dishonestly", "induced", "complainant", "to", "deliver", "property", ".", "Then", "he", "left",
             "?", "Yes", "!", "12", "Sec.", "420"]
    out = []
    for _ in range(n):
        facts = [(f"F{i + 1}", " ".join(rnd.choice(words) for _ in range(rnd.randint(1, 40)))) for i in range(rnd.randint(1, 5))]
        if rnd.random() < 0.4:
            row = " ".join(rnd.choice(words) for _ in range(rnd.randint(1, 30)))
            facts.insert(rnd.randint(0, len(facts)), ("F_narrative", row))
        out.append(req(facts, narrative=rnd.choice(["", "", "A supplied narrative."]), statute="x" * rnd.randint(0, 1500)))
    return out


# -- flag OFF: byte-identical to today ---------------------------------------------------------------------------------------
def test_flag_off_prompts_are_byte_identical_to_the_pre_flag_function() -> None:
    for r in _random_requests():
        assert build_house_prompt(r, statute_chars=600) == reference_production_prompt(r, statute_chars=600)
        off = build_house_prompt(r, statute_chars=600, canonical_layout=False)
        assert off == reference_production_prompt(r, statute_chars=600)


def test_the_judge_default_is_off_and_produces_the_same_prompt() -> None:
    seen: list[str] = []

    def complete(prompt: str) -> CompletionResult:
        seen.append(prompt)
        top = [{" established": -3.0, " not": -0.05}]
        return CompletionResult(text=" not", model="m", top_logprobs=top, wall_s=0.01, finish_reason="stop")

    j = HouseJudge(complete=complete, tau=0.74, statute_chars=600)
    assert j.canonical_layout is False and j.layout == "production"
    r = req([("F1", "Arun beat Bela. He did it often."), ("F2", "Bela left.")])
    j.judge(r)
    assert seen == [reference_production_prompt(r, statute_chars=600)]


# -- flag ON ----------------------------------------------------------------------------------------------------------------
def test_flag_on_with_a_supplied_narrative_is_verbatim_and_equals_off() -> None:
    for r in _random_requests():
        if r.narrative:
            assert build_house_prompt(r, statute_chars=600, canonical_layout=True) == build_house_prompt(r, statute_chars=600)


def test_flag_on_uses_the_f_narrative_row_when_no_narrative_was_supplied_and_equals_the_frozen_prompt() -> None:
    rnd = random.Random(31)
    for k in range(31):
        facts = [{"id": "F_narrative", "text": f"Scene {k}. Vikram met Sunita at a depot."}] + [
            {"id": f"F_el{i}", "text": f"Fact {k}.{i} Sunita received {rnd.randint(1, 9)} pumps."}
            for i in range(rnd.randint(1, 3))
        ]
        statute, element = "Whoever dishonestly misappropriates property " * 20, f"element {k}"
        r = req([(f["id"], f["text"]) for f in facts], narrative="", statute=statute, element=element)
        assert build_house_prompt(r, statute_chars=600, canonical_layout=True) == frozen_build_prompt(statute, element, facts)
        assert "Scenario: \n" in build_house_prompt(r, statute_chars=600)   # flag OFF keeps the production layout (the harm)


@pytest.mark.skipif(
    not os.environ.get("NYAYA_REGRESSION_ROWS"),
    reason="the 31 #726 regression rows are local research data (set NYAYA_REGRESSION_ROWS to layout726_jobs.json)",
)
def test_flag_on_equals_the_frozen_prompt_for_the_31_regression_rows() -> None:
    jobs = json.loads(Path(os.environ["NYAYA_REGRESSION_ROWS"]).read_text())
    canon = [j for j in jobs if j["layout"] == "canonical"]
    assert len({j["item_id"] for j in canon}) == 31 and len(canon) == 93
    for j in canon:
        facts = [{"id": fid, "text": t} for fid, t in j["facts"]]
        r = req([(fid, t) for fid, t in j["facts"]], narrative="", statute=j["statute"], element=j["element_text"])
        on = build_house_prompt(r, statute_chars=600, canonical_layout=True)
        assert on == frozen_build_prompt(j["statute"], j["element_text"], facts)


# -- N1: first sentence of F1, capped, F1 kept -------------------------------------------------------------------------------
def test_n1_is_the_first_sentence_of_f1_and_f1_stays_in_the_facts() -> None:
    r = req([("F1", "The accused took the cheque. He never repaid it."), ("F2", "A notice was sent.")])
    assert canonical_narrative(r) == "The accused took the cheque."
    prompt = build_house_prompt(r, statute_chars=600, canonical_layout=True)
    assert "Scenario: The accused took the cheque.\n" in prompt
    assert "[F1] The accused took the cheque. He never repaid it.\n[F2] A notice was sent.\n" in prompt   # facts untouched


@pytest.mark.parametrize("text,want", [
    ("No sentence end here", "No sentence end here"),
    ("Stops with a question? Then more.", "Stops with a question?"),
    ("Exclaims! Then more.", "Exclaims!"),
    ("Sec. 420 was invoked. Later.", "Sec."),                      # the registered regex splits here: a known, registered limit
    ("   padded start.  Then more.  ", "padded start."),
    ("", ""),
])
def test_n1_sentence_rule(text: str, want: str) -> None:
    facts = [("F1", text)] if text.strip() else [("F1", "x")]
    got = canonical_narrative(req(facts))
    assert got == (want if text.strip() else "x")


def test_n1_is_capped_at_401_characters_on_a_word_boundary() -> None:
    long_sentence = " ".join(["word"] * 200) + "."
    got = canonical_narrative(req([("F1", long_sentence)]))
    assert len(got) <= 401 and not got.endswith(" ") and long_sentence.startswith(got) and len(got) > 380
    assert len(canonical_narrative(req([("F1", "a" * 401 + ".")]))) <= 401


def test_a_leading_f_narrative_row_is_not_the_first_fact() -> None:
    r = req([("F_narrative", "Narrative row. More."), ("F1", "First fact.")])
    assert canonical_narrative(r) == "Narrative row. More."   # rule 2 wins; the row stays out of the facts block
    assert "[F_narrative]" not in build_house_prompt(r, statute_chars=600, canonical_layout=True)


def test_no_facts_leaves_scenario_empty() -> None:
    assert canonical_narrative(req([])) == ""


# -- both judges, config and audit -------------------------------------------------------------------------------------------
def _fake(prompts: list[str]) -> Any:
    def complete(prompt: str) -> CompletionResult:
        prompts.append(prompt)
        top = [{" established": -0.01, " not": -5.0}]
        return CompletionResult(text=" established F1:0:5", model="m", top_logprobs=top, wall_s=0.01, finish_reason="stop")

    return complete


def test_the_second_judge_request_uses_the_same_function() -> None:
    p1: list[str] = []
    p2: list[str] = []
    primary = HouseJudge(complete=_fake(p1), tau=0.74, statute_chars=600, canonical_layout=True)
    second = HouseJudge(complete=_fake(p2), tau=0.97, statute_chars=600, canonical_layout=True)
    gate = AndGateJudge(primary, second, tau_primary=0.74, tau_second=0.97)
    r = req([("F1", "Arun beat Bela. Often."), ("F2", "Bela left.")])
    gate.judge(r)
    assert p1 == p2 == [build_house_prompt(r, statute_chars=600, canonical_layout=True)]
    assert primary.layout == second.layout == gate.layout == "canonical"


def test_the_and_gate_reports_a_mixed_layout() -> None:
    a = HouseJudge(complete=_fake([]), tau=0.74, statute_chars=600, canonical_layout=True)
    b = HouseJudge(complete=_fake([]), tau=0.97, statute_chars=600)
    assert AndGateJudge(a, b, tau_primary=0.74, tau_second=0.97).layout == "mixed"


def test_from_config_reads_the_flag() -> None:
    cfg = {"statute_chars": 600, "base_url": "http://127.0.0.1:8110/v1", "model": "m", "max_tokens": 30, "top_logprobs": 20,
           "timeout_s": 60, "label_mass_floor": 0.5}
    assert HouseJudge.from_config(cfg, tau=0.5).canonical_layout is False
    assert HouseJudge.from_config(cfg, tau=0.5, canonical_layout=True).canonical_layout is True
    assert HouseJudge.from_config_with_fallback(cfg, tau=0.5, canonical_layout=True).canonical_layout is True


def test_the_shipped_config_defaults_the_flag_off(tmp_path: Path) -> None:
    from pravrudhi.application.nyaya_agent import load_agent_config

    assert load_agent_config(REPO).canonical_layout is False


def test_config_and_env_turn_it_on(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from pravrudhi.application.nyaya_agent import load_agent_config

    (tmp_path / "configs").mkdir()
    base = "refer_band: [0.5, 0.74]\nmax_retries: 2\naudit_dir: audit\ntau: 0.74\n"
    (tmp_path / "configs" / "nyaya_agent.yaml").write_text(base + "judge_prompt: {canonical_layout: true}\n")
    assert load_agent_config(tmp_path).canonical_layout is True
    (tmp_path / "configs" / "nyaya_agent.yaml").write_text(base)
    assert load_agent_config(tmp_path).canonical_layout is False
    monkeypatch.setenv("NYAYA_JUDGE_CANONICAL_LAYOUT", "1")
    assert load_agent_config(tmp_path).canonical_layout is True
    monkeypatch.setenv("NYAYA_JUDGE_CANONICAL_LAYOUT", "0")
    assert load_agent_config(tmp_path).canonical_layout is False



_HJ = {"base_url": "http://h/v1", "model": "m", "statute_chars": 600, "max_tokens": 30, "top_logprobs": 20, "timeout_s": 5,
       "label_mass_floor": 0.5}


@pytest.mark.parametrize("on", [False, True])
def test_the_flag_reaches_both_judges_through_nyaya_agent_house(tmp_path: Path, on: bool) -> None:
    from pravrudhi.application.nyaya_agent import AgentConfig, NyayaAgent

    score = tmp_path / "score"
    score.write_bytes(b"fake binary")
    cfg = AgentConfig(
        tau=0.74, refer_band=(0.5, 0.74), max_retries=1, audit_dir=tmp_path / "audit", house_judge=_HJ,
        second_judge={**_HJ, "base_url": "http://s/v1", "model": "m2", "tau": 0.97}, score_bin=score,
        pinned_score_sha256=None, canonical_layout=on,
    )
    agent = NyayaAgent.house(tmp_path, config=cfg)
    want = "canonical" if on else "production"
    assert agent.judge.primary.layout == agent.judge.second.layout == agent.judge.layout == want
