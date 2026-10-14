"""The eight stage tools of the assisted screening, executed in-process against the engine's own functions.

Each stage is the engine's OWN function or method:
  ingest            -> nyaya_agent.ingest_facts
  select_contracts  -> nyaya_agent.select_contracts over registry.list_contracts()
  describe_contract -> registry.describe
  judge_element     -> NyayaAgent._judge_element (the live judge boundary: one JudgeRequest, bounded quote retries included)
  quote_check       -> nyaya_quote.locate_quote (a pure re-check of a cited quote against the ingested facts)
  assemble          -> assemble_assertions + expected_outcome + reg_wire_line
  lean_check        -> registry.check on the assertions parsed back from the wire (wire sha binding) + outcome_from_lean
  decide            -> the engine's decision tail (a faithful restatement of NyayaAgent._run_contract after the Lean call),
                       returning the ContractResult exactly as AgentRun.to_dict() carries it.
The decision tail is inline in the engine, so `decide` restates it; its equality with the engine is a test (a sweep over
scripted element results against NyayaAgent.run), not an assumption.

BINDING: every agent-carried input must equal (canonical JSON) the earlier tool output it comes from. A violation is REFUSED
(the tool returns {"error": ..., "refused": true}, nothing is executed), recorded in `refusals`, and makes the run
incomplete. Every call is recorded as {tool, input, output} in call order. No tool accepts an outcome, reason or status it
did not compute; outcome and reason exist only in the outputs of assemble, lean_check and decide. Tool outputs live in
memory for the request only.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from types import SimpleNamespace
from typing import Any


def engine_namespace() -> SimpleNamespace:
    from pravrudhi.application import nyaya_agent, nyaya_judges, nyaya_lean_registry, nyaya_quote

    return SimpleNamespace(agent=nyaya_agent, judges=nyaya_judges, quote=nyaya_quote, reg=nyaya_lean_registry)


STAGES = ("ingest", "select_contracts", "describe_contract", "judge_element", "quote_check", "assemble", "lean_check", "decide")


def canon(x: Any) -> str:
    return json.dumps(x, sort_keys=True, ensure_ascii=False)


class Refused(Exception):
    """A tool call refused by the live binding / stage-order check (the item is a session FAIL)."""


def parse_wire(wire: str) -> tuple[str, dict[str, bool]]:
    """Inverse of reg_wire_line for the Met assertions: (contract_id, {element: True})."""
    parts = wire.split("\t")
    if len(parts) < 3 or parts[0] != "REG" or parts[1] != "live":
        raise ValueError("not a REG wire line")
    out: dict[str, bool] = {}
    for claim in parts[3:]:
        pre, suf = "G_SATISFIES(E(the conduct in the facts,AC),E(", ",EL))"
        if not (claim.startswith(pre) and claim.endswith(suf)):
            raise ValueError("unrecognised claim in the wire line")
        name = claim[len(pre) : -len(suf)]
        out[name.replace("%28", "(").replace("%29", ")").replace("%2C", ",").replace("%25", "%")] = True
    return parts[2], out


class A0Driver:
    def __init__(self, agent: Any, *, request: dict[str, Any], proceeding_posture: str | None = None, eng: Any = None) -> None:
        """agent: a NyayaAgent (config, registry, judge); request: {"facts": [str], "narrative": str, "contract_ids": [str]} as
        the caller gave it (the binding reference). `eng` is the engine namespace (a test may pass a stand-in)."""
        self.eng, self.agent, self.request, self.posture = eng or engine_namespace(), agent, request, proceeding_posture
        self.calls: list[dict[str, Any]] = []
        self.refusals: list[str] = []
        self._ingested: dict[str, Any] | None = None
        self._selected: list[str] | None = None
        self._described: dict[str, Any] = {}
        self._described_out: dict[str, Any] = {}
        self._judged: dict[str, list[dict[str, Any]]] = {}
        self._assembled: dict[str, dict[str, Any]] = {}
        self._lean: dict[str, dict[str, Any]] = {}
        self._decided: dict[str, dict[str, Any]] = {}
        self._statute: dict[str, dict[str, Any]] = {}
        self.listed: dict[str, list[str]] | None = None

    # ---- dispatch ------------------------------------------------------------------------------------------
    def call(self, tool: str, inp: dict[str, Any]) -> dict[str, Any]:
        """Execute one stage tool; returns its output dict, or {"error": ...} when refused. Always recorded."""
        try:
            if tool not in STAGES:
                raise Refused(f"unknown tool {tool!r}")
            out = getattr(self, "_t_" + tool)(inp if isinstance(inp, dict) else {})
        except Refused as e:
            self.refusals.append(f"{tool}: {e}")
            out = {"error": str(e), "refused": True}
        self.calls.append({"tool": tool, "input": inp, "output": out})
        return out

    # ---- helpers -------------------------------------------------------------------------------------------
    def _need(self, cond: bool, msg: str) -> None:
        if not cond:
            raise Refused(msg)

    def _facts_tuple(self) -> tuple[Any, ...]:
        return tuple(self.eng.agent.Fact(f["id"], f["text"], f["sha256"]) for f in self._ingested["facts"])  # type: ignore[index]

    def _statute_view(self, cid: str) -> dict[str, Any]:
        """The statute fields `_run_contract` computes before judging (judge training text vs the binary's official text)."""
        if cid in self._statute:
            return self._statute[cid]
        a, cfg = self.eng.agent, self.agent.config
        training = cfg.judge_statute_text.get(cid)
        official = self.agent.registry.source_text(cid)
        mismatch = None if training is None else training != official
        cut = int(cfg.house_judge.get("statute_chars") or 0)
        judge_rule_text = (
            training[:cut] if (training is not None and cut > 0 and (mismatch is True or len(training) > cut)) else None
        )
        similarity = None if training is None else a._text_similarity(training, official)
        self._statute[cid] = {
            "training": training,
            "official": official,
            "mismatch": mismatch,
            "similarity": similarity,
            "judge_rule_text": judge_rule_text,
        }
        return self._statute[cid]

    # ---- stage 1 -------------------------------------------------------------------------------------------
    def _t_ingest(self, inp: dict[str, Any]) -> dict[str, Any]:
        self._need(not self.calls, "ingest is called exactly once, as the first call")
        self._need(canon(inp.get("facts")) == canon(self.request["facts"]), "ingest input facts differ from the request facts")
        self._need(
            canon(inp.get("narrative", "")) == canon(self.request.get("narrative", "")),
            "ingest narrative differs from the request narrative",
        )
        facts = self.eng.agent.ingest_facts(inp["facts"])
        out = {"facts": [{"id": f.id, "text": f.text, "sha256": f.sha256} for f in facts], "narrative": inp.get("narrative", "")}
        self._ingested = out
        return out

    # ---- stage 2 -------------------------------------------------------------------------------------------
    def _t_select_contracts(self, inp: dict[str, Any]) -> dict[str, Any]:
        self._need(self._ingested is not None, "select_contracts comes after ingest")
        self._need(self._selected is None, "select_contracts is called once")
        self._need(("contract_ids" in inp) != ("sections" in inp), "pass exactly one of contract_ids or sections")
        self._need("sections" not in inp, "this request selects by contract_ids")
        self._need(
            canon(inp.get("contract_ids")) == canon(self.request["contract_ids"]),
            "select_contracts input is not the caller's contract_ids verbatim",
        )
        listed = self.agent.registry.list_contracts()
        self.listed = {c: list(s) for c, s in listed.items()}
        chosen = self.eng.agent.select_contracts(listed, contract_ids=inp["contract_ids"])
        self._selected = chosen
        return {"contracts": [{"contract_id": c, "source_sections": list(listed[c])} for c in chosen]}

    # ---- stage 3 -------------------------------------------------------------------------------------------
    def _t_describe_contract(self, inp: dict[str, Any]) -> dict[str, Any]:
        cid = inp.get("contract_id")
        self._need(
            self._selected is not None and cid in self._selected, "describe_contract names a contract select_contracts returned"
        )
        self._need(cid not in self._described, "describe_contract is called once per contract")
        d = self.agent.registry.describe(cid)
        self._described[cid] = d
        out = {
            "contract_id": cid,
            "elements": [{"element_id": e, "description": e, "is_denial": False} for e in d.elements]
            + [{"element_id": x, "description": x, "is_denial": True} for x in d.denials],
            "required_elements": list(d.elements),
            "defeaters": list(d.denials),
        }
        self._described_out[cid] = out
        return out

    # ---- stage 4 -------------------------------------------------------------------------------------------
    def _t_judge_element(self, inp: dict[str, Any]) -> dict[str, Any]:
        cid, element, is_denial = inp.get("contract_id"), inp.get("element"), inp.get("is_denial")
        self._need(cid in self._described, "judge_element comes after describe_contract for the contract")
        d = self._described[cid]
        listed = d.denials if is_denial is True else d.elements
        self._need(
            isinstance(is_denial, bool) and element in listed,
            "judge_element names an element describe_contract listed, with the same is_denial",
        )
        self._need(all(r["element"] != element for r in self._judged.get(cid, [])), "an element is judged once")
        self._need(
            canon(inp.get("facts")) == canon(self._ingested["facts"]), "judge_element input facts differ from the ingest output"
        )  # type: ignore[index]
        self._need(
            canon(inp.get("narrative", "")) == canon(self._ingested["narrative"]),
            "judge_element narrative differs from the ingest output",
        )  # type: ignore[index]
        st = self._statute_view(cid)
        self._need(
            st["training"] is not None, "no training statute text for the contract (the engine would ABSTAIN without judging)"
        )
        res, _calls = self.agent._judge_element(
            self.agent.judge,
            self._audit(),
            cid,
            element,
            is_denial,
            st["training"],
            self._facts_tuple(),
            self._ingested["narrative"],
            self.posture,
        )  # type: ignore[index]
        out = dataclasses.asdict(res)
        self._judged.setdefault(cid, []).append(out)
        return out

    def _audit(self) -> Any:
        if not hasattr(self, "_audit_obj"):
            self._audit_obj = _NullAudit()
        return self._audit_obj

    # ---- stage 5 -------------------------------------------------------------------------------------------
    def _t_quote_check(self, inp: dict[str, Any]) -> dict[str, Any]:
        self._need(self._ingested is not None, "quote_check comes after ingest")
        self._need(
            canon(inp.get("facts")) == canon(self._ingested["facts"]), "quote_check input facts differ from the ingest output"
        )  # type: ignore[index]
        cited = {(r["fact_id"], r["quote"]) for rs in self._judged.values() for r in rs if r.get("quote")}
        self._need((inp.get("fact_id"), inp.get("quote")) in cited, "quote_check cites a quote no judge_element returned")
        loc = self.eng.quote.locate_quote(
            {f["id"]: f["text"] for f in self._ingested["facts"]}, fact_id=inp["fact_id"], quote=inp["quote"]
        )  # type: ignore[index]
        return {
            "valid": loc.valid,
            "reason": loc.reason,
            "start": loc.start,
            "end": loc.end,
            "occurrences": loc.occurrences,
            "offsets_source": loc.offsets_source,
        }

    # ---- stage 6 -------------------------------------------------------------------------------------------
    def _results_for(self, cid: str, supplied: Any) -> list[Any]:
        self._need(
            canon(supplied) == canon(self._judged.get(cid, [])), "element_results differ from the earlier judge_element outputs"
        )
        d = self._described[cid]
        self._need(
            {r["element"] for r in self._judged[cid]} == set(d.elements) | set(d.denials),
            "every element and defeater is judged before this stage",
        )
        return [self.eng.agent.ElementResult(**r) for r in self._judged[cid]]

    def _t_assemble(self, inp: dict[str, Any]) -> dict[str, Any]:
        cid = inp.get("contract_id")
        self._need(cid in self._described and cid not in self._assembled, "assemble is called once per described contract")
        results = self._results_for(cid, inp.get("element_results"))
        a = self.eng.agent
        assertions = a.assemble_assertions(self._described[cid], {r.element: a._established_tristate(r.status) for r in results})
        out = {
            "contract_id": cid,
            "assertions": assertions,
            "expected_outcome": a.expected_outcome(self._described[cid], assertions),
            "wire": self.eng.reg.reg_wire_line(assertions, cid),
        }
        self._assembled[cid] = out
        return out

    # ---- stage 7 -------------------------------------------------------------------------------------------
    def _t_lean_check(self, inp: dict[str, Any]) -> dict[str, Any]:
        cid = inp.get("contract_id")
        self._need(cid in self._assembled and cid not in self._lean, "lean_check is called once per assembled contract")
        self._need(
            canon(inp.get("wire")) == canon(self._assembled[cid]["wire"]), "lean_check wire differs from the assemble output"
        )
        wcid, met = parse_wire(inp["wire"])
        assertions = dict(self._assembled[cid]["assertions"])
        self._need(
            wcid == cid and {k for k, v in assertions.items() if v} == set(met),
            "the wire does not carry the assembled assertions",
        )
        a = self.eng.agent
        lean = self.agent.registry.check(assertions, cid)
        outcome = a.outcome_from_lean(lean)
        attestation = {
            "binary_sha256": self.agent.registry.sha256,
            "wire_sha256": self.eng.reg.reg_wire_sha256(assertions, cid),
            "verdict": str(lean.get("verdict")),
        }
        out = {"contract_id": cid, "lean_outcome": outcome, "lean": lean, "lean_attestation": attestation}
        self._lean[cid] = out
        return out

    # ---- stage 8 -------------------------------------------------------------------------------------------
    def _t_decide(self, inp: dict[str, Any]) -> dict[str, Any]:
        cid = inp.get("contract_id")
        self._need(cid in self._lean and cid not in self._decided, "decide is called once per contract, after lean_check")
        self._need(
            canon(inp.get("lean_outcome")) == canon(self._lean[cid]["lean_outcome"]),
            "decide lean_outcome differs from the lean_check output",
        )
        results = self._results_for(cid, inp.get("element_results"))
        cfg = self.agent.config
        for extra, mine in (("refer_band", list(cfg.refer_band)),):
            if extra in inp:
                self._need(canon(inp[extra]) == canon(mine), "decide refer_band is not the engine's own band")
        out = self._decision(cid, results)
        if "uncertain" in inp:
            self._need(canon(inp["uncertain"]) == canon(out["uncertain"]), "decide uncertain list is not the engine's own")
        self._decided[cid] = out
        return out

    def _decision(self, cid: str, results: list[Any]) -> dict[str, Any]:
        """The engine's decision tail, restated. Returns the ContractResult as AgentRun.to_dict() carries it (rule-text
        fields included; the product response pops them separately)."""
        a, cfg = self.eng.agent, self.agent.config
        st = self._statute_view(cid)
        assembled, lean_out = self._assembled[cid], self._lean[cid]
        assertions, lean, lean_outcome, attestation = (
            assembled["assertions"],
            lean_out["lean"],
            lean_out["lean_outcome"],
            lean_out["lean_attestation"],
        )

        def finish(outcome: str, reason: str, **kw: Any) -> dict[str, Any]:
            def early(name: str) -> list[str]:
                """Early decisions (input_too_long, judge_error) never compute these lists; absent means none (the engine default)."""
                try:
                    return kw[name]
                except KeyError:
                    return []

            res = a.ContractResult(
                cid,
                outcome,
                reason,
                results,
                kw.get("assertions"),
                kw.get("lean"),
                kw.get("lean_outcome"),
                early("uncertain"),
                st["mismatch"],
                early("uncertain_second"),
                statute_text_similarity=st["similarity"],
                element_stages={r.element: s for r in results if (s := a.element_stage(r))},
                unavailable_second=early("unavailable_second"),
                gate1_unavailable=early("gate1_unavailable"),
                gate1_failed=early("gate1_failed"),
                gate1_contradiction=early("gate1_contradiction"),
                lean_attestation=kw.get("lean_attestation"),
                rule_text=st["official"],
                judge_rule_text=st["judge_rule_text"],
                rule_text_source=a.RULE_TEXT_SOURCE,
            )
            return dataclasses.asdict(res)

        if any(r.input_too_long for r in results):
            return finish("REFER_TO_LAWYER", "input_too_long")
        if any(r.error is not None for r in results):
            return finish("ABSTAIN", "judge_error")
        local = assembled["expected_outcome"]
        uncertain = [r.element for r in results if r.p_established is not None and cfg.in_band(r.p_established)]
        uncertain_second = [r.element for r in results if r.second_refer_band_fired]
        unavailable_second = [r.element for r in results if r.second_unavailable]
        defeater_disagreement = [r.element for r in results if r.defeater_second_disagreement]
        gate1_unavailable = [r.element for r in results if r.gate1_unavailable]
        gate1_failed = [r.element for r in results if r.gate1_not_entailed]
        gate1_contradiction = [r.element for r in results if r.gate1_contradiction]
        kw: dict[str, Any] = {
            "assertions": assertions,
            "lean": lean,
            "lean_outcome": lean_outcome,
            "lean_attestation": attestation,
            "uncertain": uncertain,
            "uncertain_second": uncertain_second,
            "unavailable_second": unavailable_second,
            "gate1_unavailable": gate1_unavailable,
            "gate1_failed": gate1_failed,
            "gate1_contradiction": gate1_contradiction,
        }
        if lean_outcome != local:
            return finish("ABSTAIN", "assembly_lean_mismatch", **kw)
        if any(r.is_denial and r.claimed and r.status != "established" for r in results):
            return finish("REFER_TO_LAWYER", "denial_unquotable", **kw)
        if defeater_disagreement:
            return finish("REFER_TO_LAWYER", "second_judge_defeater_disagreement", **kw)
        if uncertain:
            return finish("REFER_TO_LAWYER", "uncertain", **kw)
        if uncertain_second:
            return finish("REFER_TO_LAWYER", "uncertain_second_judge", **kw)
        if unavailable_second:
            return finish("REFER_TO_LAWYER", "second_judge_unavailable", **kw)
        if gate1_unavailable:
            return finish("REFER_TO_LAWYER", "gate1_unavailable", **kw)
        if gate1_failed:
            return finish("REFER_TO_LAWYER", "gate1_not_entailed", **kw)
        if gate1_contradiction:
            return finish("REFER_TO_LAWYER", "gate1_contradiction", **kw)
        if lean_outcome in ("PROOF", "DENIAL") and cid not in cfg.validated_contracts:
            return finish("REFER_TO_LAWYER", "contract_not_validated", **kw)
        reasons = {"PROOF": "all_elements_established", "DENIAL": "denial_established", "ABSTAIN": "missing_element"}
        return finish(lean_outcome, reasons[lean_outcome], **kw)

    # ---- the session's end ---------------------------------------------------------------------------------
    def complete(self) -> list[str]:
        """Problems that make the run incomplete even with no refusal: a missing stage, no decide, decide not last."""
        p: list[str] = []
        order = [c["tool"] for c in self.calls if not (c["output"] or {}).get("refused")]
        for s in STAGES:
            if s not in order:
                p.append(f"stage {s} never ran")
        if order and order[-1] != "decide":
            p.append("decide is not the last stage")
        if order.count("decide") != 1:
            p.append(f"decide ran {order.count('decide')} times (exactly one is registered)")
        if self._selected is not None and set(self._decided) != set(self._selected):
            p.append("not every selected contract was decided")
        p += [f"judge failure: {f}" for f in getattr(self.agent.judge, "failures", [])]
        return p


class _NullAudit:
    """The engine's _judge_element writes audit steps; the per-step records here are the tool calls, so this only collects."""

    def __init__(self) -> None:
        self.steps: list[dict[str, Any]] = []

    def step(self, name: str, inputs: Any, output: Any, wall_ms: float) -> None:
        self.steps.append(
            {
                "step": name,
                "inputs_sha256": hashlib.sha256(
                    json.dumps(inputs, sort_keys=True, ensure_ascii=False, default=str).encode()
                ).hexdigest(),
                "output": output,
            }
        )
