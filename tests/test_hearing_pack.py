"""#415 hearing-preparation pack: a pure function of the decide result, plus the `pack` driver tool. Both arms of every rule."""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import get_args

import pytest

from pravrudhi.application import nyaya_agent as agent_mod
from pravrudhi.application.a0_assist import driver as A0
from pravrudhi.application.hearing_pack import pack as P
from pravrudhi.application.hearing_pack.driver import STAGES_PACK, PackDriver
from tests import test_a0_assist_driver as AD
from tests import test_a0_assist_ref_driver as RD

REPO = Path(__file__).resolve().parents[1]
OUTCOME_WORDS = ("PROOF", "DENIAL", "ABSTAIN", "REFER_TO_LAWYER")


def el(name, status="established", denial=False, fact="F1"):
    return {"element": name, "is_denial": denial, "status": status, "fact_id": fact if status == "established" else None}


def contract(elements, reason="missing_element", outcome="ABSTAIN", **lists):
    return {"contract_id": "ipc415_property", "outcome": outcome, "reason": reason, "elements": elements, **lists}


# ---- the reason table: sentences of the signed table, pinned to the file
def doc_rows() -> dict[str, str]:
    t = (REPO / "docs/api/reason-codes.md").read_text()
    sec = t.split("## Contract `reason`")[1].split("## Element")[0]
    return dict(re.findall(r"^\| `(\w+)` \| (.+?) \|$", sec, re.M))


def test_every_reason_sentence_equals_the_signed_table_row():
    assert doc_rows() == P.REASON_TEXT


def test_the_table_covers_every_reason_the_engine_can_give():
    produced = {
        "input_too_long",
        "judge_error",
        "assembly_lean_mismatch",
        "denial_unquotable",
        "second_judge_defeater_disagreement",
        "uncertain",
        "uncertain_second_judge",
        "second_judge_unavailable",
        "gate1_unavailable",
        "gate1_not_entailed",
        "gate1_contradiction",
        "second_judge_not_confirmed",
        "contract_not_validated",
        "all_elements_established",
        "denial_established",
        "missing_element",
        "no_training_statute_text",
    }
    assert produced <= set(P.REASON_TEXT)


@pytest.mark.parametrize("reason", sorted(doc_rows()))
def test_the_pack_shows_the_sentence_and_never_the_code(reason):
    pk = P.build_pack([contract([el("E1")], reason=reason)], [])
    c = pk["contracts"][0]
    assert c["reason_text"] == P.REASON_TEXT[reason] and c["reason_code"] == reason
    assert "outcome" not in c and "stage" not in json.dumps(pk)
    display = " ".join(
        v
        for ob in [c, *c["open_ingredients"], *c["defences_cited"], *c["review_flags"]]
        for k, v in ob.items()
        if k.endswith("_text")
    )
    assert not any(code in display for code in P.REASON_TEXT)  # no reason code in any display field
    assert not any(w in json.dumps(pk) for w in OUTCOME_WORDS)


def test_an_unknown_reason_shows_the_generic_sentence_never_the_raw_code():
    c = P.build_pack([contract([el("E1")], reason="a_new_reason_nobody_wrote_text_for", uncertain=["E1"])], [])["contracts"][0]
    assert c["reason_text"] == P.GENERIC_REVIEW_TEXT
    assert "a_new_reason" not in c["reason_text"] and c["reason_code"] == "a_new_reason_nobody_wrote_text_for"


# ---- open ingredients: both arms of the status rule
@pytest.mark.parametrize(
    "status,prompt",
    [
        ("not_established", "no_fact_found"),
        ("not_confirmed", "cited_fact_unconfirmed"),
        ("not_evaluated_second_unavailable", "not_assessed"),
        ("not_evaluated_gate1_unavailable", "not_assessed"),
    ],
)
def test_a_non_established_ingredient_is_open_with_its_fixed_prompt(status, prompt):
    pk = P.build_pack([contract([el("E1"), el("E2", status), el("E3")])], [])
    (o,) = pk["contracts"][0]["open_ingredients"]
    assert (o["element"], o["ordinal"], o["status"], o["prompt_id"]) == ("E2", 2, status, prompt)
    assert o["prompt_text"] == P.PROMPT_DEFAULTS[prompt]


def test_an_established_ingredient_is_never_open_and_a_fully_established_contract_has_none():
    pk = P.build_pack([contract([el("E1"), el("E2")], reason="all_elements_established", outcome="PROOF")], [])
    assert pk["contracts"][0]["open_ingredients"] == []


def test_ordinals_count_required_ingredients_only_not_defences():
    els = [el("D1", "established", denial=True), el("E1", "not_established"), el("E2", "not_established")]
    pk = P.build_pack([contract(els)], [])
    assert [(o["element"], o["ordinal"]) for o in pk["contracts"][0]["open_ingredients"]] == [("E1", 1), ("E2", 2)]


# ---- defences: shown as prompts, only when a defeating fact is cited
def test_a_cited_defeater_is_a_prompt_for_review_and_an_uncited_one_is_not_shown():
    els = [el("E1"), el("D1", "established", denial=True, fact="F2"), el("D2", "not_established", denial=True)]
    (d,) = P.build_pack([contract(els)], [])["contracts"][0]["defences_cited"]
    assert (d["element"], d["ordinal"], d["fact_id"], d["prompt_id"]) == ("D1", 1, "F2", P.DEFENCE_PROMPT_ID)
    assert "finding" not in d["prompt_text"].lower().replace("not a finding", "")


# ---- review flags: copied from the decide lists, each with the table's sentence
@pytest.mark.parametrize(
    "key,reason_key",
    [
        ("uncertain", "uncertain"),
        ("uncertain_second", "uncertain_second_judge"),
        ("unavailable_second", "second_judge_unavailable"),
        ("gate1_unavailable", "gate1_unavailable"),
        ("gate1_failed", "gate1_not_entailed"),
        ("gate1_contradiction", "gate1_contradiction"),
    ],
)
def test_each_decide_list_becomes_a_flag_with_the_table_sentence(key, reason_key):
    c = contract([el("E1"), el("E2", "not_established")], reason="uncertain", **{key: ["E2"]})
    (f,) = P.build_pack([c], [])["contracts"][0]["review_flags"]
    assert f == {"element": "E2", "ordinal": 2, "flag_text": P.REASON_TEXT[reason_key]}


def test_no_list_no_flag():
    assert P.build_pack([contract([el("E1")])], [])["contracts"][0]["review_flags"] == []


# ---- citations: parsed by the engine's own parser, one verify body each, deduplicated
FACT_A = "The respondent relied on Narandas Karsondas v. S.A. Kamtam, (1977) 3 SCC 247 for the point."
FACT_B = "The same case, (1977) 3 SCC 247, is cited again, and AIR 1950 SC 27 as well."


def test_citations_are_found_deduplicated_and_ready_for_the_verifier():
    from pravrudhi.application.citations import parse_citations

    facts = [{"id": "F1", "text": FACT_A}, {"id": "F2", "text": FACT_B}]
    cites = P.build_pack([contract([el("E1")])], facts, "A narrative citing (1977) 3 SCC 247.")["citations_to_check"]
    by = {c["citation"]: c for c in cites}
    assert set(by) == {"(1977) 3 SCC 247", "AIR 1950 SC 27"}
    assert by["(1977) 3 SCC 247"]["source_ids"] == ["F1", "F2", "narrative"]
    for c in cites:
        assert c["verify_request"] == {"citation": c["citation"]} and len(parse_citations(c["citation"])) == 1


def test_no_citation_no_entry_and_a_statute_tag_is_not_a_case_citation():
    facts = [
        {"id": "F1", "text": "He promised to pay on the first of the month."},
        {"id": "F2", "text": "See [IPC/Section 415]."},
    ]
    assert P.build_pack([contract([el("E1")])], facts)["citations_to_check"] == []


def test_the_citation_list_is_capped():
    facts = [{"id": f"F{i}", "text": f"(2000) {i + 1} SCC {i + 100}"} for i in range(P.MAX_CITATIONS + 10)]
    assert len(P.build_pack([contract([el("E1")])], facts)["citations_to_check"]) == P.MAX_CITATIONS


# ---- purity
def test_same_input_same_bytes_and_the_input_is_not_changed():
    c = [contract([el("E1"), el("E2", "not_confirmed"), el("D1", "established", denial=True)], uncertain=["E2"])]
    facts = [{"id": "F1", "text": FACT_A}]
    before = copy.deepcopy((c, facts))
    a, b = P.build_pack(c, facts), P.build_pack(copy.deepcopy(c), copy.deepcopy(facts))
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True) and (c, facts) == before


def test_the_pack_has_no_free_text_beyond_the_fixed_tables():
    c = [contract([el("E1", "not_established")], reason="missing_element")]
    pk = P.build_pack(c, [])
    strings = {s for s in re.findall(r'"([^"]{25,})"', json.dumps(pk))}
    allowed = set(P.REASON_TEXT.values()) | set(P.PROMPT_DEFAULTS.values())
    assert strings <= allowed


def test_the_tool_schema_is_frozen():
    assert P.PACK_TOOL_NAME == "pack" and (*A0.STAGES, "pack") == STAGES_PACK
    assert P.PACK_TOOL_SCHEMA == {
        "type": "object",
        "properties": {"contract_ids": {"type": "array", "items": {"type": "string"}, "minItems": 1}},
        "required": ["contract_ids"],
        "additionalProperties": False,
    }


# ---- the driver (by reference: the hand-over the pinned product agent uses)
def pdrive(tmp_path, script=AD.s_proof, upto="decide"):
    _, agent = AD.agents(tmp_path, script)
    d = PackDriver(agent, request={"facts": AD.FACTS, "narrative": "", "contract_ids": [AD.CID]})
    if upto == "decide":
        RD.full(d)
    else:  # everything up to and including lean_check, no decide
        _, _, js = RD.prefix(d)
        RD.quotes(d, js)
        asm = d.call("assemble", {"contract_id": AD.CID, "element_refs": RD.refs(js)})
        d.call("lean_check", {"contract_id": AD.CID, "assemble_ref": asm["call_ref"]})
    return d


def code(out):
    return RD.code(out)


@pytest.mark.parametrize("name", sorted(AD.SCRIPTS))
def test_the_driver_pack_equals_the_pure_pack_of_the_engines_own_run(tmp_path, name):
    native, _ = AD.agents(tmp_path / "n", AD.SCRIPTS[name])
    run = native.run(AD.FACTS, contract_ids=[AD.CID]).to_dict()
    d = pdrive(tmp_path / "d", AD.SCRIPTS[name])
    out = d.call("pack", {"contract_ids": [AD.CID]})
    assert not out.get("refused") and d.refusals == [] and d.complete() == []
    ing = d._ingested
    assert json.dumps(out, sort_keys=True) == json.dumps(
        P.build_pack(run["contracts"], ing["facts"], ing["narrative"]), sort_keys=True
    )


def test_pack_has_no_call_ref_and_is_recorded_like_decide(tmp_path):
    d = pdrive(tmp_path)
    out = d.call("pack", {"contract_ids": [AD.CID]})
    assert "call_ref" not in out and d.calls[-1]["tool"] == "pack" and d.calls[-1]["call_ref"] is None
    assert d.calls[-1]["refused"] is False and d.calls[-1]["output_sha256"]


def test_pack_before_decide_is_refused_out_of_order_and_the_run_is_incomplete(tmp_path):
    d = pdrive(tmp_path, upto="lean")
    assert code(d.call("pack", {"contract_ids": [AD.CID]})) == "out_of_order"
    assert any("pack comes after decide" in r for r in d.refusals)
    assert any("pack ran 0 times" in p for p in d.complete())


def test_pack_twice_wrong_ids_and_extra_keys_are_refused_with_their_codes(tmp_path):
    d = pdrive(tmp_path)
    assert code(d.call("pack", {"contract_ids": ["not_the_selected_one"]})) == "binding"
    assert code(d.call("pack", {"contract_ids": [AD.CID], "prompts": ["invented"]})) == "bad_args"
    assert d.call("pack", {"contract_ids": [AD.CID]}).get("refused") is None
    assert code(d.call("pack", {"contract_ids": [AD.CID]})) == "already_decided"
    assert len(d.refusals) == 3


def test_a_missing_pack_or_a_pack_that_is_not_last_makes_the_run_incomplete(tmp_path):
    d = pdrive(tmp_path)
    assert any("pack ran 0 times" in p for p in d.complete())
    d.call("pack", {"contract_ids": [AD.CID]})
    assert d.complete() == []
    d.calls.append({"tool": "quote_check", "input": {}, "output": {"valid": True}, "refused": False})
    assert any("pack is not the last stage" in p for p in d.complete())


def test_the_plain_ref_driver_does_not_know_the_pack_tool(tmp_path):
    d = RD.fresh(tmp_path)
    assert code(d.call("pack", {"contract_ids": [AD.CID]})) == "bad_args"


def test_a_copy_style_pack_driver_call_is_refused_like_the_rest_of_the_ref_layer(tmp_path):
    pk = PackDriver(
        AD.agents(tmp_path / "x", AD.s_proof)[1], request={"facts": AD.FACTS, "narrative": "", "contract_ids": [AD.CID]}
    )
    ing = pk.call("ingest", {"facts": list(AD.FACTS), "narrative": ""})
    pk.call("select_contracts", {"contract_ids": [AD.CID]})
    desc = pk.call("describe_contract", {"contract_id": AD.CID})
    e = desc["elements"][0]
    copy_call = {
        "contract_id": AD.CID,
        "element": e["element_id"],
        "is_denial": e["is_denial"],
        "facts": ing["facts"],
        "narrative": "",
    }
    assert code(pk.call("judge_element", copy_call)) == "bad_args"


def test_the_pack_tool_takes_no_text_from_the_agent(tmp_path):
    d = pdrive(tmp_path)
    out = d.call("pack", {"contract_ids": [AD.CID]})
    assert set(out) == {"pack_version", "provenance", "contracts", "citations_to_check", "citations_truncated", "prompt_defaults"}


def test_a_citation_repeated_in_one_source_lists_that_source_once():
    facts = [{"id": "F1", "text": "(1977) 3 SCC 247 is cited, and again (1977) 3 SCC 247."}]
    (c,) = P.build_pack([contract([el("E1")])], facts)["citations_to_check"]
    assert c["source_ids"] == ["F1"]


BANNED = ("proved", "established", "verbatim", "verified", "fake", "invalid", "false", "fabricated")


def test_the_default_prompt_wording_uses_no_banned_phrase():
    for text in [*P.PROMPT_DEFAULTS.values(), P.GENERIC_REVIEW_TEXT]:
        assert not [w for w in BANNED if w in text.lower()], text


# ---- review findings of the isolated pre-check, each with its arm
def test_the_reason_table_covers_the_engines_own_reason_type():
    assert set(get_args(agent_mod.ContractReason)) <= set(P.REASON_TEXT)


def test_an_element_status_with_no_fixed_prompt_is_open_with_the_generic_prompt_not_a_crash():
    (o,) = P.build_pack([contract([el("E1", "a_status_nobody_planned")])], [])["contracts"][0]["open_ingredients"]
    assert o["prompt_id"] == P.GENERIC_PROMPT_ID and o["prompt_text"] == P.GENERIC_REVIEW_TEXT


def test_the_citation_cap_is_flagged_not_silent():
    many = [{"id": f"F{i}", "text": f"(2000) {i + 1} SCC {i + 100}"} for i in range(P.MAX_CITATIONS + 3)]
    assert P.build_pack([contract([el("E1")])], many)["citations_truncated"] is True
    assert P.build_pack([contract([el("E1")])], many[:3])["citations_truncated"] is False


def test_attach_pack_adds_the_pack_to_a_screening_result_and_passes_everything_else_through():
    body = {"run_id": "r", "contracts": [contract([el("E1", "not_established")])]}
    out = P.attach_pack(body, ["He promised to pay."], "")
    assert out["pack"]["contracts"][0]["open_ingredients"][0]["element"] == "E1" and out["run_id"] == "r" and "pack" not in body
    for other in ({"error": "x"}, {"contracts": "not a list"}, None, ["a"]):
        assert P.attach_pack(other, ["a fact"], "") is other


def test_a_pack_that_cannot_be_built_never_costs_the_user_the_ordinary_result():
    broken = {"run_id": "r", "contracts": [{"contract_id": "x"}]}  # no reason, no elements: build_pack raises inside
    assert P.attach_pack(broken, ["a fact"], "") is broken


def test_the_fallback_pack_numbers_facts_as_the_engine_does():
    from pravrudhi.application.nyaya_agent import ingest_facts

    texts = ["  First fact (1977) 3 SCC 247.  ", "Second."]
    ids = [f.id for f in ingest_facts(texts)]
    out = P.attach_pack({"contracts": [contract([el("E1")])]}, texts, "")
    assert ids == ["F1", "F2"] and out["pack"]["citations_to_check"][0]["source_ids"] == ["F1"]


@pytest.mark.parametrize("ids", [[], [AD.CID, AD.CID], "bns69", None, [AD.CID, "extra"], ["extra", AD.CID]])
def test_pack_contract_ids_must_be_the_selected_list_verbatim(tmp_path, ids):
    d = pdrive(tmp_path)
    assert code(d.call("pack", {"contract_ids": ids})) in ("binding", "bad_args")


def test_pack_before_select_contracts_is_refused(tmp_path):
    _, agent = AD.agents(tmp_path, AD.s_proof)
    d = PackDriver(agent, request={"facts": AD.FACTS, "narrative": "", "contract_ids": [AD.CID]})
    d.call("ingest", {"facts": list(AD.FACTS), "narrative": ""})
    assert code(d.call("pack", {"contract_ids": [AD.CID]})) == "out_of_order"


def test_no_text_the_judge_produced_reaches_the_pack(tmp_path):
    d = pdrive(tmp_path)
    out = d.call("pack", {"contract_ids": [AD.CID]})
    judged = [c["output"] for c in d.calls if c["tool"] == "judge_element"]
    blob = json.dumps(out)
    for j in judged:
        if isinstance(j.get("quote"), str) and len(j["quote"]) > 5:
            assert j["quote"] not in blob


def test_the_base_driver_problem_string_the_pack_driver_filters_still_exists(tmp_path):
    """PackDriver.complete drops the reference driver's 'decide is not the last stage': pin it so a rewording cannot hide it."""
    d = RD.fresh(tmp_path)
    RD.full(d)
    assert d.complete() == []
    d.calls.append({"tool": "quote_check", "input": {}, "output": {"valid": True}, "refused": False})
    assert "decide is not the last stage" in d.complete()
