"""#416 steps appendix: rendered only from the recorded calls; both arms of every rule."""

from __future__ import annotations

import copy
import json

import pytest

from pravrudhi.application.a0_assist import driver as A0
from pravrudhi.application.steps_appendix.appendix import APPENDIX_VERSION, build_appendix
from tests import test_a0_assist_driver as AD


def calls_for(tmp_path, name):
    _, agent = AD.agents(tmp_path, AD.SCRIPTS[name])
    return AD.drive(agent).calls


def expected(calls):
    """An independent hand count of the same facts, written differently from the module."""
    ok = [c for c in calls if not (c["output"] or {}).get("refused")]
    judged = [c for c in ok if c["tool"] == "judge_element"]
    quotes = [c for c in ok if c["tool"] == "quote_check"]
    return len(judged), len(quotes), sum(1 for q in quotes if q["output"]["valid"] is True)


@pytest.mark.parametrize("name", sorted(AD.SCRIPTS))
def test_the_counts_equal_an_independent_count_of_the_calls(tmp_path, name):
    calls = calls_for(tmp_path, name)
    ap = build_appendix(calls)
    n_judged, n_quotes, n_valid = expected(calls)
    (row,) = ap["per_contract"]
    assert row["contract_id"] == AD.CID
    assert (row["elements_judged"], row["quotes_checked"], row["quotes_valid"]) == (n_judged, n_quotes, n_valid)
    assert ap["appendix_version"] == APPENDIX_VERSION and ap["refused_calls"] == 0


@pytest.mark.parametrize("name", sorted(AD.SCRIPTS))
def test_the_steps_are_the_stages_in_first_seen_order_with_their_call_counts(tmp_path, name):
    calls = calls_for(tmp_path, name)
    ap = build_appendix(calls)
    seen = list(dict.fromkeys(c["tool"] for c in calls))
    assert [s["name"] for s in ap["steps"]] == seen
    for s in ap["steps"]:
        assert s["calls"] == sum(1 for c in calls if c["tool"] == s["name"])
    assert seen[0] == "ingest" and seen[-1] == "decide" and set(seen) <= set(A0.STAGES)


def test_a_bad_quote_shows_fewer_valid_quotes_than_checked_and_a_good_run_shows_all_valid(tmp_path):
    good = build_appendix(calls_for(tmp_path / "g", "proof"))["per_contract"][0]
    bad = build_appendix(calls_for(tmp_path / "b", "bad_quote"))["per_contract"][0]
    assert good["quotes_checked"] > 0 and good["quotes_valid"] == good["quotes_checked"]
    assert bad["quotes_valid"] < bad["quotes_checked"]


def test_the_lean_check_agrees_with_the_assembly_in_a_normal_run(tmp_path):
    assert build_appendix(calls_for(tmp_path, "proof"))["per_contract"][0]["lean_agrees"] is True


def test_a_lean_outcome_that_differs_from_the_assembly_is_reported_as_differs(tmp_path):
    calls = copy.deepcopy(calls_for(tmp_path, "proof"))
    for c in calls:
        if c["tool"] == "lean_check":
            c["output"]["lean_outcome"] = "ABSTAIN" if c["output"]["lean_outcome"] != "ABSTAIN" else "PROOF"
    ap = build_appendix(calls)
    assert ap["per_contract"][0]["lean_agrees"] is False
    assert [s for s in ap["steps"] if s["name"] == "lean_check"][0]["check"] == "differs"


def test_refused_calls_are_excluded_and_counted(tmp_path):
    calls = copy.deepcopy(calls_for(tmp_path, "proof"))
    clean = build_appendix(calls)
    calls.insert(3, {"tool": "judge_element", "input": {"contract_id": AD.CID}, "output": {"error": "x", "refused": True}})
    ap = build_appendix(calls)
    assert ap["refused_calls"] == 1 and ap["per_contract"] == clean["per_contract"] and ap["steps"] == clean["steps"]


def test_the_same_calls_give_the_same_bytes_and_the_input_is_not_changed(tmp_path):
    calls = calls_for(tmp_path, "uncertain")
    before = copy.deepcopy(calls)
    a, b = build_appendix(calls), build_appendix(copy.deepcopy(calls))
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True) and calls == before


def test_no_text_from_the_agent_or_the_judge_reaches_the_appendix(tmp_path):
    calls = calls_for(tmp_path, "proof")
    ap = build_appendix(calls)
    blob = json.dumps(ap)
    for c in calls:  # no quote, fact text or element result string from any call appears in the appendix
        for v in ((c["output"] or {}).get("quote"), (c["output"] or {}).get("fact_id")):
            if isinstance(v, str) and len(v) > 8:
                assert v not in blob
    for word in ("PROOF", "DENIAL", "ABSTAIN", "REFER_TO_LAWYER"):
        assert word not in blob  # the appendix never carries an outcome


def test_an_empty_trace_gives_an_empty_appendix():
    assert build_appendix([]) == {"appendix_version": APPENDIX_VERSION, "steps": [], "per_contract": [], "refused_calls": 0}
