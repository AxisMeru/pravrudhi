"""The assistant can be asked what a night did.

Asked "what did night 18 do, and what is the incumbent?", the bot answered — correctly and uselessly — that it
had no record. The engine's whole point is nights, and no tool reached them: the schema covered objectives,
recipes, routing, appetite and memory, and stopped there. The honesty pass then did its job and refused to
invent, which turned a missing tool into what looked like a missing result.
"""

from __future__ import annotations

import json
from pathlib import Path

from pravrudhi.application import chat
from pravrudhi.application.memory_store import store_for


def _ledger_at(root: Path) -> Path:
    """A two-event ledger of one night, written where the tool will look for it.

    These tests used to dispatch against `Path(".")` and assert "the committed ledger has nights". The ledger is
    not committed - `research/` is gitignored - so they passed on a machine that had run a night and failed in
    CI, which is the direction that wastes a day: local `make smoke` was green while `main` was red.
    """
    ledger = root / "research" / "ledger.jsonl"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    base = {
        "epoch": 0, "night": 18, "cycle": None, "kind": "audit", "actor": "kernel",
        "candidate_id": None, "surface": None, "bucket": None, "provenance": None,
        "kernel_release": "0.1.0", "prev_hash": "0" * 64, "this_hash": "1" * 64,
    }
    rows = [
        {**base, "seq": 1, "t": "2026-09-05T02:00:00.000Z",
         "payload": {"kind": "night_start", "track": "lora", "selection_policy": "efe", "incumbent": "c-0045"}},
        {**base, "seq": 2, "t": "2026-09-05T03:00:00.000Z",
         "payload": {"kind": "night_end", "track": "lora", "spent_gpu_h": 1.25,
                     "outcomes": {"c-0101": "pruned", "c-0102": "promoted"}}},
    ]
    ledger.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return ledger



def test_nights_is_dispatchable_and_reads_the_ledger(tmp_path: Path) -> None:
    assert "nights" in chat.TOOL_NAMES
    _ledger_at(tmp_path)
    call = chat.dispatch(tmp_path, store_for(tmp_path, None), "nights", {})
    assert not call.refusal, call.refusal
    nights = call.result.get("nights")
    assert isinstance(nights, list) and nights, "the fixture ledger has one night"
    latest = nights[-1]
    for key in ("night", "candidates", "pruned", "promoted", "spent_gpu_h"):
        assert key in latest, latest
    assert call.result_summary


def test_an_unknown_tool_is_still_refused(tmp_path: Path) -> None:
    """Adding a tool must not weaken the closed set."""
    call = chat.dispatch(Path("."), store_for(tmp_path, None), "ledger", {})
    assert call.refusal


def test_the_assistant_can_reach_candidates_the_trace_and_the_seats(tmp_path: Path) -> None:
    """Three questions with answers in the workspace that the assistant could not reach.

    `nights` was added after the bot answered "I have no record of what night 18 did" — correctly, because no
    tool read the ledger's night rows, and the honesty pass then refused to invent one. The same hole remained
    for candidates, for what the agents actually did, and for which routes are spendable. Each is a direct read
    of a function `demo_export` already calls, so no new data path is created.
    """
    _ledger_at(tmp_path)
    for name in ("candidates", "agent_trace", "routing_seats"):
        assert name in chat.TOOL_NAMES, name
        call = chat.dispatch(tmp_path, store_for(tmp_path, None), name, {})
        assert not call.refusal, (name, call.refusal)
        assert call.result_summary, name


def test_the_seats_tool_says_what_a_route_costs_and_whether_it_can_be_used(tmp_path: Path) -> None:
    """The most expensive fact the engine holds is which seat is spendable right now."""
    call = chat.dispatch(tmp_path, store_for(tmp_path, None), "routing_seats", {})
    seats = call.result.get("seats")
    assert isinstance(seats, list) and seats
    for key in ("id", "relative_cost", "usable"):
        assert key in seats[0], seats[0]
