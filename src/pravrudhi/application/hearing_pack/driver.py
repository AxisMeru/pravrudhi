"""The hearing-pack driver: the by-reference A0 driver plus ONE more tool, `pack`, valid only after `decide` (#415).

`PackDriver` IS an `A0RefDriver` (the hand-over the pinned product agent's tools use: every stage result carries a driver-assigned
`call_ref`, later tools take refs, the driver resolves them to the stored outputs; same refusal codes) with a ninth tool. The pack
is computed in-process by `build_pack` from the driver's accepted decide outputs and the ingested facts; the only agent-carried
input is `contract_ids` (plain ids, like `select_contracts`: decide results carry no ref), which must equal the selected contracts
verbatim. No tool accepts an outcome, a prompt or any text it did not compute. The run is complete only when the reference
driver's own checks pass (every stage ran, a quote check for every quote-bearing result, every selected contract decided) and
`pack` ran exactly once, last. A `pack` record has `call_ref` None, like `decide`.
"""

from __future__ import annotations

from typing import Any

from pravrudhi.application.a0_assist import driver as drv
from pravrudhi.application.a0_assist import ref_driver as rd
from pravrudhi.application.hearing_pack.pack import PACK_TOOL_NAME, build_pack

STAGES_PACK = (*drv.STAGES, PACK_TOOL_NAME)  # the tool names of this workflow, in order


class PackDriver(rd.A0RefDriver):
    STAGES = STAGES_PACK

    def __init__(self, *a: Any, **kw: Any) -> None:
        super().__init__(*a, **kw)
        self._pack: dict[str, Any] | None = None

    def _resolve(self, tool: str, a: dict[str, Any]) -> dict[str, Any]:
        if tool != PACK_TOOL_NAME:
            return super()._resolve(tool, a)
        if set(a) != {"contract_ids"}:
            raise rd.RefusedCode("bad_args", "pack takes only contract_ids")
        ids = a["contract_ids"]
        if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
            raise rd.RefusedCode("bad_args", "pack: contract_ids must be a list of contract ids")
        if self._pack is not None:
            raise rd.RefusedCode("already_decided", "pack is called once")
        if not self._selected or set(self._decided) != set(self._selected):
            raise rd.RefusedCode("out_of_order", "pack comes after decide for every selected contract")
        if drv.canon(ids) != drv.canon(self._selected):
            raise rd.RefusedCode("binding", "pack contract_ids are not the selected contracts verbatim")
        return {"contract_ids": list(ids)}

    def _t_pack(self, inp: dict[str, Any]) -> dict[str, Any]:
        contracts = [self._decided[c] for c in self._selected or []]
        ing = self._ing()
        self._pack = build_pack(contracts, ing["facts"], ing.get("narrative", ""))
        return self._pack

    def complete(self) -> list[str]:
        """The reference driver's problems, except its 'decide is not the last stage' (here `pack` is last), plus: `pack` ran
        exactly once and is the LAST successful tool."""
        order = [c["tool"] for c in self.calls if not c["refused"]]
        problems = [p for p in super().complete() if p != "decide is not the last stage"]
        if order.count(PACK_TOOL_NAME) != 1:
            problems.append(f"pack ran {order.count(PACK_TOOL_NAME)} times (exactly one is registered)")
        if order and order[-1] != PACK_TOOL_NAME:
            problems.append("pack is not the last stage")
        return problems
