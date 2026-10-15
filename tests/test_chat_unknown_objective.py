"""An unknown objective id from the model is a plain tool error, never an exception (the chat tools `objective_progress` and
`objective_plan`)."""

from pathlib import Path

import pytest

from pravrudhi.application import chat
from pravrudhi.application.memory_store import FileMemoryStore
from tests.test_chat import OBJECTIVE, _measured_workspace


def _store(root: Path) -> FileMemoryStore:
    return FileMemoryStore(root / "memory")


@pytest.mark.parametrize("tool", ["objective_progress", "objective_plan"])
def test_an_unknown_objective_id_is_a_plain_error_result(tmp_path: Path, tool: str) -> None:
    _measured_workspace(tmp_path)
    root = tmp_path
    inv = chat.dispatch(root, _store(root), tool, {"id": "no-such-objective"})
    assert inv.result == {"error": "no objective named 'no-such-objective' in this workspace"}


@pytest.mark.parametrize("tool", ["objective_progress", "objective_plan"])
def test_an_empty_or_missing_id_is_the_same_plain_error(tmp_path: Path, tool: str) -> None:
    _measured_workspace(tmp_path)
    root = tmp_path
    for args in ({}, {"id": ""}, {"id": None}):
        inv = chat.dispatch(root, _store(root), tool, args)
        assert "error" in inv.result and "no objective named" in inv.result["error"]


def test_a_known_id_still_answers(tmp_path: Path) -> None:
    _measured_workspace(tmp_path)
    root = tmp_path
    inv = chat.dispatch(root, _store(root), "objective_progress", {"id": OBJECTIVE.id})
    assert inv.result["objective"] == OBJECTIVE.id and "progress" in inv.result
    plan = chat.dispatch(root, _store(root), "objective_plan", {"id": OBJECTIVE.id})
    assert plan.result["objective"] == OBJECTIVE.id and "error" not in plan.result


def test_an_objective_that_vanishes_between_the_two_reads_is_the_same_plain_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The summary read finds the id but the plan read no longer does (a concurrent edit): this used to raise StopIteration."""
    _measured_workspace(tmp_path)
    root = tmp_path
    monkeypatch.setattr(chat, "_objective_summary", lambda _root, oid: {"progress": {}, "id": oid})
    inv = chat.dispatch(root, _store(root), "objective_plan", {"id": "vanished"})
    assert inv.result == {"error": "no objective named 'vanished' in this workspace"}
