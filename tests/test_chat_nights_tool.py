"""The assistant can be asked what a night did.

Asked "what did night 18 do, and what is the incumbent?", the bot answered — correctly and uselessly — that it
had no record. The engine's whole point is nights, and no tool reached them: the schema covered objectives,
recipes, routing, appetite and memory, and stopped there. The honesty pass then did its job and refused to
invent, which turned a missing tool into what looked like a missing result.
"""

from __future__ import annotations

from pathlib import Path

from pravrudhi.application import chat
from pravrudhi.application.memory_store import store_for


def test_nights_is_dispatchable_and_reads_the_ledger(tmp_path: Path) -> None:
    assert "nights" in chat.TOOL_NAMES
    call = chat.dispatch(Path("."), store_for(tmp_path, None), "nights", {})
    assert not call.refusal, call.refusal
    nights = call.result.get("nights")
    assert isinstance(nights, list) and nights, "the committed ledger has nights"
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
    for name in ("candidates", "agent_trace", "routing_seats"):
        assert name in chat.TOOL_NAMES, name
        call = chat.dispatch(Path("."), store_for(tmp_path, None), name, {})
        assert not call.refusal, (name, call.refusal)
        assert call.result_summary, name


def test_the_seats_tool_says_what_a_route_costs_and_whether_it_can_be_used(tmp_path: Path) -> None:
    """The most expensive fact the engine holds is which seat is spendable right now."""
    call = chat.dispatch(Path("."), store_for(tmp_path, None), "routing_seats", {})
    seats = call.result.get("seats")
    assert isinstance(seats, list) and seats
    for key in ("id", "relative_cost", "usable"):
        assert key in seats[0], seats[0]
