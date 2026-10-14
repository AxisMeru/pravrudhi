"""The in-process stage driver: equal to the engine's own run on every shape, and every tampering refused and recorded."""

from __future__ import annotations

import json

import pytest

from pravrudhi.application import nyaya_agent as agent_mod
from pravrudhi.application import nyaya_lean_registry as reg_mod
from pravrudhi.application.a0_assist import driver as D
from tests import test_nyaya_agent as T

FACTS = list(T.TOY_FACTS)


def s_proof():
    return T._proof_script(FACTS)


def s_denial():
    s = T._proof_script(FACTS)
    s[T.BNS69_DENY] = [T._est("F1", FACTS[0], "told her he would marry her")]
    return s


def s_abstain():
    s = T._proof_script(FACTS)
    s[T.BNS69_EL[1]] = [T._not()]
    return s


def s_uncertain():
    s = T._proof_script(FACTS)
    s[T.BNS69_EL[0]] = [T._est("F2", FACTS[1], "never to marry Lata", p=0.6)]
    return s


def s_bad_quote():
    s = T._proof_script(FACTS)
    s[T.BNS69_EL[0]] = [T._bad("F2", "a quote that is not in the fact")]
    return s


def s_unquotable_defeater():
    s = T._proof_script(FACTS)
    s[T.BNS69_DENY] = [T._bad("F1", "not a substring of fact one")]
    return s


SCRIPTS = {
    "proof": s_proof,
    "denial": s_denial,
    "abstain": s_abstain,
    "uncertain": s_uncertain,
    "bad_quote": s_bad_quote,
    "unquotable_defeater": s_unquotable_defeater,
}
CID = "bns69"


def canon(x):
    return json.dumps(x, sort_keys=True, ensure_ascii=False, default=str)


def agents(tmp_path, script, validated=True):
    cfg = T._config(tmp_path) if validated else T._config(tmp_path, validated_contracts=frozenset())
    registry = T._registry()
    native = agent_mod.NyayaAgent(T.ScriptedJudge(script()), registry, cfg)
    driven = agent_mod.NyayaAgent(T.ScriptedJudge(script()), registry, cfg)
    return native, driven


def drive(agent, cids=(CID,)):
    d = D.A0Driver(agent, request={"facts": FACTS, "narrative": "", "contract_ids": list(cids)})
    ing = d.call("ingest", {"facts": list(FACTS), "narrative": ""})
    sel = d.call("select_contracts", {"contract_ids": list(cids)})
    for c in sel["contracts"]:
        cid = c["contract_id"]
        desc = d.call("describe_contract", {"contract_id": cid})
        outs = [
            d.call(
                "judge_element",
                {
                    "contract_id": cid,
                    "element": e["element_id"],
                    "is_denial": e["is_denial"],
                    "facts": ing["facts"],
                    "narrative": "",
                },
            )
            for e in desc["elements"]
        ]
        for o in outs:
            if o.get("quote"):
                d.call("quote_check", {"facts": ing["facts"], "fact_id": o["fact_id"], "quote": o["quote"]})
        asm = d.call("assemble", {"contract_id": cid, "element_results": outs})
        lc = d.call("lean_check", {"wire": asm["wire"], "contract_id": cid})
        d.call("decide", {"contract_id": cid, "element_results": outs, "lean_outcome": lc["lean_outcome"]})
    return d


@pytest.mark.parametrize("name", sorted(SCRIPTS))
def test_the_drivers_decide_equals_the_engines_own_run(tmp_path, name):
    native, driven = agents(tmp_path, SCRIPTS[name])
    run = native.run(FACTS, contract_ids=[CID]).to_dict()
    d = drive(driven)
    assert d.refusals == [] and d.complete() == []
    assert canon([d._decided[c] for c in d._selected]) == canon(run["contracts"])


def test_contract_not_validated_reason_matches(tmp_path):
    native, driven = agents(tmp_path, s_proof, validated=False)
    run = native.run(FACTS, contract_ids=[CID]).to_dict()
    d = drive(driven)
    assert run["contracts"][0]["reason"] == "contract_not_validated" == d._decided[CID]["reason"]


def test_the_scripts_cover_every_outcome_class(tmp_path):
    seen = set()
    for name, s in SCRIPTS.items():
        native, _ = agents(tmp_path / name, s)
        c = native.run(FACTS, contract_ids=[CID]).to_dict()["contracts"][0]
        seen.add(c["outcome"])
    assert seen >= {"PROOF", "DENIAL", "ABSTAIN", "REFER_TO_LAWYER"}


def start(tmp_path, upto="ingest"):
    _, driven = agents(tmp_path, s_proof)
    d = D.A0Driver(driven, request={"facts": FACTS, "narrative": "", "contract_ids": [CID]})
    ing = d.call("ingest", {"facts": list(FACTS), "narrative": ""})
    if upto == "ingest":
        return d, ing, None
    d.call("select_contracts", {"contract_ids": [CID]})
    desc = d.call("describe_contract", {"contract_id": CID})
    return d, ing, desc


def judged(d, ing, desc):
    return [
        d.call(
            "judge_element",
            {"contract_id": CID, "element": e["element_id"], "is_denial": e["is_denial"], "facts": ing["facts"], "narrative": ""},
        )
        for e in desc["elements"]
    ]


def test_tampered_ingest_facts_refused(tmp_path):
    _, driven = agents(tmp_path, s_proof)
    d = D.A0Driver(driven, request={"facts": FACTS, "narrative": "", "contract_ids": [CID]})
    out = d.call("ingest", {"facts": [FACTS[0] + " (edited)"] + FACTS[1:], "narrative": ""})
    assert out["refused"] and len(d.refusals) == 1 and d._ingested is None


def test_narrative_in_ingest_must_match_the_request(tmp_path):
    _, driven = agents(tmp_path, s_proof)
    d = D.A0Driver(driven, request={"facts": FACTS, "narrative": "", "contract_ids": [CID]})
    assert d.call("ingest", {"facts": list(FACTS), "narrative": "added"})["refused"]


def test_contract_ids_must_be_the_callers(tmp_path):
    d, _, _ = start(tmp_path)
    assert d.call("select_contracts", {"contract_ids": ["bns85"]})["refused"]
    assert d.call("select_contracts", {"sections": ["x"]})["refused"]


def test_judge_element_refusals_and_once_only(tmp_path):
    d, ing, desc = start(tmp_path, "describe")
    el = desc["elements"][0]
    base = {"contract_id": CID, "element": el["element_id"], "is_denial": el["is_denial"], "facts": ing["facts"], "narrative": ""}
    assert d.call("judge_element", {**base, "facts": ing["facts"][:-1]})["refused"]
    assert d.call("judge_element", {**base, "element": "an element the agent invented"})["refused"]
    assert d.call("judge_element", {**base, "is_denial": not el["is_denial"]})["refused"]
    assert d.call("judge_element", {**base, "narrative": "extra"})["refused"]
    assert "status" in d.call("judge_element", base)
    assert d.call("judge_element", base)["refused"]


def test_assemble_decide_and_lean_refusals(tmp_path):
    d, ing, desc = start(tmp_path, "describe")
    outs = judged(d, ing, desc)
    asm = d.call("assemble", {"contract_id": CID, "element_results": outs})
    lc = d.call("lean_check", {"wire": asm["wire"], "contract_id": CID})
    bad = [dict(outs[0], quote="tampered")] + outs[1:]
    assert d.call("decide", {"contract_id": CID, "element_results": bad, "lean_outcome": lc["lean_outcome"]})["refused"]
    assert d.call("decide", {"contract_id": CID, "element_results": outs, "lean_outcome": "DENIAL"})["refused"]
    assert d.call(
        "decide", {"contract_id": CID, "element_results": outs, "lean_outcome": lc["lean_outcome"], "refer_band": [0.0, 1.0]}
    )["refused"]
    assert "outcome" in d.call("decide", {"contract_id": CID, "element_results": outs, "lean_outcome": lc["lean_outcome"]})
    assert d.call("decide", {"contract_id": CID, "element_results": outs, "lean_outcome": lc["lean_outcome"]})["refused"]


def test_lean_check_wire_must_be_the_assemble_wire(tmp_path):
    d, ing, desc = start(tmp_path, "describe")
    asm = d.call("assemble", {"contract_id": CID, "element_results": judged(d, ing, desc)})
    assert d.call("lean_check", {"wire": asm["wire"] + "\textra", "contract_id": CID})["refused"]


def test_assemble_before_every_element_is_judged_is_refused(tmp_path):
    d, ing, desc = start(tmp_path, "describe")
    one = d.call(
        "judge_element",
        {
            "contract_id": CID,
            "element": desc["elements"][0]["element_id"],
            "is_denial": False,
            "facts": ing["facts"],
            "narrative": "",
        },
    )
    assert d.call("assemble", {"contract_id": CID, "element_results": [one]})["refused"]


def test_quote_check_must_cite_a_judged_quote(tmp_path):
    d, ing, desc = start(tmp_path, "describe")
    outs = judged(d, ing, desc)
    q = next(o for o in outs if o.get("quote"))
    assert d.call("quote_check", {"facts": ing["facts"], "fact_id": q["fact_id"], "quote": q["quote"]})["valid"] is True
    assert d.call("quote_check", {"facts": ing["facts"], "fact_id": "F1", "quote": "a quote nobody returned"})["refused"]


def test_stage_order_unknown_tool_and_ingest_once(tmp_path):
    _, driven = agents(tmp_path, s_proof)
    d = D.A0Driver(driven, request={"facts": FACTS, "narrative": "", "contract_ids": [CID]})
    assert d.call("describe_contract", {"contract_id": CID})["refused"]
    assert d.call("bash", {"cmd": "ls"})["refused"]
    assert len(d.refusals) == 2
    assert d.call("ingest", {"facts": list(FACTS), "narrative": ""}).get("facts") is None  # not first: refused
    d2, _, _ = start(tmp_path / "b")
    assert d2.call("ingest", {"facts": list(FACTS), "narrative": ""})["refused"]


def test_an_incomplete_run_is_reported(tmp_path):
    d, _, _ = start(tmp_path)
    probs = d.complete()
    assert any("select_contracts" in p for p in probs) and any("decide" in p for p in probs)


def test_a_judge_failure_makes_the_run_incomplete(tmp_path):
    _, driven = agents(tmp_path, s_proof)
    driven.judge.failures = ["Timeout: judge"]
    d = drive(driven)
    assert any("judge failure: Timeout" in p for p in d.complete())


def test_wire_roundtrip():
    a = {"element (one), 100%": True, "two": True}
    assert D.parse_wire(reg_mod.reg_wire_line(a, "c1")) == ("c1", a)
