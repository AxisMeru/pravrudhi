"""The steps appendix of an assisted run: counts and verdicts read from the recorded calls, nothing else (#416).

`build_appendix(calls)` takes the driver's recorded calls ({tool, input, output} in call order) and returns what the screening
did: the stages in the order first seen with how many times each ran, the quote and Lean checks as the existing `steps_from`
reports them, and per contract the counts of elements judged, quotes checked, quotes found valid, and whether the Lean check
agreed with the engine's own assembly. It is rendered ONLY from the calls: no text from the agent's messages reaches it, no
outcome or reason is computed here, and refused calls are excluded and counted. The memo shows it as one list.
"""

from __future__ import annotations

from typing import Any

from pravrudhi.application.a0_assist.assist import steps_from

APPENDIX_VERSION = 1


def _ok(calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [c for c in calls if not (c.get("output") or {}).get("refused")]


def build_appendix(calls: list[dict[str, Any]]) -> dict[str, Any]:
    ok = _ok(calls)
    counts: dict[str, int] = {}
    for c in ok:
        counts[c["tool"]] = counts.get(c["tool"], 0) + 1
    steps = [{**s, "calls": counts[s["name"]]} for s in steps_from(calls)]
    per: dict[str, dict[str, Any]] = {}
    cited: dict[tuple[Any, Any], list[str]] = {}
    expected: dict[str, Any] = {}
    for c in ok:
        inp, out = c.get("input") or {}, c.get("output") or {}
        if c["tool"] == "judge_element":
            cid = str(inp.get("contract_id"))
            row = per.setdefault(
                cid, {"contract_id": cid, "elements_judged": 0, "quotes_checked": 0, "quotes_valid": 0, "lean_agrees": None}
            )
            row["elements_judged"] += 1
            if out.get("quote"):
                cited.setdefault((out.get("fact_id"), out.get("quote")), []).append(cid)
        elif c["tool"] == "quote_check":
            for cid in cited.get((inp.get("fact_id"), inp.get("quote")), []):
                per[cid]["quotes_checked"] += 1
                per[cid]["quotes_valid"] += 1 if out.get("valid") is True else 0
        elif c["tool"] == "assemble":
            expected[str(out.get("contract_id"))] = out.get("expected_outcome")
        elif c["tool"] == "lean_check":
            cid = str(out.get("contract_id"))
            if cid in per:
                per[cid]["lean_agrees"] = expected.get(cid) == out.get("lean_outcome")
    return {
        "appendix_version": APPENDIX_VERSION,
        "steps": steps,
        "per_contract": list(per.values()),
        "refused_calls": len(calls) - len(ok),
    }
