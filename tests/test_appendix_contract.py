"""CONTRACT of the steps appendix on a finished assisted SCREENING response (the memo-steps work in the app reads exactly this).

Shape recorded from production engine 0.6.8 (Track B round 6; recorded file sha12 203899ef8854). `appendix` is a TOP-LEVEL
key of the response, NOT inside `assistant`:
  appendix = {appendix_version: 1, steps: [{name, calls, check?}], per_contract: [{contract_id, elements_judged, quotes_checked,
              quotes_valid, lean_agrees}], refused_calls}
Step names are the engine's own stage names. The key sets are EXACT on purpose: renaming, moving, retyping OR ADDING a field
is a change the app must be told about (update this file and the app together, or bump appendix_version)."""

from __future__ import annotations

from typing import Any

import pytest

from pravrudhi.application import assistant_tier as TIER
from pravrudhi.application.a0_assist import driver as A0
from pravrudhi.application.a0_assist import runtime as RT
from pravrudhi.application.steps_appendix.appendix import APPENDIX_VERSION, build_appendix
from tests.test_a0_assist_route import BODY, URL, client


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-real")  # the same set-up as tests/test_a0_assist_route.py
    TIER.reset()
    yield
    RT.reset()
    TIER.reset()


APPENDIX_KEYS = {"appendix_version", "steps", "per_contract", "refused_calls"}
STEP_KEYS_REQUIRED = {"name", "calls"}
STEP_KEYS_ALL = {"name", "calls", "check"}
PER_CONTRACT_KEYS = {"contract_id", "elements_judged", "quotes_checked", "quotes_valid", "lean_agrees"}
#: The step names of the production run the shape was recorded from, in the engine's order.
RECORDED_STEP_NAMES = ["ingest", "select_contracts", "describe_contract", "judge_element", "assemble", "lean_check", "decide"]
CHECK_VALUES = {"lean_check": {"agrees", "differs"}, "quote_check": {"valid", "invalid"}}


def check_appendix_shape(ap: Any) -> None:
    assert isinstance(ap, dict) and set(ap) == APPENDIX_KEYS, sorted(ap) if isinstance(ap, dict) else type(ap)
    assert ap["appendix_version"] == 1 and APPENDIX_VERSION == 1 and type(ap["appendix_version"]) is int
    assert isinstance(ap["steps"], list) and ap["steps"], "steps is a non-empty list"
    for s in ap["steps"]:
        assert isinstance(s, dict) and STEP_KEYS_REQUIRED <= set(s) <= STEP_KEYS_ALL, s
        assert s["name"] in A0.STAGES and type(s["calls"]) is int and s["calls"] >= 1, s
        if "check" in s:
            assert s["name"] in CHECK_VALUES and s["check"] in CHECK_VALUES[s["name"]], s
    assert isinstance(ap["per_contract"], list) and ap["per_contract"]
    for row in ap["per_contract"]:
        assert isinstance(row, dict) and set(row) == PER_CONTRACT_KEYS, row
        assert isinstance(row["contract_id"], str) and row["contract_id"]
        for k in ("elements_judged", "quotes_checked", "quotes_valid"):
            assert type(row[k]) is int and row[k] >= 0, (k, row)
        assert type(row["lean_agrees"]) is bool
        assert row["quotes_valid"] <= row["quotes_checked"]
    assert type(ap["refused_calls"]) is int and ap["refused_calls"] >= 0


def test_a_finished_screening_response_carries_the_appendix_as_a_top_level_key_not_inside_assistant(tmp_path):
    out = client(tmp_path).post(URL, json=BODY).json()
    assert out["assistant"]["used"] is True and out["assistant"]["outcome"] == "finished"
    assert "appendix" in out  # top level
    assert "appendix" not in out["assistant"]  # never inside the assistant block
    check_appendix_shape(out["appendix"])


def test_the_steps_are_the_engines_stage_names_in_run_order_and_match_the_recorded_production_shape(tmp_path):
    ap = client(tmp_path).post(URL, json=BODY).json()["appendix"]
    names = [s["name"] for s in ap["steps"]]
    assert names[0] == "ingest" and names[-1] == "decide"
    assert set(names) <= set(A0.STAGES)
    # the recorded production run used exactly these steps; the engine may add quote_check when it checks quotes, nothing else
    assert [n for n in names if n != "quote_check"] == RECORDED_STEP_NAMES  # all seven, once each, in order


def test_the_appendix_exists_only_on_a_finished_run(tmp_path):
    anonymous = client(tmp_path, user=None).post(URL, json=BODY).json()
    assert anonymous["assistant"]["used"] is False
    assert "appendix" not in anonymous  # a fallback carries no appendix (not even a null one)


def test_build_appendix_alone_has_the_same_contract_as_the_route_output():
    import tempfile
    from pathlib import Path

    from tests import test_a0_assist_driver as AD
    from tests.test_steps_appendix import calls_for

    ap = build_appendix(calls_for(Path(tempfile.mkdtemp()), "proof"))
    check_appendix_shape(ap)
    assert AD.CID in {r["contract_id"] for r in ap["per_contract"]}
