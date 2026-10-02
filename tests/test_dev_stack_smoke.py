"""check_response in scripts/dev_stack_smoke.py is the smoke's only logic; pin its pass/fail rules."""

from __future__ import annotations

import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("dev_stack_smoke", Path(__file__).parent.parent / "scripts" / "dev_stack_smoke.py")
smoke = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
spec.loader.exec_module(smoke)  # type: ignore[union-attr]


def _resp(std, elements):
    return {"standard": std, "results": [{"elements": elements}]}


OK_STD = {"applied": "proved", "source": "default", "proceeding_posture": None, "in_judge_prompt": False}


def test_clean_response_passes() -> None:
    el = [{"element": "a", "status": "established", "fact_id": "F1", "quote": "q"},
          {"element": "b", "status": "not_established", "fact_id": None, "quote": None}]
    assert smoke.check_response(_resp(OK_STD, el), None) == []


def test_missing_or_wrong_standard_fails() -> None:
    assert smoke.check_response({"results": []}, None) == ["standard missing"]
    bad = {"applied": "prima_facie_disclosed", "source": "proceeding_posture",
           "proceeding_posture": "quash", "in_judge_prompt": False}
    assert any("proved/default" in f for f in smoke.check_response(_resp(bad, []), None))
    assert any("!= sent" in f for f in smoke.check_response(_resp(bad, []), "trial"))


def test_fact_id_fail_closed_rules() -> None:
    est_no_id = [{"element": "a", "status": "established", "fact_id": None, "quote": "q"}]
    assert any("without fact_id" in f for f in smoke.check_response(_resp(OK_STD, est_no_id), None))
    ne_with_id = [{"element": "a", "status": "not_established", "fact_id": "F1", "quote": None}]
    assert any("carries fact_id" in f for f in smoke.check_response(_resp(OK_STD, ne_with_id), None))


def test_in_judge_prompt_must_be_a_bool() -> None:
    bad = {**OK_STD, "in_judge_prompt": "no"}
    assert any("in_judge_prompt" in f for f in smoke.check_response(_resp(bad, []), None))
    assert smoke.check_response(_resp({**OK_STD, "in_judge_prompt": True}, []), None) == []
