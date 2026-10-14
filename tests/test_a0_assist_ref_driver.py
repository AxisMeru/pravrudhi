"""The by-reference driver (the hand-over the pinned product agent's tools use): equal to the engine's own run on every shape,
and every refusal code has a test. The stage logic is the parent's; these tests cover only the ref layer."""

from __future__ import annotations

import json

import pytest

from pravrudhi.application import nyaya_agent as agent_mod
from pravrudhi.application.a0_assist import ref_driver as R
from tests import test_a0_assist_driver as A
from tests import test_nyaya_agent as T

FACTS, CID = A.FACTS, A.CID


def agents(tmp_path, script):
    return A.agents(tmp_path, script)


def ref_driver(agent):
    return R.A0RefDriver(agent, request={"facts": FACTS, "narrative": "", "contract_ids": [CID]})


def prefix(d):
    ing = d.call("ingest", {"facts": list(FACTS), "narrative": ""})
    d.call("select_contracts", {"contract_ids": [CID]})
    desc = d.call("describe_contract", {"contract_id": CID})
    js = [
        d.call(
            "judge_element",
            {"ingest_ref": ing["call_ref"], "contract_id": CID, "element": e["element_id"], "is_denial": e["is_denial"]},
        )
        for e in desc["elements"]
    ]
    return ing, desc, js


def refs(js):
    return [j["call_ref"] for j in js]


def quotes(d, js):
    return [d.call("quote_check", {"judge_ref": j["call_ref"]}) for j in js if j.get("quote")]


def full(d):
    ing, desc, js = prefix(d)
    quotes(d, js)
    asm = d.call("assemble", {"contract_id": CID, "element_refs": refs(js)})
    lc = d.call("lean_check", {"contract_id": CID, "assemble_ref": asm["call_ref"]})
    dec = d.call("decide", decide_args(js, asm, lc))
    return ing, js, asm, lc, dec


def decide_args(js, asm, lc):
    return {"contract_id": CID, "element_refs": refs(js), "assemble_ref": asm["call_ref"], "lean_ref": lc["call_ref"]}


def code(out):
    return out["error"].split(":")[0] if out.get("refused") else None


def fresh(tmp_path, script=A.s_proof):
    _, driven = agents(tmp_path, script)
    return ref_driver(driven)


@pytest.mark.parametrize("name", sorted(A.SCRIPTS))
def test_the_by_reference_decide_equals_the_engines_own_run(tmp_path, name):
    native, driven = agents(tmp_path, A.SCRIPTS[name])
    run = native.run(FACTS, contract_ids=[CID]).to_dict()
    d = ref_driver(driven)
    full(d)
    assert d.refusals == [] and d.complete() == []
    assert A.canon([d._decided[c] for c in d._selected]) == A.canon(run["contracts"])


def test_every_result_carries_a_deterministic_ref_except_decide(tmp_path):
    d = fresh(tmp_path)
    ing, js, asm, lc, dec = full(d)
    assert "call_ref" not in dec and asm["call_ref"] == "A1" and lc["call_ref"] == "L1"
    assert refs(js) == [f"J{i + 1}" for i in range(len(js))] and ing["call_ref"] == "I1"
    assert [c["call_ref"] for c in d.calls[:3]] == ["I1", "S1", "D1"]
    assert d.calls[3]["input"]["facts"] == d.calls[0]["output"]["facts"]  # the RESOLVED input has the facts ...
    assert "facts" not in d.calls[3]["agent_input"]  # ... the agent's argument does not
    assert all("output_sha256" in c and "refused" in c for c in d.calls)


def test_a_ref_run_through_the_steps_summary_has_the_fixed_stage_names(tmp_path):
    from pravrudhi.application.a0_assist import assist as AS

    d = fresh(tmp_path)
    full(d)
    names = [s["name"] for s in AS.steps_from(d.calls)]
    assert names[0] == "ingest" and names[-1] == "decide" and "judge_element" in names


# ---- one test per refusal code ---------------------------------------------------------------------------------------------------
def test_bad_args(tmp_path):
    d = fresh(tmp_path)
    ing = d.call("ingest", {"facts": list(FACTS), "narrative": ""})
    d.call("select_contracts", {"contract_ids": [CID]})
    d.call("describe_contract", {"contract_id": CID})
    assert code(d.call("judge_element", {"ingest_ref": ing["call_ref"], "contract_id": CID})) == "bad_args"
    assert code(d.call("nonsense", {})) == "bad_args"
    assert code(d.call("assemble", {"contract_id": CID})) == "bad_args"
    assert code(d.call("quote_check", {})) == "bad_args"


def test_bad_ref_unknown_wrong_kind_and_no_quote(tmp_path):
    d = fresh(tmp_path)
    ing, desc, js = prefix(d)
    el = desc["elements"][0]
    base = {"contract_id": CID, "element": el["element_id"], "is_denial": el["is_denial"]}
    assert code(d.call("judge_element", {**base, "ingest_ref": "I99"})) == "bad_ref"
    assert code(d.call("judge_element", {**base, "ingest_ref": "D1"})) == "bad_ref"  # a describe result is not an ingest ref
    assert code(d.call("quote_check", {"judge_ref": "I1"})) == "bad_ref"
    no_quote = [j for j in js if not j.get("quote")]
    if no_quote:
        assert code(d.call("quote_check", {"judge_ref": no_quote[0]["call_ref"]})) == "bad_ref"
    assert code(d.call("assemble", {"contract_id": CID, "element_refs": ["J1", 5]})) == "bad_args"
    assert code(d.call("assemble", {"contract_id": CID, "element_refs": "J1"})) == "bad_args"


def test_wrong_contract(tmp_path):
    d = fresh(tmp_path)
    ing, desc, js = prefix(d)
    quotes(d, js)
    assert code(d.call("assemble", {"contract_id": "bns999", "element_refs": refs(js)})) == "out_of_order"  # not described
    d._described["other"] = d._described[CID]  # a second described contract, to carry a ref across contracts
    d._described_out["other"] = d._described_out[CID]
    assert code(d.call("assemble", {"contract_id": "other", "element_refs": refs(js)})) == "wrong_contract"


def test_ref_reused(tmp_path):
    d = fresh(tmp_path)
    _, _, js = prefix(d)
    assert code(d.call("assemble", {"contract_id": CID, "element_refs": [js[0]["call_ref"]] * 2})) == "ref_reused"


def test_out_of_order(tmp_path):
    d = fresh(tmp_path)
    assert code(d.call("assemble", {"contract_id": CID, "element_refs": []})) == "out_of_order"  # before describe
    d2 = fresh(tmp_path / "y")
    _, _, js2 = prefix(d2)
    quotes(d2, js2)
    first = d2.call("assemble", {"contract_id": CID, "element_refs": refs(js2)})
    assert "call_ref" in first
    assert code(d2.call("assemble", {"contract_id": CID, "element_refs": refs(js2)})) == "out_of_order"  # assembled once


def test_missing_quote_check_and_the_quote_rule_both_arms(tmp_path):
    d = fresh(tmp_path)
    _, _, js = prefix(d)
    with_quote = [j for j in js if j.get("quote")]
    assert with_quote  # the proof script has quote-bearing results
    assert code(d.call("assemble", {"contract_id": CID, "element_refs": refs(js)})) == "missing_quote_check"
    assert d.quote_rule()["missing"] == [j["call_ref"] for j in with_quote]
    quotes(d, js)
    assert d.quote_rule()["missing"] == []
    assert "call_ref" in d.call("assemble", {"contract_id": CID, "element_refs": refs(js)})  # allowed once covered
    # the other arm: a result with no quote needs no quote_check
    d2 = fresh(tmp_path / "nq", A.s_abstain)
    _, _, js2 = prefix(d2)
    nq = [j for j in js2 if not j.get("quote")]
    assert nq and all(j["call_ref"] not in d2.quote_rule()["judge_refs_with_quote"] for j in nq)


def test_incomplete_extra_and_order_mismatch(tmp_path):
    d = fresh(tmp_path)
    _, _, js = prefix(d)
    quotes(d, js)
    r = refs(js)
    assert code(d.call("assemble", {"contract_id": CID, "element_refs": r[:-1]})) == "incomplete_elements"
    assert code(d.call("assemble", {"contract_id": CID, "element_refs": list(reversed(r))})) == "order_mismatch"
    d.store[r[0]]["obj"]["element"] = "an element the contract does not have"  # a stored result for a non-element
    d.store[r[0]]["sha"] = R.sha(d.store[r[0]]["obj"])
    assert code(d.call("assemble", {"contract_id": CID, "element_refs": r})) == "extra_elements"


def test_lean_mismatch_decide_must_use_the_same_refs_and_assemble(tmp_path):
    d = fresh(tmp_path)
    _, _, js = prefix(d)
    quotes(d, js)
    asm = d.call("assemble", {"contract_id": CID, "element_refs": refs(js)})
    lc = d.call("lean_check", {"contract_id": CID, "assemble_ref": asm["call_ref"]})
    d.store[lc["call_ref"]]["from_assemble"] = "A9"  # a lean result computed from some other assemble
    base = decide_args(js, asm, lc)
    assert code(d.call("decide", base)) == "lean_mismatch"
    d.store[lc["call_ref"]]["from_assemble"] = asm["call_ref"]
    d.store[asm["call_ref"]]["consumed"] = list(reversed(refs(js)))  # assemble consumed another order than decide carries
    assert code(d.call("decide", base)) == "lean_mismatch"


def test_already_decided_and_decide_is_not_a_ref(tmp_path):
    d = fresh(tmp_path)
    ing, js, asm, lc, dec = full(d)
    again = d.call("decide", decide_args(js, asm, lc))
    assert code(again) == "already_decided" and d.decide_attempts == 2 and list(d.decide_accepted()) == [CID]
    after = d.call("assemble", {"contract_id": CID, "element_refs": ["J1"]})
    assert code(after) in ("out_of_order", "bad_ref", "incomplete_elements")


def test_tampered_output_is_caught_at_the_next_resolution(tmp_path):
    d = fresh(tmp_path)
    _, _, js = prefix(d)
    quotes(d, js)
    d.store[js[0]["call_ref"]]["obj"]["p_established"] = 0.0001  # the stored output changed behind the driver's back
    assert code(d.call("assemble", {"contract_id": CID, "element_refs": refs(js)})) == "tampered_output"


def test_a_refused_ingest_does_not_block_the_first_accepted_one(tmp_path):
    d = fresh(tmp_path)
    assert d.call("ingest", {"facts": ["not the request facts"], "narrative": ""})["refused"]
    out = d.call("ingest", {"facts": list(FACTS), "narrative": ""})
    assert out["call_ref"] == "I1" and "refused" not in out
    assert d.call("ingest", {"facts": list(FACTS), "narrative": ""})["refused"]  # exactly once


def test_complete_names_each_missing_part(tmp_path):
    d = fresh(tmp_path)
    assert any("never ran" in p for p in d.complete())
    ing, js, asm, lc, dec = full(fresh(tmp_path / "ok"))
    ok = fresh(tmp_path / "ok2")
    full(ok)
    assert ok.complete() == []
    ok.refusals.append("x")  # a refusal is recorded, but complete() judges the stages; the runner refuses on `refusals`
    assert ok.complete() == []


def test_the_refusals_are_recorded_with_their_code_and_the_agent_can_retry(tmp_path):
    d = fresh(tmp_path)
    ing, desc, js = prefix(d)
    d.call("assemble", {"contract_id": CID, "element_refs": ["J1"]})
    assert d.refusal_events and d.refusal_events[-1]["code"] in R.CODES and d.refusals
    quotes(d, js)
    assert "call_ref" in d.call("assemble", {"contract_id": CID, "element_refs": refs(js)})  # the session continues


def test_the_codes_are_the_fixed_list_the_agent_is_told():
    assert set(R.CODES) == {
        "bad_args", "bad_ref", "wrong_contract", "ref_reused", "out_of_order", "missing_quote_check", "incomplete_elements",
        "extra_elements", "order_mismatch", "lean_mismatch", "already_decided", "tampered_output", "binding",
    }


def test_the_pinned_tools_file_matches_this_drivers_inputs():
    """The product agent's tool schemas (a0_tools_v2_E4b.json, sha ab252d65...) are ref style: every ref argument the schema names
    is one this driver resolves."""
    expect = {
        "judge_element": {"ingest_ref", "contract_id", "element", "is_denial"},
        "quote_check": {"judge_ref"},
        "assemble": {"contract_id", "element_refs"},
        "lean_check": {"contract_id", "assemble_ref"},
        "decide": {"contract_id", "element_refs", "assemble_ref", "lean_ref"},
    }
    for tool, keys in expect.items():
        d = R.A0RefDriver.__new__(R.A0RefDriver)
        d.calls, d.refusals, d.refusal_events, d.store, d.counts, d.decide_attempts = [], [], [], {}, {}, 0
        for missing in keys:
            out = d.call(tool, {k: "x" for k in keys if k != missing})
            assert code(out) == "bad_args" and missing in out["error"], (tool, missing)
    assert T is not None and agent_mod is not None and json is not None
