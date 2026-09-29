"""`ContractResult.element_stages` splits the contract-level `missing_element` by where each element stopped."""

from __future__ import annotations

from pathlib import Path

from pravrudhi.application.nyaya_agent import ElementResult, NyayaAgent, element_stage
from pravrudhi.application.nyaya_judges import ElementJudgment
from tests.test_nyaya_agent_concurrency import (
    CONTRACT,
    ELEMENTS,
    FACTS,
    ConcurrentFakeJudge,
    FakeRegistry,
    _config,
    _script_all_established,
)


def _el(status: str, **kw: object) -> ElementResult:
    base = dict(element="e", is_denial=False, status=status, claimed=False, p_established=0.5, fact_id=None,
                quote=None, start=None, end=None, quote_check=None, attempts=1)
    base.update(kw)
    return ElementResult(**base)  # type: ignore[arg-type]


def test_element_stage_labels() -> None:
    assert element_stage(_el("established")) is None
    assert element_stage(_el("not_established", binding_leg="primary")) == "not_established:primary"
    assert element_stage(_el("not_confirmed", binding_leg="second")) == "not_confirmed:second"
    assert element_stage(_el("not_established", claimed=True, fact_id="F1.2", quote_check="unknown_fact")) == \
        "not_established:quote_unknown_fact"
    assert element_stage(_el("not_established")) == "not_established:none"


def test_contract_result_carries_stages_only_for_unmet_elements(tmp_path: Path) -> None:
    script = _script_all_established()
    script["el3"] = ElementJudgment("established", 0.99, "F1.2", None, None)
    script["el4"] = ElementJudgment("not_established", 0.1)
    run = NyayaAgent(ConcurrentFakeJudge(script), FakeRegistry(CONTRACT), _config(tmp_path, 1)).run(
        FACTS, narrative="", contract_ids=[CONTRACT.contract_id])
    c = run.contracts[0]
    assert c.reason == "missing_element"
    assert c.element_stages["el3"] == "not_established:quote_unknown_fact"
    assert c.element_stages["el4"].startswith("not_established:")
    assert set(ELEMENTS) - set(c.element_stages) == {"el1", "el2", "el5", "el6"}
