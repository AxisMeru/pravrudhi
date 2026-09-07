"""Nights, dispatches and workflow runs are start-and-walk-away work; a person should not have to sit and poll a
page to learn one finished. `notifications.py` is the record of what happened while nobody was watching."""

from __future__ import annotations

from pathlib import Path

from pravrudhi.application.notifications import (
    MAX_NOTIFICATIONS,
    emit,
    log_path,
    mark_read,
    recent,
    unread,
)


class TestEmitAndReadRoundTrip:
    def test_emitted_notifications_are_unread_newest_first(self, tmp_path: Path) -> None:
        first = emit(tmp_path, kind="run_finished", title="model night 3 finished", detail="", ref="/runs/abc")
        second = emit(tmp_path, kind="job_accepted", title='"tidy the docs" was accepted', ref="/swarm")

        rows = unread(tmp_path)
        assert [r.id for r in rows] == [second.id, first.id]
        assert all(not r.read for r in rows)

    def test_mark_read_by_id_leaves_the_rest_unread(self, tmp_path: Path) -> None:
        first = emit(tmp_path, kind="criterion_met", title="criterion met", ref="/requests")
        second = emit(tmp_path, kind="criterion_met", title="another criterion met", ref="/requests")

        mark_read(tmp_path, [first.id])

        still_unread = unread(tmp_path)
        assert [r.id for r in still_unread] == [second.id]
        rows = {r.id: r for r in recent(tmp_path, 10)}
        assert rows[first.id].read is True
        assert rows[second.id].read is False

    def test_mark_read_ignores_an_unknown_id(self, tmp_path: Path) -> None:
        note = emit(tmp_path, kind="job_rejected", title="rejected", ref="/swarm")
        mark_read(tmp_path, ["not-a-real-id"])
        assert unread(tmp_path)[0].id == note.id
        assert unread(tmp_path)[0].read is False

    def test_recent_respects_n(self, tmp_path: Path) -> None:
        for i in range(5):
            emit(tmp_path, kind="job_accepted", title=f"job {i}", ref="/swarm")
        assert len(recent(tmp_path, 3)) == 3

    def test_no_notifications_yet_is_an_empty_list_not_an_error(self, tmp_path: Path) -> None:
        assert recent(tmp_path, 50) == []
        assert unread(tmp_path) == []


class TestCorruptLine:
    def test_a_corrupt_line_is_skipped_not_fatal(self, tmp_path: Path) -> None:
        good = emit(tmp_path, kind="run_finished", title="a night finished", ref="/runs/x")
        path = log_path(tmp_path)
        with path.open("a") as fh:
            fh.write("not json at all\n")
            fh.write("\n")  # a blank line must also not blow up the reader
        emit(tmp_path, kind="run_finished", title="another night finished", ref="/runs/y")

        rows = recent(tmp_path, 50)
        assert len(rows) == 2
        assert {r.title for r in rows} == {"a night finished", "another night finished"}
        assert good.id in {r.id for r in rows}


class TestCapHolds:
    def test_the_log_never_grows_past_the_cap(self, tmp_path: Path) -> None:
        for i in range(MAX_NOTIFICATIONS + 20):
            emit(tmp_path, kind="job_accepted", title=f"job {i}", ref="/swarm")

        rows = recent(tmp_path, MAX_NOTIFICATIONS * 2)
        assert len(rows) == MAX_NOTIFICATIONS
        # the oldest were the ones dropped, so only the last MAX_NOTIFICATIONS survive
        assert rows[0].title == f"job {MAX_NOTIFICATIONS + 19}"
        assert rows[-1].title == "job 20"


class TestSecretShapedStrings:
    def test_a_provider_key_shaped_title_is_redacted(self, tmp_path: Path) -> None:
        secret = "sk-ant-abcdefghijklmnopqrstuvwxyz0123456789"
        note = emit(tmp_path, kind="job_rejected", title=f"failed with key {secret}", detail=secret, ref="/swarm")

        assert secret not in note.title
        assert secret not in note.detail
        stored = recent(tmp_path, 1)[0]
        assert secret not in stored.title
        assert secret not in stored.detail
        assert secret not in log_path(tmp_path).read_text()

    def test_a_hex_looking_ref_is_redacted(self, tmp_path: Path) -> None:
        secret = "abcdef0123456789abcdef0123456789"  # 32 hex chars, matches the credentials hex pattern
        note = emit(tmp_path, kind="run_finished", title="ok", ref=f"/runs/{secret}")
        assert secret not in note.ref
