"""The hearing-pack driver: the A0 screening driver plus ONE more tool, `pack`, valid only after `decide` (#415).

`PackDriver` IS an `A0Driver` (same eight stage tools, same binding rules, same refusals) with a ninth stage. The pack is
computed in-process by `build_pack` from the driver's own decide outputs and ingested facts; the only agent-carried input is
`contract_ids`, which must equal the selected contracts verbatim. No tool accepts an outcome, a prompt or any text it did not
compute. The run is complete only when decide ran exactly once per selected contract and `pack` ran exactly once, last.
"""

from __future__ import annotations

from typing import Any

from pravrudhi.application.a0_assist import driver as drv
from pravrudhi.application.hearing_pack.pack import PACK_TOOL_NAME, build_pack

STAGES_PACK = (*drv.STAGES, PACK_TOOL_NAME)


class PackDriver(drv.A0Driver):
    def __init__(self, *a: Any, **kw: Any) -> None:
        super().__init__(*a, **kw)
        self._pack: dict[str, Any] | None = None

    def call(self, tool: str, inp: dict[str, Any]) -> dict[str, Any]:
        """Same contract as A0Driver.call: the output dict, or {"error", "refused"}; every call recorded in order."""
        try:
            if tool not in STAGES_PACK:
                raise drv.Refused(f"unknown tool {tool!r}")
            out = getattr(self, "_t_" + tool)(inp if isinstance(inp, dict) else {})
        except drv.Refused as e:
            self.refusals.append(f"{tool}: {e}")
            out = {"error": str(e), "refused": True}
        self.calls.append({"tool": tool, "input": inp, "output": out})
        result: dict[str, Any] = out
        return result

    def _t_pack(self, inp: dict[str, Any]) -> dict[str, Any]:
        self._need(self._pack is None, "pack is called once")
        self._need(self._selected is not None and bool(self._selected), "pack comes after select_contracts")
        self._need(set(self._decided) == set(self._selected or []), "pack comes after decide for every selected contract")
        self._need(set(inp) == {"contract_ids"}, "pack takes only contract_ids")
        self._need(
            drv.canon(inp.get("contract_ids")) == drv.canon(self._selected),
            "pack contract_ids are not the selected contracts verbatim",
        )
        contracts = [self._decided[c] for c in self._selected or []]
        self._pack = build_pack(contracts, self._ing()["facts"], self._ing().get("narrative", ""))
        return self._pack

    def complete(self) -> list[str]:
        """The A0 problems for the eight stages (decide count, every selected contract decided, judge failures), plus: `pack`
        ran exactly once and is the LAST successful tool (A0's own 'decide is not the last stage' does not apply here)."""
        order = [c["tool"] for c in self.calls if not (c["output"] or {}).get("refused")]
        problems = [p for p in super().complete() if p != "decide is not the last stage" and p != "stage pack never ran"]
        if order.count(PACK_TOOL_NAME) != 1:
            problems.append(f"pack ran {order.count(PACK_TOOL_NAME)} times (exactly one is registered)")
        if order and order[-1] != PACK_TOOL_NAME:
            problems.append("pack is not the last stage")
        return problems
