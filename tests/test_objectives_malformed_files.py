"""A malformed or half-written objective file is skipped and reported (never an exception out of the readers), and `write` is
atomic. Invented data only."""

import os
from pathlib import Path

import pytest
import yaml

from pravrudhi.application import chat, objectives
from pravrudhi.application.memory_store import FileMemoryStore
from tests.test_chat import OBJECTIVE, _measured_workspace

BAD_TEXTS = {
    "empty": b"",
    "list": b"- a\n- b\n",
    "scalar": b"just a string\n",
    "number": b"42\n",
    "benchmarks_str": b"intent: x\ntrack: t\nbenchmarks: nope\n",
    "benchmarks_ints": b"intent: x\ntrack: t\nbenchmarks: [1, 2]\n",
    "benchmark_no_id": b"intent: x\ntrack: t\nbenchmarks:\n  - metric: acc\n",
    "target_not_number": b"intent: x\ntrack: t\ntarget_delta: abc\nbenchmarks:\n  - {id: b, metric: acc}\n",
    "recipes_int": b"intent: x\ntrack: t\nrecipes: 5\nbenchmarks:\n  - {id: b, metric: acc}\n",
    "bad_utf8": b"intent: \xff\xfe\xfa\n",
    "nul": b"intent: x\x00y\n",
    "truncated": b"intent: x\ntrack: t\nbenchmarks:\n  - {id: b, metr",
    "deep": b"[" * 20000,
}


def _write_bad(root: Path, name: str, data: bytes) -> None:
    d = objectives.objectives_dir(root)
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.yaml").write_bytes(data)


@pytest.mark.parametrize("kind", sorted(BAD_TEXTS))
def test_a_malformed_file_is_skipped_and_reported_without_a_path(tmp_path: Path, kind: str) -> None:
    _measured_workspace(tmp_path)
    _write_bad(tmp_path, "bad-one", BAD_TEXTS[kind])
    assert [o.id for o in objectives.load_all(tmp_path)] == [OBJECTIVE.id]  # the good file still loads
    found = dict(objectives.problems(tmp_path))
    assert "bad-one.yaml" in found and str(tmp_path) not in found["bad-one.yaml"] and found["bad-one.yaml"]


@pytest.mark.parametrize("kind", sorted(BAD_TEXTS))
def test_the_chat_tools_and_summaries_survive_a_malformed_file(tmp_path: Path, kind: str) -> None:
    _measured_workspace(tmp_path)
    _write_bad(tmp_path, "bad-one", BAD_TEXTS[kind])
    store = FileMemoryStore(tmp_path / "memory")
    for tool in ("objectives", "objective_progress", "objective_plan"):
        chat.dispatch(tmp_path, store, tool, {"id": OBJECTIVE.id})  # no exception


def test_a_good_file_is_unchanged(tmp_path: Path) -> None:
    _measured_workspace(tmp_path)
    (obj,) = objectives.load_all(tmp_path)
    assert obj.id == OBJECTIVE.id and obj.benchmarks == OBJECTIVE.benchmarks and obj.intent == OBJECTIVE.intent


def test_write_is_atomic_and_leaves_no_temporary_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    objectives.write(tmp_path, OBJECTIVE)
    d = objectives.objectives_dir(tmp_path)
    assert sorted(p.name for p in d.iterdir()) == [f"{OBJECTIVE.id}.yaml"]
    before = (d / f"{OBJECTIVE.id}.yaml").read_text()

    def broken(*_a: object, **_k: object) -> str:
        raise RuntimeError("disk full")

    monkeypatch.setattr(yaml, "safe_dump", broken)
    with pytest.raises(RuntimeError):
        objectives.write(tmp_path, OBJECTIVE)
    assert (d / f"{OBJECTIVE.id}.yaml").read_text() == before  # the old file is intact
    assert sorted(p.name for p in d.iterdir()) == [f"{OBJECTIVE.id}.yaml"]


def test_a_crash_while_writing_never_leaves_a_half_file_at_the_target(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    objectives.write(tmp_path, OBJECTIVE)
    d = objectives.objectives_dir(tmp_path)
    before = (d / f"{OBJECTIVE.id}.yaml").read_text()

    def killed(_fd: int) -> None:  # the process "dies" after the temporary file is written, before the rename
        raise RuntimeError("killed")

    monkeypatch.setattr(os, "fsync", killed)
    with pytest.raises(RuntimeError):
        objectives.write(tmp_path, OBJECTIVE)
    monkeypatch.undo()
    assert (d / f"{OBJECTIVE.id}.yaml").read_text() == before
    assert sorted(p.name for p in d.iterdir()) == [f"{OBJECTIVE.id}.yaml"]  # and the temporary file is removed


def test_concurrent_writers_of_one_objective_never_fail_and_readers_never_see_a_partial_file(tmp_path: Path) -> None:
    import threading

    objectives.write(tmp_path, OBJECTIVE)
    path = objectives.objectives_dir(tmp_path) / f"{OBJECTIVE.id}.yaml"
    stop = threading.Event()
    errors: list[BaseException] = []
    empties = [0]

    def writer() -> None:
        try:
            for _ in range(200):
                objectives.write(tmp_path, OBJECTIVE)
        except BaseException as e:  # noqa: BLE001
            errors.append(e)

    def reader() -> None:
        while not stop.is_set():
            try:
                if not path.read_text():
                    empties[0] += 1
            except FileNotFoundError:
                empties[0] += 1

    threads = [threading.Thread(target=writer) for _ in range(8)]
    r = threading.Thread(target=reader)
    r.start()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    stop.set()
    r.join()
    assert errors == [] and empties[0] == 0
    assert sorted(p.name for p in path.parent.iterdir()) == [path.name]  # no temporary file left


def test_the_mode_of_an_existing_file_is_kept_and_a_symlink_stays_a_symlink(tmp_path: Path) -> None:
    objectives.write(tmp_path, OBJECTIVE)
    path = objectives.objectives_dir(tmp_path) / f"{OBJECTIVE.id}.yaml"
    path.chmod(0o600)
    objectives.write(tmp_path, OBJECTIVE)
    assert (path.stat().st_mode & 0o777) == 0o600
    real = path.with_name("real-copy.yml")
    real.write_text(path.read_text())
    path.unlink()
    path.symlink_to(real)
    objectives.write(tmp_path, OBJECTIVE)
    assert path.is_symlink() and real.read_text()
