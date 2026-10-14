"""The by-reference stage driver: the hand-over the pinned product agent's tools use (E4b design; tools file
a0_tools_v2_E4b.json, manifest 5fb55e20).

`A0Driver` (driver.py) is the copy-style hand-over: the agent re-types every earlier output into the next call and the driver
checks it equals the stored one. The product agent's tools are by REFERENCE: every tool result carries a driver-assigned
`call_ref` (I1 S1 D1.. J1.. Q1.. A1.. L1..; `decide` carries none), later tools take refs, and the driver RESOLVES them to the
stored outputs, so the agent cannot alter a stage output between calls. Every stored output keeps its sha256, re-checked at each
resolution (`tampered_output`). A refused call returns {"error": "<code>: <message>", "refused": true}, is recorded with its
code, and the agent may call again (a refusal is an EVENT, not the end of the session). The stage logic is the parent's, unchanged
(the engine's own functions, the decision tail); only the hand-over differs.

Codes: bad_args, bad_ref, wrong_contract, ref_reused, out_of_order, missing_quote_check, incomplete_elements, extra_elements,
order_mismatch, lean_mismatch, already_decided, tampered_output, binding.
`quote_check` is required only for a `judge_element` result whose output carries a non-empty quote; `decide` is accepted once
per contract; the accepted decide results are the answer. Nothing here accepts an outcome, reason or status the driver did not
compute.
"""

from __future__ import annotations

import copy
import hashlib
from typing import Any

from pravrudhi.application.a0_assist import driver as D

CODES = (
    "bad_args", "bad_ref", "wrong_contract", "ref_reused", "out_of_order", "missing_quote_check", "incomplete_elements",
    "extra_elements", "order_mismatch", "lean_mismatch", "already_decided", "tampered_output", "binding",
)
PREFIX = {
    "ingest": "I", "select_contracts": "S", "describe_contract": "D", "judge_element": "J", "quote_check": "Q",
    "assemble": "A", "lean_check": "L",
}


class RefusedCode(D.Refused):
    """A refusal with one of the fixed codes the agent is told about."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code, self.message = code, message


def sha(obj: Any) -> str:
    return hashlib.sha256(D.canon(obj).encode()).hexdigest()


class A0RefDriver(D.A0Driver):
    #: The tool names this driver answers. A workflow built on it (a subclass) extends this tuple, adds a `_t_<tool>` method and
    #: overrides `_resolve` for its own tool, calling `super()._resolve` for the rest.
    STAGES: tuple[str, ...] = D.STAGES

    def __init__(self, *a: Any, **kw: Any) -> None:
        super().__init__(*a, **kw)
        self.store: dict[str, dict[str, Any]] = {}
        self.counts: dict[str, int] = {}
        self.refusal_events: list[dict[str, Any]] = []
        self.decide_attempts = 0

    # ---- store and refs ----------------------------------------------------------------------------------------------
    def _store(self, tool: str, cid: str | None, out: dict[str, Any], **extra: Any) -> str:
        self.counts[tool] = self.counts.get(tool, 0) + 1
        ref = f"{PREFIX[tool]}{self.counts[tool]}"
        self.store[ref] = {"tool": tool, "cid": cid, "obj": copy.deepcopy(out), "sha": sha(out), **extra}
        return ref

    def _get(self, ref: Any, kinds: tuple[str, ...], cid: str | None = None) -> dict[str, Any]:
        if not isinstance(ref, str) or ref not in self.store:
            raise RefusedCode("bad_ref", f"no such ref {ref!r}")
        e = self.store[ref]
        if e["tool"] not in kinds:
            raise RefusedCode("bad_ref", f"{ref} is a {e['tool']} result; a {'/'.join(kinds)} ref is required here")
        if cid is not None and e["cid"] not in (None, cid):
            raise RefusedCode("wrong_contract", f"{ref} belongs to contract {e['cid']!r}, not {cid!r}")
        if sha(e["obj"]) != e["sha"]:
            raise RefusedCode("tampered_output", f"the stored output of {ref} no longer matches its sha256")
        return e

    def _element_order(self, cid: str) -> list[str]:
        return [e["description"] for e in self._described_out[cid]["elements"]]

    def _resolve_element_refs(self, cid: str, refs: Any) -> list[dict[str, Any]]:
        if not isinstance(refs, list) or not all(isinstance(r, str) for r in refs):
            raise RefusedCode("bad_args", "element_refs must be a list of judge_element call_refs")
        if len(set(refs)) != len(refs):
            dup = sorted({r for r in refs if refs.count(r) > 1})
            raise RefusedCode("ref_reused", f"a ref is used twice in element_refs: {dup}")
        outs = [self._get(r, ("judge_element",), cid)["obj"] for r in refs]
        want = self._element_order(cid)
        got = [o["element"] for o in outs]
        missing = [w for w in want if w not in got]
        extra = [g for g in got if g not in want]
        if extra:
            raise RefusedCode("extra_elements", f"element_refs name results that are not elements of {cid}: {extra}")
        if missing:
            raise RefusedCode("incomplete_elements", f"element_refs omit the results for: {missing}")
        if got != want:
            raise RefusedCode("order_mismatch", f"element_refs must follow the contract's element order: {want}")
        return outs

    def _quote_covered(self, refs: list[str]) -> list[str]:
        have = {e["judge_ref"] for e in self.store.values() if e["tool"] == "quote_check"}
        return [r for r in refs if self.store[r]["obj"].get("quote") and r not in have]

    # ---- the call ----------------------------------------------------------------------------------------------------
    def call(self, tool: str, inp: dict[str, Any]) -> dict[str, Any]:
        agent_input = inp if isinstance(inp, dict) else {}
        resolved: dict[str, Any] = agent_input
        ref: str | None = None
        code: str | None = None
        out: dict[str, Any] = {}
        if tool == "decide":
            self.decide_attempts += 1
        try:
            if tool not in self.STAGES:
                raise RefusedCode("bad_args", f"unknown tool {tool!r}")
            resolved = self._resolve(tool, agent_input)
            out = getattr(self, "_t_" + tool)(resolved)
            if tool in PREFIX:
                extra: dict[str, Any] = {}
                if tool == "quote_check":
                    extra["judge_ref"] = agent_input["judge_ref"]
                if tool == "assemble":
                    extra["consumed"] = list(agent_input["element_refs"])
                if tool == "lean_check":
                    extra["from_assemble"] = agent_input["assemble_ref"]
                ref = self._store(tool, resolved.get("contract_id"), out, **extra)
        except D.Refused as e:
            code, msg = getattr(e, "code", "binding"), getattr(e, "message", str(e))
            self.refusals.append(f"{tool}: {code}: {msg}")
            self.refusal_events.append({"seq": len(self.calls), "tool": tool, "code": code, "message": msg})
            out = {"error": f"{code}: {msg}", "refused": True}
        self.calls.append(
            {
                "tool": tool, "input": resolved, "agent_input": agent_input, "output": out, "call_ref": ref,
                "refused": code is not None or bool(out.get("refused")), "refusal_code": code, "output_sha256": sha(out),
            }
        )
        # the agent gets a copy, never the stored object
        return dict(copy.deepcopy(out), call_ref=ref) if ref else copy.deepcopy(out)

    def _resolve(self, tool: str, a: dict[str, Any]) -> dict[str, Any]:
        """The agent's by-reference arguments -> the parent's by-value input, taken from the stored outputs."""

        def need(*keys: str) -> None:
            miss = [k for k in keys if k not in a]
            if miss:
                raise RefusedCode("bad_args", f"{tool} needs {miss}")
            for k in keys:  # a value of the wrong type is bad_args here, never an exception from a dict lookup further down
                if k == "is_denial":
                    ok = isinstance(a[k], bool)
                elif k == "element_refs":
                    ok = isinstance(a[k], list)
                else:
                    ok = isinstance(a[k], str)
                if not ok:
                    raise RefusedCode("bad_args", f"{tool}: {k} has the wrong type")

        if tool in ("ingest", "select_contracts", "describe_contract"):
            return a
        if tool == "judge_element":
            need("ingest_ref", "contract_id", "element", "is_denial")
            ing = self._get(a["ingest_ref"], ("ingest",))["obj"]
            return {
                "contract_id": a["contract_id"], "element": a["element"], "is_denial": a["is_denial"],
                "facts": ing["facts"], "narrative": ing["narrative"],
            }
        if tool == "quote_check":
            need("judge_ref")
            j = self._get(a["judge_ref"], ("judge_element",))["obj"]
            if not j.get("quote"):
                raise RefusedCode("bad_ref", f"{a['judge_ref']} returned no quote: nothing to check")
            if self._ingested is None:
                raise RefusedCode("out_of_order", "quote_check comes after ingest")
            return {"facts": self._ingested["facts"], "fact_id": j["fact_id"], "quote": j["quote"]}
        if tool == "assemble":
            need("contract_id", "element_refs")
            cid = a["contract_id"]
            if cid not in self._described:
                raise RefusedCode("out_of_order", f"assemble comes after describe_contract for {cid!r}")
            if cid in self._assembled:
                raise RefusedCode("out_of_order", f"{cid} is already assembled")
            outs = self._resolve_element_refs(cid, a["element_refs"])
            miss = self._quote_covered(a["element_refs"])
            if miss:
                raise RefusedCode(
                    "missing_quote_check", f"quote_check is required for the quote-bearing results {miss} before assemble"
                )
            self._judged[cid] = copy.deepcopy(outs)  # the contract's element order
            return {"contract_id": cid, "element_results": outs}
        if tool == "lean_check":
            need("contract_id", "assemble_ref")
            cid = a["contract_id"]
            asm = self._get(a["assemble_ref"], ("assemble",), cid)["obj"]
            return {"contract_id": cid, "wire": asm["wire"]}
        if tool == "decide":
            need("contract_id", "element_refs", "assemble_ref", "lean_ref")
            cid = a["contract_id"]
            if cid in self._decided:
                raise RefusedCode("already_decided", f"decide was already accepted for {cid}; it is called once per contract")
            ae = self._get(a["assemble_ref"], ("assemble",), cid)
            le = self._get(a["lean_ref"], ("lean_check",), cid)
            if le["from_assemble"] != a["assemble_ref"]:
                raise RefusedCode(
                    "lean_mismatch", f"{a['lean_ref']} was computed from {le['from_assemble']}, not from {a['assemble_ref']}"
                )
            outs = self._resolve_element_refs(cid, a["element_refs"])
            if list(a["element_refs"]) != ae["consumed"]:
                raise RefusedCode(
                    "lean_mismatch", f"decide's element_refs differ from the ones assemble consumed ({ae['consumed']})"
                )
            return {"contract_id": cid, "element_results": outs, "lean_outcome": le["obj"]["lean_outcome"]}
        raise RefusedCode("bad_args", f"unknown tool {tool!r}")

    # ---- ingest must be the first ACCEPTED call (an earlier refused call must not block it forever) ----------------------
    def _t_ingest(self, inp: dict[str, Any]) -> dict[str, Any]:
        self._need(
            self._ingested is None and not any(not c["refused"] for c in self.calls),
            "ingest is called exactly once, as the first accepted call",
        )
        calls, self.calls = self.calls, []
        try:
            return super()._t_ingest(inp)
        finally:
            self.calls = calls

    # ---- the session's end -------------------------------------------------------------------------------------------
    def decide_accepted(self) -> dict[str, Any]:
        return {cid: copy.deepcopy(o) for cid, o in self._decided.items()}

    def quote_rule(self) -> dict[str, Any]:
        js = [r for r, e in self.store.items() if e["tool"] == "judge_element"]
        withq = [r for r in js if self.store[r]["obj"].get("quote")]
        by = {e["judge_ref"]: r for r, e in self.store.items() if e["tool"] == "quote_check"}
        return {
            "judge_refs_with_quote": withq, "quote_check_by_judge_ref": by,
            "missing": [r for r in withq if r not in by], "no_quote_judge_refs": [r for r in js if r not in withq],
        }

    def complete(self) -> list[str]:
        """Problems that make the run incomplete (empty = complete): a missing stage (quote_check only where a quote exists),
        decide not last, a selected contract not decided. Refused calls are in `refusals`; the runner (assist.py) treats any of
        them as a safety refusal and falls back to the core result."""
        p: list[str] = []
        order = [c["tool"] for c in self.calls if not c["refused"]]
        for s in ("ingest", "select_contracts", "describe_contract", "judge_element", "assemble", "lean_check", "decide"):
            if s not in order:
                p.append(f"stage {s} never ran")
        miss = self.quote_rule()["missing"]
        if miss:
            p.append(f"stage quote_check never ran for the quote-bearing results {miss}")
        if order and order[-1] != "decide":
            p.append("decide is not the last stage")
        if self._selected is not None and set(self._decided) != set(self._selected):
            p.append("not every selected contract was decided")
        return p
