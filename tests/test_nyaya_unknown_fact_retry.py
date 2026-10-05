"""A deterministic judge (temperature 0, same request) cannot answer differently on a retry, so an unresolvable
fact id (e.g. the compound "F1.2") must not burn `max_retries` identical re-asks. It still fails closed: the
element is downgraded and no nearest-match fact is ever substituted. A non-deterministic judge keeps retrying."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pravrudhi.application.nyaya_agent import NyayaAgent
from pravrudhi.application.nyaya_judges import ElementJudgment, HouseJudge, JudgeRequest, parse_house_fact_id
from tests.test_nyaya_agent_concurrency import CONTRACT, ELEMENTS, FACTS, FakeRegistry, _config


@dataclass
class CountingJudge:
    fact_id: str
    deterministic: bool
    name: str = "counting"
    calls: int = 0

    def judge(self, request: JudgeRequest) -> ElementJudgment:
        self.calls += 1
        return ElementJudgment("established", 0.99, self.fact_id, None, None)


def _run(tmp_path: Path, judge: CountingJudge):  # type: ignore[no-untyped-def]
    agent = NyayaAgent(judge, FakeRegistry(CONTRACT), _config(tmp_path, 1, max_retries=2))
    return agent.run(FACTS, narrative="", contract_ids=[CONTRACT.contract_id])


def test_deterministic_judge_is_not_retried_on_an_unknown_fact_id(tmp_path: Path) -> None:
    judge = CountingJudge("F1.2", deterministic=True)
    run = _run(tmp_path, judge)
    assert judge.calls == len(ELEMENTS)
    el = run.contracts[0].elements[0]
    assert el.status == "not_established" and el.fact_id == "F1.2" and el.attempts == 1
    assert el.quote_check == "unknown_fact"


def test_nondeterministic_judge_still_retries(tmp_path: Path) -> None:
    judge = CountingJudge("F1.2", deterministic=False)
    _run(tmp_path, judge)
    assert judge.calls == len(ELEMENTS) * 3


def test_house_judge_is_declared_deterministic_and_never_resolves_a_compound_id() -> None:
    assert HouseJudge.deterministic is True
    assert parse_house_fact_id("established F1.2") == "F1.2"


class _Gate1Model:
    def score(self, *a, **k):  # type: ignore[no-untyped-def]
        raise AssertionError("gate1 must not run on an unresolved element")


def test_wrappers_propagate_the_flag() -> None:
    from pravrudhi.application.nyaya_judges import AndGateJudge, Gate1Judge
    from pravrudhi.application.typed.house_judge import TypedHouseJudge

    det, non = CountingJudge("F1", True), CountingJudge("F1", False)
    assert AndGateJudge(det, non).deterministic is True
    assert AndGateJudge(non, det).deterministic is False
    assert Gate1Judge(det, _Gate1Model()).deterministic is True  # type: ignore[arg-type]
    assert Gate1Judge(non, _Gate1Model()).deterministic is False  # type: ignore[arg-type]
    assert Gate1Judge(AndGateJudge(det, non), _Gate1Model()).deterministic is True  # type: ignore[arg-type]
    assert TypedHouseJudge.deterministic is True


def test_and_gate_is_not_retried_so_second_is_asked_once_not_per_retry(tmp_path: Path) -> None:
    from pravrudhi.application.nyaya_judges import AndGateJudge

    primary, second = CountingJudge("F1.2", True, "p"), CountingJudge("F1", True, "s")
    run = _run(tmp_path, AndGateJudge(primary, second))  # type: ignore[arg-type]
    assert primary.calls == len(ELEMENTS)
    # the second is asked once with the primary (it runs inside judge(), before the quote check), never again on a retry
    assert second.calls == len(ELEMENTS) and run.contracts[0].elements[0].quote_check == "unknown_fact"
