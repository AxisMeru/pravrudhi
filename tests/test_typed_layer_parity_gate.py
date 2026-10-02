"""Offline tests for the T1 parity gate logic (pravrudhi #196) over recorded completions; no network."""

from __future__ import annotations

import pytest

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


def test_gate_passes_on_agreeing_recorded_completions() -> None:
    rep = run_parity(_rows(RECORDED), _fetch_from(RECORDED))
    assert rep["gate"]["status"] == "pass", rep
    assert (rep["flips"], rep["n_both_error"], rep["n_compared"]) == ([], 1, 5)
    assert rep["max_abs_dp"] <= MAX_ABS_DP


def test_prose_top1_is_a_flip_the_gate_catches() -> None:
    """Production p_established_from_top_logprobs fail-closes when the top-1 token is prose or label mass is
    below the floor (2026-09-28); the typed path's score_decision has no such guard, so the two disagree: one
    raises, the other returns a number. The gate must report that as a flip, not skip it."""
    table = {"prose": ("Based on", {"Based": -0.1, " established": -2.5, " not": -2.6})}
    rep = run_parity(_rows(table), _fetch_from(table))
    assert rep["gate"]["status"] == "fail"
    assert len(rep["flips"]) == 1 and rep["flips"][0]["untyped"]["error"] == "JudgeOutputError"


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
