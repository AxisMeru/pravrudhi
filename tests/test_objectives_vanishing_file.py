"""An objective file that vanishes after the directory listing (a concurrent writer) must not raise out of `load_all`, `problems`
or the chat tools; and the error for an unknown id echoes only a bounded part of the id."""

import json
import os
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


def test_problems_ignores_a_vanished_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _measured_workspace(tmp_path)
    real = objectives.load

    def vanishing(p: Path):  # noqa: ANN202
        p.unlink()
        return real(p)

    monkeypatch.setattr(objectives, "load", vanishing)
    assert objectives.problems(tmp_path) == []


@pytest.mark.skipif(os.geteuid() == 0, reason="a file permission is not enforced for root")
def test_an_unreadable_file_and_a_directory_named_like_one_are_reported_without_a_path(tmp_path: Path) -> None:
    """REAL files, no monkeypatch: an OSError message carries the absolute path; the reason must carry the cause only, in every
    place `problems` is read (the function, the chat tool, the GET /objectives body, the demo export)."""
    d = objectives.objectives_dir(tmp_path)
    d.mkdir(parents=True, exist_ok=True)
    unreadable = d / "unreadable.yaml"
    unreadable.write_text("intent: x\n")
    unreadable.chmod(0)
    (d / "adir.yaml").mkdir()
    try:
        found = dict(objectives.problems(tmp_path))
        assert set(found) == {"unreadable.yaml", "adir.yaml"}
        assert found["unreadable.yaml"] == "could not be read (Permission denied)"
        assert found["adir.yaml"] == "could not be read (Is a directory)"
        root = str(tmp_path)
        assert all(root not in r and "/" not in r for r in found.values())
        from pravrudhi.application.memory_store import FileMemoryStore

        inv = chat.dispatch(tmp_path, FileMemoryStore(tmp_path / "memory"), "objectives", {})
        assert inv.result["problems"] and root not in json.dumps(inv.result)
        from fastapi.testclient import TestClient

        from pravrudhi.api.server import create_app

        body = TestClient(create_app(tmp_path), base_url="http://127.0.0.1").get("/api/objectives").json()
        from pravrudhi.application.demo_export import build_demo

        demo = build_demo(tmp_path, product_root=None)["objectives"]
        assert {p["file"] for p in demo["problems"]} == {"unreadable.yaml", "adir.yaml"} and root not in json.dumps(demo)
        assert {p["file"] for p in body["problems"]} >= {"unreadable.yaml", "adir.yaml"} and root not in json.dumps(body)
    finally:
        unreadable.chmod(0o600)


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


def test_the_echoed_id_is_bounded_after_the_repr_for_characters_that_expand(tmp_path: Path) -> None:
    _measured_workspace(tmp_path)
    store = FileMemoryStore(tmp_path / "memory")
    for oid in ("\x00" * 90, "\u202e" * 100, "\\" * 100, "\n" * 100, "\U0001f600" * 100, "a" * 81, "a" * 80):
        for tool in ("objective_progress", "objective_plan"):
            err = chat.dispatch(tmp_path, store, tool, {"id": oid}).result["error"]
            assert len(err) <= len("no objective named ") + chat._SHOWN_REPR_CHARS + len("... in this workspace"), (
                oid[:3],
                len(err),
            )
    short = chat.dispatch(tmp_path, store, "objective_plan", {"id": "nope"}).result
    assert short == {"error": "no objective named 'nope' in this workspace"}
