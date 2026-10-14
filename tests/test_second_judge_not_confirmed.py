"""#404: a required element the primary established and the second declined must not read as `missing_element`.
Both arms of every conditional: required vs defeater, primary-established vs not, each higher-precedence REFER wins,
outcomes that are not ABSTAIN untouched. Real AndGateJudge over scripted judges (never a hand-built second-judge field)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from pravrudhi.application.nyaya_agent import NyayaAgent
from pravrudhi.application.nyaya_judges import AndGateJudge
from tests.test_nyaya_agent import (
    BNS69_DENY,
    BNS69_EL,
    TOY_FACTS,
    ScriptedJudge,
    _config,
    _denial_script,
    _est,
    _not,
    _proof_script,
    _registry,
    _second,
    _second_answers_everything,
)

TAU2 = 0.97


def _run(tmp_path: Path, primary: dict[str, list[Any]], second: dict[str, list[Any]], *, delta: float | None = None) -> Any:
    gate = AndGateJudge(ScriptedJudge(primary), ScriptedJudge(second), tau_primary=0.74, tau_second=TAU2)
    cfg_second = {"tau": TAU2} if delta is None else {"tau": TAU2, "refer_logit_delta": delta}
    agent = NyayaAgent(gate, _registry(), _config(tmp_path, second_judge=cfg_second))
    return agent.run(TOY_FACTS, narrative="TOY narrative.", contract_ids=["bns69"]).contracts[0]


def _second_all(p_el0: float) -> dict[str, list[Any]]:
    s = _second_answers_everything()
    s[BNS69_EL[0]] = [_second("not_established", p_el0)]
    return s


def test_primary_established_and_second_declined_on_a_required_element_refers_with_its_own_reason(tmp_path: Path) -> None:
    c = _run(tmp_path, _proof_script(TOY_FACTS), _second_all(0.30))
    assert (c.outcome, c.reason) == ("REFER_TO_LAWYER", "second_judge_not_confirmed")
    assert c.elements[0].binding_leg == "second"


def test_the_primary_not_establishing_keeps_missing_element(tmp_path: Path) -> None:
    primary = _proof_script(TOY_FACTS)
    primary[BNS69_EL[0]] = [_not()]
    c = _run(tmp_path, primary, _second_answers_everything())
    assert (c.outcome, c.reason) == ("ABSTAIN", "missing_element")
    assert c.elements[0].binding_leg == "primary"


def test_a_defeater_disagreement_keeps_its_own_existing_reason(tmp_path: Path) -> None:
    primary = _proof_script(TOY_FACTS)
    primary[BNS69_DENY] = [_est("F3", TOY_FACTS[2], "sexual intercourse with Kiran")]  # the primary calls the defeater present
    second = _second_answers_everything()
    second[BNS69_DENY] = [_second("not_established", 0.30)]  # the second declines the defeater
    c = _run(tmp_path, primary, second)
    assert c.reason != "second_judge_not_confirmed"
    # unchanged: a defeater the two judges disagree on already refers under its own reason
    assert (c.outcome, c.reason) == ("REFER_TO_LAWYER", "second_judge_defeater_disagreement")


def test_a_denial_outcome_is_untouched(tmp_path: Path) -> None:
    c = _run(tmp_path, _denial_script(TOY_FACTS), _second_answers_everything())
    assert c.outcome == "DENIAL" and c.reason == "denial_established"


def test_a_proof_is_untouched(tmp_path: Path) -> None:
    c = _run(tmp_path, _proof_script(TOY_FACTS), _second_answers_everything())
    assert (c.outcome, c.reason) == ("PROOF", "all_elements_established")


def test_the_second_judge_refer_band_still_wins(tmp_path: Path) -> None:
    # p2 = 0.965 is below tau 0.97 (declined) and inside the logit band: `uncertain_second_judge` keeps precedence
    c = _run(tmp_path, _proof_script(TOY_FACTS), _second_all(0.965), delta=0.2)
    assert (c.outcome, c.reason) == ("REFER_TO_LAWYER", "uncertain_second_judge")


def test_outside_the_band_the_new_reason_applies_with_the_band_on(tmp_path: Path) -> None:
    c = _run(tmp_path, _proof_script(TOY_FACTS), _second_all(0.30), delta=0.2)
    assert (c.outcome, c.reason) == ("REFER_TO_LAWYER", "second_judge_not_confirmed")


def test_second_judge_unavailable_still_wins(tmp_path: Path) -> None:
    second = _second_all(0.30)
    second[BNS69_EL[1]] = [RuntimeError("second judge down")]
    c = _run(tmp_path, _proof_script(TOY_FACTS), second)
    assert (c.outcome, c.reason) == ("REFER_TO_LAWYER", "second_judge_unavailable")


def test_the_primary_band_still_wins(tmp_path: Path) -> None:
    primary = _proof_script(TOY_FACTS)
    primary[BNS69_EL[1]] = [_est("F3", TOY_FACTS[2], "Lata had sexual intercourse with Kiran", p=0.6)]
    c = _run(tmp_path, primary, _second_all(0.30))
    assert c.reason in ("uncertain", "uncertain_second_judge")


@pytest.mark.parametrize("n_declined", [1, 2])
def test_any_declined_required_element_is_enough(tmp_path: Path, n_declined: int) -> None:
    second = _second_all(0.30)
    if n_declined == 2:
        second[BNS69_EL[1]] = [_second("not_established", 0.20)]
    c = _run(tmp_path, _proof_script(TOY_FACTS), second)
    assert c.reason == "second_judge_not_confirmed"


def test_single_judge_mode_never_produces_the_reason(tmp_path: Path) -> None:
    primary = _proof_script(TOY_FACTS)
    primary[BNS69_EL[0]] = [_not()]
    agent = NyayaAgent(ScriptedJudge(primary), _registry(), _config(tmp_path))
    c = agent.run(TOY_FACTS, narrative="TOY narrative.", contract_ids=["bns69"]).contracts[0]
    assert (c.outcome, c.reason) == ("ABSTAIN", "missing_element")


# -- the A0 driver's own decide mirrors the engine rung (parity) -----------------------------------------------------
def test_the_a0_drivers_decide_equals_the_engines_own_run_for_the_new_reason(tmp_path: Path) -> None:
    from tests import test_a0_assist_driver as A

    def build(sub: Path) -> NyayaAgent:
        sub.mkdir()
        gate = AndGateJudge(ScriptedJudge(_proof_script(A.FACTS)), ScriptedJudge(_second_all(0.30)),
                            tau_primary=0.74, tau_second=TAU2)
        return NyayaAgent(gate, _registry(), _config(sub, second_judge={"tau": TAU2}))

    run = build(tmp_path / "native").run(A.FACTS, contract_ids=[A.CID]).to_dict()
    d = A.drive(build(tmp_path / "driven"))
    assert run["contracts"][0]["reason"] == "second_judge_not_confirmed" == d._decided[A.CID]["reason"]
    assert A.canon([d._decided[c] for c in d._selected]) == A.canon(run["contracts"])


def test_a_denial_outcome_with_a_declined_required_element_stays_a_denial(tmp_path: Path) -> None:
    # the rung fires only where the contract would otherwise ABSTAIN: a DENIAL (defeater established by both) is not changed
    c = _run(tmp_path, _denial_script(TOY_FACTS), _second_all(0.30))
    assert c.outcome == "DENIAL" and c.reason == "denial_established"
