"""Accused-attribution check, v1 variant D0 (prabhasa-nyaya #418 design, Lead-2 decisions 5 Oct 2026): a deterministic
check that the actor of a quoted act IS the accused under review, for elements that require a specific act by the accused.

Every fact, name and accused number below is a CONSTRUCTED toy (accused numbering is randomised per pair so a number never
encodes the label); no real or sealed text appears anywhere in this file, and no model is called (judges are scripted doubles).

Part 1 (this block) is the FLAG-OFF GOLDEN: the agent's results and audit records with the flag off must stay byte-identical
to what the code produced before the feature existed. The golden file was generated from the unmodified tree."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from pravrudhi.application import nyaya_lean_registry as reg
from pravrudhi.application.nyaya_agent import NyayaAgent
from pravrudhi.application.nyaya_judges import ElementJudgment, SpanRelevanceJudge
from tests.test_nyaya_agent import ScriptedJudge, ScriptedRegistry, _config

GOLDEN = Path(__file__).parent / "fixtures" / "nyaya_attribution_flag_off_golden.json"

EL1 = "the accused is the husband, or a relative of the husband, of the woman"
EL2 = "subjects the woman to cruelty (s.86(a) or (b))"
EL3 = "the facts attribute specific acts to this accused, not only a general or omnibus allegation"
ELS = [EL1, EL2, EL3]
FACTS = [
    "TOY: Dev is the husband of Nila.",
    "TOY: Dev beat Nila on several evenings in March.",
    "TOY: Dev demanded money from Nila's parents.",
]


def _est(fid: str, quote: str, p: float = 0.97) -> ElementJudgment:
    return ElementJudgment("established", p, fid, quote, "model")


def _registry() -> ScriptedRegistry:
    return ScriptedRegistry(
        contracts={"bns85": reg.DescribedContract("bns85", list(ELS), [])},
        sources={"bns85": ["Bharatiya Nyaya Sanhita (toy source)"]},
    )


def _cfg(tmp_path: Path, **over: Any):
    return _config(tmp_path, judge_statute_text={"bns85": "TRAINING statute text for bns85"},
                   validated_contracts=frozenset({"bns85"}), **over)


def _script(facts: list[str] = FACTS) -> dict[str, list[ElementJudgment | Exception]]:
    return {EL1: [_est("F1", "the husband of Nila")], EL2: [_est("F2", "beat Nila on several evenings")],
            EL3: [_est("F2", "Dev beat Nila")]}


def _strip(o: Any) -> Any:
    if isinstance(o, dict):
        return {k: _strip(v) for k, v in o.items() if not (k in ("ts", "run_id", "audit_path") or k.endswith("_ms"))}
    if isinstance(o, list):
        return [_strip(v) for v in o]
    return o


def _snapshot(run: Any) -> dict[str, Any]:
    lines = [json.loads(x) for x in run.audit_path.read_text().splitlines()]
    for ln in lines:  # these two steps hash wall-clock timings in their inputs, so the hash differs run to run
        if ln["step"] in ("judge_accounting", "run_end"):
            ln.pop("inputs_sha256", None)
    return json.loads(json.dumps(_strip({"run": run.to_dict(), "audit": lines}), sort_keys=True, default=str))


def _golden_runs(tmp_path: Path, **cfg_over: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    # 1: plain single judge, PROOF
    out["proof"] = _snapshot(NyayaAgent(ScriptedJudge(_script()), _registry(), _cfg(tmp_path / "a", **cfg_over)).run(FACTS, narrative="TOY.", contract_ids=["bns85"]))
    # 2: an element not established (ABSTAIN)
    s = _script()
    s[EL3] = [ElementJudgment("not_established", 0.03)]
    out["abstain"] = _snapshot(NyayaAgent(ScriptedJudge(s), _registry(), _cfg(tmp_path / "b", **cfg_over)).run(FACTS, narrative="TOY.", contract_ids=["bns85"]))
    # 3: span-relevance stack demotes element 3
    check = ScriptedJudge({EL1: [_est("F1", "x")], EL2: [_est("F1", "x")], EL3: [ElementJudgment("not_established", 0.1)]})
    out["span_demoted"] = _snapshot(NyayaAgent(SpanRelevanceJudge(ScriptedJudge(_script()), check), _registry(), _cfg(tmp_path / "c", **cfg_over)).run(FACTS, narrative="TOY.", contract_ids=["bns85"]))
    # 4: a quote that is not in the named fact (quote check rejects)
    s = _script()
    s[EL3] = [_est("F2", "this text is not in the fact")]
    out["bad_quote"] = _snapshot(NyayaAgent(ScriptedJudge(s), _registry(), _cfg(tmp_path / "d", **cfg_over)).run(FACTS, narrative="TOY.", contract_ids=["bns85"]))
    return out


def test_generate_golden_only_when_asked(tmp_path: Path) -> None:
    if os.environ.get("NYAYA_ATTRIBUTION_WRITE_GOLDEN") == "1":
        GOLDEN.write_text(json.dumps(_golden_runs(tmp_path), indent=1, sort_keys=True) + "\n")
    assert GOLDEN.exists()


def test_flag_off_results_and_audit_are_byte_identical_to_the_pre_feature_golden(tmp_path: Path) -> None:
    assert json.dumps(_golden_runs(tmp_path), indent=1, sort_keys=True) + "\n" == GOLDEN.read_text()
