"""An objective file that vanishes after the directory listing (a concurrent writer) must not raise out of `load_all`, `problems`
or the chat tools; and the error for an unknown id echoes only a bounded part of the id."""

import threading
from pathlib import Path

import pytest

from pravrudhi.application import chat, objectives
from pravrudhi.application.memory_store import FileMemoryStore
from tests.test_chat import OBJECTIVE, _measured_workspace


def test_a_file_that_vanishes_after_the_glob_is_skipped_by_load_all(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _measured_workspace(tmp_path)
    real = objectives.load

    def vanishing(p: Path):  # noqa: ANN202
        p.unlink()
        return real(p)  # now raises FileNotFoundError

    monkeypatch.setattr(objectives, "load", vanishing)
    assert objectives.load_all(tmp_path) == []


def test_problems_ignores_a_vanished_file_and_reports_other_read_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _measured_workspace(tmp_path)
    real = objectives.load
    mode = {"m": "vanish"}

    def flaky(p: Path):  # noqa: ANN202
        if mode["m"] == "vanish":
            p.unlink()
        else:
            raise PermissionError("no read access")
        return real(p)

    monkeypatch.setattr(objectives, "load", flaky)
    assert objectives.problems(tmp_path) == []
    _measured_workspace(tmp_path)  # write the file again
    mode["m"] = "denied"
    assert objectives.problems(tmp_path) == [(f"{OBJECTIVE.id}.yaml", "no read access")]


def test_the_chat_tools_survive_a_file_toggling_under_a_writer(tmp_path: Path) -> None:
    """A writer removes and rewrites the objective file in a loop while the tools read: no exception ever escapes dispatch."""
    _measured_workspace(tmp_path)
    path = objectives.objectives_dir(tmp_path) / f"{OBJECTIVE.id}.yaml"
    body = path.read_text()
    stop = threading.Event()

    def writer() -> None:
        while not stop.is_set():
            path.unlink(missing_ok=True)
            path.write_text(body)

    t = threading.Thread(target=writer)
    t.start()
    store = FileMemoryStore(tmp_path / "memory")
    raised = 0
    try:
        for _ in range(400):
            for tool in ("objective_progress", "objective_plan", "objectives"):
                try:
                    chat.dispatch(tmp_path, store, tool, {"id": OBJECTIVE.id})
                except Exception:  # noqa: BLE001
                    raised += 1
    finally:
        stop.set()
        t.join()
    assert raised == 0


def test_the_unknown_id_error_echoes_a_bounded_part_of_the_id(tmp_path: Path) -> None:
    _measured_workspace(tmp_path)
    store = FileMemoryStore(tmp_path / "memory")
    for tool in ("objective_progress", "objective_plan"):
        inv = chat.dispatch(tmp_path, store, tool, {"id": "x" * 1_000_000})
        assert len(inv.result["error"]) < 200 and inv.result["error"].startswith("no objective named")
        short = chat.dispatch(tmp_path, store, tool, {"id": "nope"})
        assert short.result == {"error": "no objective named 'nope' in this workspace"}
