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
