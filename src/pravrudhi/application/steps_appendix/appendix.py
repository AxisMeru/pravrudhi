"""The steps appendix of an assisted run: counts and verdicts read from the recorded calls, nothing else (#416).

`build_appendix(calls)` takes the driver's recorded calls ({tool, input, output} in call order) and returns what the screening
did: the stages in the order first seen with how many times each ran, the quote and Lean checks as the existing `steps_from`
reports them, and per contract the counts of elements judged, quotes checked, quotes found valid, and whether the Lean check
agreed with the engine's own assembly. It is rendered ONLY from the calls: no text from the agent's messages reaches it, no
outcome or reason is computed here, and refused calls are excluded and counted. The contract ids it carries are the ones the
driver already validated (a call naming any other contract is refused, so it is excluded). A malformed call is skipped.
The memo shows it as one list.
"""

from __future__ import annotations

from typing import Any

from pravrudhi.application.a0_assist.assist import steps_from

APPENDIX_VERSION = 1


def _well_formed(c: Any) -> bool:
    return (
        isinstance(c, dict)
        and isinstance(c.get("tool"), str)
        and isinstance(c.get("input") or {}, dict)
        and isinstance(c.get("output") or {}, dict)
    )


def _ok(calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [c for c in calls if _well_formed(c) and not (c.get("output") or {}).get("refused")]


def build_appendix(calls: list[dict[str, Any]]) -> dict[str, Any]:
    good = [c for c in calls if _well_formed(c)]
    ok = _ok(calls)
    counts: dict[str, int] = {}
    for c in ok:
        counts[c["tool"]] = counts.get(c["tool"], 0) + 1
    steps = [{**s, "calls": counts.get(s["name"], 0)} for s in steps_from(ok)]
    per: dict[str, dict[str, Any]] = {}
    cited: dict[tuple[Any, Any], set[str]] = {}
    expected: dict[str, Any] = {}
    for c in ok:
        inp, out = c.get("input") or {}, c.get("output") or {}
        cid = inp.get("contract_id") if isinstance(inp.get("contract_id"), str) else out.get("contract_id")
        if c["tool"] == "judge_element" and isinstance(cid, str):
            row = per.setdefault(
                cid, {"contract_id": cid, "elements_judged": 0, "quotes_checked": 0, "quotes_valid": 0, "lean_agrees": None}
            )
            row["elements_judged"] += 1
            if out.get("quote"):
                cited.setdefault((out.get("fact_id"), out.get("quote")), set()).add(cid)
        elif c["tool"] == "quote_check":
            for owner in sorted(cited.get((inp.get("fact_id"), inp.get("quote")), set())):
                per[owner]["quotes_checked"] += 1
                per[owner]["quotes_valid"] += 1 if out.get("valid") is True else 0
        elif c["tool"] == "assemble" and isinstance(cid, str):
            expected[cid] = out.get("expected_outcome")
        elif c["tool"] == "lean_check" and isinstance(cid, str) and cid in per and cid in expected:
            per[cid]["lean_agrees"] = expected[cid] == out.get("lean_outcome")
    return {
        "appendix_version": APPENDIX_VERSION,
        "steps": steps,
        "per_contract": list(per.values()),
        "refused_calls": len(good) - len(ok),
    }


def safe_appendix(calls: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The appendix, or None when it cannot be built: a finished screening result is never lost to its appendix."""
    try:
        return build_appendix(calls)
    except Exception:  # noqa: BLE001
        return None
