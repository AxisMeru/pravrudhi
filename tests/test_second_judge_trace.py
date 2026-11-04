"""The scoring trace (diagnostic only): what the judge's first token actually was, recorded so a second-judge disagreement
can be followed from prompt to score. It must never change a decision, and must never carry a credential or fact text."""

from __future__ import annotations

import hashlib
import json
import logging
import math

import pytest

from pravrudhi.application.nyaya_judges import (
    AndGateJudge,
    HouseJudge,
    build_house_prompt,
    first_token_digest,
)
from tests.test_nyaya_judges import REQ, _completion, _FakeComplete


def _sig(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def _house(text: str, top: dict[str, float], tau: float) -> tuple[HouseJudge, _FakeComplete]:
    fake = _FakeComplete(_completion(text, top))
    return HouseJudge(complete=fake, tau=tau, statute_chars=600), fake


def test_digest_reports_each_label_token_and_the_top_alternatives() -> None:
    d = first_token_digest({" not": -0.2, " established": -1.8, "Based": -4.0, "x": -9.0})
    assert d["labels"] == {" established": -1.8, "established": None, " not": -0.2, "not": None}
    assert d["top1"] == " not"
    assert [t for t, _ in d["top"]] == [" not", " established", "Based", "x"]
    assert d["k"] == 4


def test_house_trace_hashes_the_exact_prompt_and_keeps_the_label_logprobs() -> None:
    j, fake = _house(" not", {" not": -0.1, " established": -2.35}, tau=0.97)
    out = j.judge(REQ)
    assert out.status == "not_established"
    assert out.prompt_sha256 == hashlib.sha256(fake.prompts[0].encode()).hexdigest()
    assert fake.prompts[0] == build_house_prompt(REQ, statute_chars=600)
    assert out.first_token["labels"][" not"] == -0.1
    assert out.p_established == pytest.approx(_sig(-2.25))


def test_missing_established_token_is_visible_as_a_bound_in_the_trace() -> None:
    j, _ = _house(" not", {" not": -0.05, "No": -4.0, "None": -6.0}, tau=0.97)
    out = j.judge(REQ)
    assert out.clamp == "upper_bound"
    assert out.first_token["labels"][" established"] is None  # the reason it is a bound, now on the record


def _gate(second_top: dict[str, float], second_text: str = " not") -> AndGateJudge:
    primary, _ = _house(" established F1:0:10", {" established": -0.01, " not": -4.6}, tau=0.74)
    second, _ = _house(second_text, second_top, tau=0.97)
    return AndGateJudge(primary, second)


def test_and_gate_copies_the_second_judges_trace_without_changing_the_decision(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="pravrudhi.application.nyaya_judges")
    out = _gate({" not": -0.1, " established": -2.35}).judge(REQ)
    assert out.status == "not_established" and out.vetoed_by == "second"  # unchanged AND-gate behaviour
    t = out.second_trace
    assert t is not None
    assert t["raw"] == " not" and t["clamp"] == "none" and t["status"] == "not_established"
    assert t["first_token"]["top1"] == " not"
    assert t["p"] == pytest.approx(out.p_established_second)
    assert out.first_token["top1"] == " established"  # the primary's own trace stays the primary's
    line = next(r.getMessage() for r in caplog.records if "second_judge_trace" in r.getMessage())
    logged = json.loads(line.split(" ", 1)[1])
    assert logged["prompt_sha256"] == t["prompt_sha256"]
    for _fact_id, text in REQ.facts:  # no fact text in the log line
        assert text not in line


def test_trace_is_none_when_the_second_is_never_asked() -> None:
    primary, _ = _house(" not", {" not": -0.01, " established": -4.6}, tau=0.74)
    second, fake2 = _house(" established", {" established": -0.01, " not": -4.6}, tau=0.97)
    out = AndGateJudge(primary, second).judge(REQ)
    assert out.second_skip_reason == "primary_not_established"
    assert out.second_trace is None and fake2.prompts == []
