"""R2's two named gaps on the ref driver (#423): `complete()` must name "decide is not the last stage" and "not every selected
contract was decided". Additive tests only."""

from __future__ import annotations

from pathlib import Path

from tests import test_a0_assist_ref_driver as RD


def _through_lean(tmp_path: Path):
    d = RD.fresh(tmp_path)
    _, _, js = RD.prefix(d)
    RD.quotes(d, js)
    asm = d.call("assemble", {"contract_id": RD.CID, "element_refs": RD.refs(js)})
    lc = d.call("lean_check", {"contract_id": RD.CID, "assemble_ref": asm["call_ref"]})
    return d, js, asm, lc


def test_a_selected_contract_not_decided_is_named(tmp_path: Path) -> None:
    d, js, asm, lc = _through_lean(tmp_path)
    assert "not every selected contract was decided" in d.complete()  # selected, assembled, lean-checked, never decided
    d.call("decide", RD.decide_args(js, asm, lc))
    assert d.complete() == []  # both arms: once decided the problem is gone


def test_decide_not_last_is_named_and_a_refused_call_after_decide_does_not_count(tmp_path: Path) -> None:
    d, js, asm, lc = _through_lean(tmp_path)
    d.call("decide", RD.decide_args(js, asm, lc))
    assert d.complete() == []
    refused = d.call("describe_contract", {"contract_id": RD.CID})  # described twice: refused
    assert refused["refused"] and d.complete() == []  # a refused call after decide is not a later stage
    later = {"tool": "quote_check", "input": {}, "output": {}, "call_ref": "Q9", "refused": False}
    d.calls.append(later)  # an accepted call after decide
    assert "decide is not the last stage" in d.complete()


def test_every_problem_is_listed_when_several_apply(tmp_path: Path) -> None:
    d = RD.fresh(tmp_path)
    problems = d.complete()
    assert "stage ingest never ran" in problems and "stage decide never ran" in problems
