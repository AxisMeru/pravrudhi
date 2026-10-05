"""Offline tests for the T1 parity gate logic (pravrudhi #196) over recorded completions; no network."""

from __future__ import annotations

import pytest

from pravrudhi.application.nyaya_judges import ElementJudgment, JudgeOutputError
from pravrudhi.application.typed.parity import MAX_ABS_DP, ParityError, apply_template, run_parity
from pravrudhi.models.openai_compat import CompletionResult

# Recorded first-token top-k shapes (text, top logprobs): confident established, confident not, both labels,
# only ' established' present (lower bound), only ' not' present (upper bound), neither label (error on both).
RECORDED = {
    "est": ("established F1", {" established": -0.05, " not": -4.0}),
    "not": ("not", {" established": -3.0, " not": -0.05}),
    "near_tau": ("established F2", {" established": -0.6, " not": -1.4}),
    "lower": ("established F1", {" established": -0.2, "Based": -3.0}),
    "upper": ("not", {" not": -0.3, "Based": -2.0}),
    "none": ("?", {"unrelated": -0.1}),
}


def _fetch_from(table):
    def fetch(prompt: str) -> CompletionResult:
        text, top = table[prompt]
        return CompletionResult(text=text, model="m", top_logprobs=[top], wall_s=0.1)

    return fetch


def _rows(keys):
    return [{"id": k, "prompt": k} for k in keys]


AGREEING = [k for k in RECORDED if k != "none"]


def test_gate_passes_only_when_every_prompt_is_compared_and_agrees() -> None:
    rep = run_parity(_rows(AGREEING), _fetch_from(RECORDED))
    assert rep["gate"]["status"] == "pass", rep
    assert (rep["flips"], rep["n_both_error"], rep["n_compared"], rep["n_prompts"]) == ([], 0, 5, 5)
    assert rep["max_abs_dp"] <= MAX_ABS_DP


def test_one_both_error_row_fails_the_gate_even_with_every_other_row_agreeing() -> None:
    rep = run_parity(_rows(RECORDED), _fetch_from(RECORDED))
    assert (rep["flips"], rep["n_both_error"], rep["n_compared"]) == ([], 1, 5)
    assert rep["gate"]["status"] == "fail"
    assert any("no usable output on both judges" in f for f in rep["gate"]["failures"])
    assert any("only 5/6 prompts were compared" in f for f in rep["gate"]["failures"])


def test_278_rows_without_logprobs_and_one_clean_row_does_not_pass() -> None:
    """R2's reproduction (review 5411685321): the gate used to pass this vacuously, compared 1/279."""
    table = {**RECORDED, **{f"nolp{i}": ("?", {}) for i in range(278)}}

    def fetch(prompt: str) -> CompletionResult:
        text, top = table[prompt]
        return CompletionResult(text=text, model="m", top_logprobs=[top] if top else [], wall_s=0.1)

    rep = run_parity(_rows(["est", *[f"nolp{i}" for i in range(278)]]), fetch)
    assert (rep["n_prompts"], rep["n_compared"], rep["n_both_error"]) == (279, 1, 278)
    assert rep["flips"] == [] and rep["transport_errors"] == []
    assert rep["gate"]["status"] == "fail"
    assert any("278 prompt(s)" in f for f in rep["gate"]["failures"])
    assert any("only 1/279" in f for f in rep["gate"]["failures"])


class _Raises:
    def judge(self, request):
        raise JudgeOutputError("stub: fail-closed")


class _Returns:
    def judge(self, request):
        return ElementJudgment("established", 0.9, raw="established")


def test_one_judge_raising_while_the_other_returns_counts_as_a_flip() -> None:
    """Gate logic only, with stub judges (independent of the real typed path and of #177's merge state)."""
    rep = run_parity(_rows(["est"]), _fetch_from(RECORDED), judges=(_Raises(), _Returns()))
    assert rep["gate"]["status"] == "fail"
    assert len(rep["flips"]) == 1 and rep["flips"][0]["untyped"]["error"] == "JudgeOutputError"
    assert rep["flips"][0]["typed"]["error"] is None


def test_both_judges_raising_is_not_a_flip() -> None:
    rep = run_parity(_rows(["est", "not"]), _fetch_from(RECORDED), judges=(_Raises(), _Raises()))
    assert rep["flips"] == [] and rep["n_both_error"] == 2
    assert rep["gate"]["status"] == "fail"  # nothing compared, so the gate still refuses to pass


def test_transport_error_fails_the_gate_and_is_recorded() -> None:
    def fetch(prompt: str) -> CompletionResult:
        raise TimeoutError("boom")

    rep = run_parity(_rows(["est"]), fetch)
    assert rep["gate"]["status"] == "fail"
    assert rep["transport_errors"][0]["error"].startswith("TimeoutError")


def test_empty_row_set_does_not_pass() -> None:
    assert run_parity([], _fetch_from(RECORDED))["gate"]["status"] == "fail"


def test_template_wraps_the_prompt_and_is_validated() -> None:
    assert apply_template(None, "abc") == "abc"
    assert apply_template("PRE {prompt} POST", "abc") == "PRE abc POST"
    for bad in ("no placeholder", "{prompt} twice {prompt}"):
        with pytest.raises(ParityError):
            apply_template(bad, "abc")
    seen: list[str] = []

    def fetch(prompt: str) -> CompletionResult:
        seen.append(prompt)
        return _fetch_from(RECORDED)("est")

    run_parity(_rows(["est"]), fetch, template="T[{prompt}]")
    assert seen == ["T[est]"]
