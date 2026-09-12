"""S6: a beat may dispatch more than one independent criterion at once.

`requests.next_unmet` always names the single oldest open request, so one hard request could monopolise every
beat for hours while hundreds of other criteria waited (S5's own survey of the beat log: 13 of 22 beats on
2026-09-12 spent on one request, zero progress, zero others touched). `configs/limits.yaml`'s
`heartbeat.concurrent_dispatches` (default 2) lets a beat pick up to that many independent criteria - different
requests, path-disjoint via `swarm.plan` (the same conflict check a wave of objective steps already uses) - and
dispatch them together in one wave. `heartbeat.min_available_gb` (default 6) is the safety valve: each concurrent
dispatch spawns its own coding-agent process and its own MCP/plugin servers, read fresh against
`/proc/meminfo`'s `MemAvailable` every beat.

A width of exactly one (the default when the RAM floor narrows it, or when nothing else is eligible - the common
case) takes the identical single-task path the loop has always taken: this is asserted throughout the existing
`test_heartbeat.py` / `test_heartbeat_build_mode.py` / `test_criterion_judging.py` suites, none of which mock
`_effective_width` and all of which still pass unchanged with this feature live.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from pravrudhi.application import heartbeat, requests


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test User")
    (root / "README.md").write_text("hello\n")
    (root / "src").mkdir()  # `unbuildable` treats a `.py`/`.ts`/`.tsx` name as engine source without this
    (root / "src" / ".gitkeep").write_text("")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "initial")
    return root


class TestAvailableMemoryGb:
    def test_parses_mem_available_in_kb_to_gb(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        meminfo = tmp_path / "meminfo"
        meminfo.write_text("MemTotal:       32000000 kB\nMemAvailable:   10098644 kB\nMemFree: 1000 kB\n")
        monkeypatch.setattr(heartbeat, "_MEMINFO_PATH", meminfo)
        gb = heartbeat._available_memory_gb()
        assert gb is not None and gb == pytest.approx(10098644 / (1024 * 1024))

    def test_a_missing_file_is_none_not_a_crash(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(heartbeat, "_MEMINFO_PATH", tmp_path / "does-not-exist")
        assert heartbeat._available_memory_gb() is None

    def test_a_file_with_no_mem_available_line_is_none(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        meminfo = tmp_path / "meminfo"
        meminfo.write_text("MemTotal: 32000000 kB\n")
        monkeypatch.setattr(heartbeat, "_MEMINFO_PATH", meminfo)
        assert heartbeat._available_memory_gb() is None


class TestEffectiveWidth:
    def test_default_width_when_memory_is_ample(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(heartbeat.availability, "concurrent_dispatches", lambda: 2)
        monkeypatch.setattr(heartbeat.availability, "min_available_gb", lambda: 6.0)
        monkeypatch.setattr(heartbeat, "_available_memory_gb", lambda: 20.0)
        width, note = heartbeat._effective_width(tmp_path)
        assert (width, note) == (2, "")

    def test_configured_to_one_is_never_narrowed_or_noted(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(heartbeat.availability, "concurrent_dispatches", lambda: 1)
        # Memory is deliberately not mocked low here: width=1 must short-circuit before even checking it.
        monkeypatch.setattr(heartbeat, "_available_memory_gb", lambda: (_ for _ in ()).throw(AssertionError("checked")))
        width, note = heartbeat._effective_width(tmp_path)
        assert (width, note) == (1, "")

    def test_the_ram_floor_narrows_width_to_one_and_names_why(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(heartbeat.availability, "concurrent_dispatches", lambda: 2)
        monkeypatch.setattr(heartbeat.availability, "min_available_gb", lambda: 6.0)
        monkeypatch.setattr(heartbeat, "_available_memory_gb", lambda: 4.2)
        width, note = heartbeat._effective_width(tmp_path)
        assert width == 1
        assert "4.2" in note and "6.0" in note and "narrowed from 2 to 1" in note

    def test_unreadable_memory_narrows_to_one_and_names_why(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(heartbeat.availability, "concurrent_dispatches", lambda: 2)
        monkeypatch.setattr(heartbeat, "_available_memory_gb", lambda: None)
        width, note = heartbeat._effective_width(tmp_path)
        assert width == 1 and "could not be read" in note

    def test_memory_exactly_at_the_floor_is_not_narrowed(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(heartbeat.availability, "concurrent_dispatches", lambda: 2)
        monkeypatch.setattr(heartbeat.availability, "min_available_gb", lambda: 6.0)
        monkeypatch.setattr(heartbeat, "_available_memory_gb", lambda: 6.0)
        width, note = heartbeat._effective_width(tmp_path)
        assert (width, note) == (2, "")


class TestLastBeatRequests:
    def test_no_prior_beat_is_empty(self, tmp_path: Path) -> None:
        assert heartbeat._last_beat_requests(tmp_path) == frozenset()

    def test_a_single_dispatch_beat_names_its_one_request(self, tmp_path: Path) -> None:
        heartbeat._append(tmp_path, heartbeat.BeatRecord(
            at="2026-01-01T00:00:00Z", looked_at=(), chose={"request": "r-1", "criterion": "0"},
            reason="dispatched", result={"accepted": True},
        ))
        assert heartbeat._last_beat_requests(tmp_path) == frozenset({"r-1"})

    def test_a_multi_dispatch_beat_names_every_request(self, tmp_path: Path) -> None:
        heartbeat._append(tmp_path, heartbeat.BeatRecord(
            at="2026-01-01T00:00:00Z", looked_at=(),
            chose=[{"request": "r-1", "criterion": "0"}, {"request": "r-2", "criterion": "3"}],
            reason="dispatched 2", result={"kind": "batch", "dispatches": []},
        ))
        assert heartbeat._last_beat_requests(tmp_path) == frozenset({"r-1", "r-2"})

    def test_a_beat_that_named_no_request_is_empty(self, tmp_path: Path) -> None:
        heartbeat._append(tmp_path, heartbeat.BeatRecord(
            at="2026-01-01T00:00:00Z", looked_at=(), chose={"objective": "obj-a", "step": "finetune"},
            reason="no route", result=None,
        ))
        assert heartbeat._last_beat_requests(tmp_path) == frozenset()

    def test_only_the_most_recent_beat_counts(self, tmp_path: Path) -> None:
        heartbeat._append(tmp_path, heartbeat.BeatRecord(
            at="2026-01-01T00:00:00Z", looked_at=(), chose={"request": "r-old", "criterion": "0"},
            reason="dispatched", result={"accepted": True},
        ))
        heartbeat._append(tmp_path, heartbeat.BeatRecord(
            at="2026-01-01T01:00:00Z", looked_at=(), chose={"request": "r-new", "criterion": "0"},
            reason="dispatched", result={"accepted": True},
        ))
        assert heartbeat._last_beat_requests(tmp_path) == frozenset({"r-new"})


class TestMoreCandidatesRotation:
    def test_prefers_a_request_not_dispatched_last_beat(self, repo: Path) -> None:
        requests.capture(repo, "ask A", request_id="r-a", asked_at="2020-01-01T00:00:00Z",
                          criteria=[requests.Criterion(text="do thing a", source="operator")])
        requests.capture(repo, "ask B", request_id="r-b", asked_at="2020-01-02T00:00:00Z",
                          criteria=[requests.Criterion(text="do thing b", source="operator")])
        # r-a is the OLDER, and would win under plain oldest-first ordering, but it was dispatched last beat.
        picks = heartbeat._more_candidates(repo, 1, exclude_requests=set(), prior_requests=frozenset({"r-a"}))
        assert [p[0].id for p in picks] == ["r-b"]

    def test_falls_back_to_a_previously_dispatched_request_when_nothing_else_is_eligible(self, repo: Path) -> None:
        requests.capture(repo, "ask A", request_id="r-a",
                          criteria=[requests.Criterion(text="do thing a", source="operator")])
        picks = heartbeat._more_candidates(repo, 1, exclude_requests=set(), prior_requests=frozenset({"r-a"}))
        assert [p[0].id for p in picks] == ["r-a"]

    def test_never_picks_a_request_already_excluded_this_beat(self, repo: Path) -> None:
        requests.capture(repo, "ask A", request_id="r-a", asked_at="2020-01-01T00:00:00Z",
                          criteria=[requests.Criterion(text="do thing a", source="operator")])
        picks = heartbeat._more_candidates(repo, 1, exclude_requests={"r-a"}, prior_requests=frozenset())
        assert picks == []

    def test_stops_at_count_even_with_more_eligible(self, repo: Path) -> None:
        for i in range(3):
            requests.capture(repo, f"ask {i}", request_id=f"r-{i}", asked_at=f"2020-01-0{i + 1}T00:00:00Z",
                              criteria=[requests.Criterion(text=f"do thing {i}", source="operator")])
        picks = heartbeat._more_candidates(repo, 2, exclude_requests=set(), prior_requests=frozenset())
        assert len(picks) == 2


class TestSelectorAcrossRequests:
    """End-to-end through `_beat_obligations`: width 2, two different requests, dispatched in one wave."""

    def _fake_run_wave(self, seen: dict[str, Any]):
        def fake(build_agent: Any, wave: list[Any], **kw: Any) -> list[Any]:
            from pravrudhi.application.delegate import Verdict

            seen["task_ids"] = [t.spec.task_id for t in wave]
            # Deliberately returned in REVERSE of submission order: run_wave's real ordering is completion
            # order (as_completed), not submission order, and results must be matched back by task_id alone.
            return [
                Verdict(task_id=t.spec.task_id, agent="fake", accepted=True,
                        files=[f"proposals/requests/{t.spec.task_id.split(':')[1]}/{t.spec.task_id.split(':')[2]}/README.md"])
                for t in reversed(wave)
            ]
        return fake

    def test_two_independent_requests_dispatch_in_one_beat(self, repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        requests.capture(repo, "ask A", request_id="r-a", asked_at="2020-01-01T00:00:00Z",
                          criteria=[requests.Criterion(text="do thing a", source="operator")])
        requests.capture(repo, "ask B", request_id="r-b", asked_at="2020-01-02T00:00:00Z",
                          criteria=[requests.Criterion(text="do thing b", source="operator")])
        monkeypatch.setattr(heartbeat, "_effective_width", lambda root: (2, ""))
        seen: dict[str, Any] = {}
        monkeypatch.setattr(heartbeat.swarm, "run_wave", self._fake_run_wave(seen))

        chose, reason, result = heartbeat._beat_obligations(
            repo, lambda _n, _m=None: object(), judge=lambda **_kw: "VERDICT: not met\nnot yet",
        )

        assert set(seen["task_ids"]) == {"request:r-a:0", "request:r-b:0"}
        assert isinstance(chose, list) and len(chose) == 2
        assert {c["request"] for c in chose} == {"r-a", "r-b"}
        assert result is not None and result["kind"] == "batch" and len(result["dispatches"]) == 2
        assert "dispatched 2 independent criteria" in reason
        assert "r-a" in reason and "r-b" in reason

    def test_the_ram_floor_note_is_named_in_the_beats_own_reason(self, repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        requests.capture(repo, "ask A", request_id="r-a",
                          criteria=[requests.Criterion(text="do thing a", source="operator")])
        monkeypatch.setattr(
            heartbeat, "_effective_width",
            lambda root: (1, "MemAvailable 4.2GB is below the 6.0GB floor; narrowed from 2 to 1"),
        )
        seen: dict[str, Any] = {}
        monkeypatch.setattr(heartbeat.swarm, "run_wave", self._fake_run_wave(seen))

        _chose, reason, _result = heartbeat._beat_obligations(
            repo, lambda _n, _m=None: object(), judge=lambda **_kw: "VERDICT: not met\nnot yet",
        )

        assert "narrowed from 2 to 1" in reason
        assert len(seen["task_ids"]) == 1, "the floor must actually stop a second candidate being picked"

    def test_a_path_conflicting_second_candidate_is_left_for_a_future_beat(
        self, repo: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """`swarm.plan` puts a colliding task in a later wave; only wave 0 is dispatched this beat - the other
        request is simply not attempted, not an error."""
        requests.capture(repo, "ask A", request_id="r-a", asked_at="2020-01-01T00:00:00Z",
                          criteria=[requests.Criterion(
                              text="`tests/test_a.py` covers thing a", source="operator", mode="build")])
        requests.capture(repo, "ask B", request_id="r-b", asked_at="2020-01-02T00:00:00Z",
                          criteria=[requests.Criterion(
                              text="`tests/test_b.py` covers thing b", source="operator", mode="build")])
        monkeypatch.setattr(heartbeat, "_effective_width", lambda root: (2, ""))
        seen: dict[str, Any] = {}
        monkeypatch.setattr(heartbeat.swarm, "run_wave", self._fake_run_wave(seen))

        chose, _reason, _result = heartbeat._beat_obligations(
            repo, lambda _n, _m=None: object(), judge=lambda **_kw: "VERDICT: not met\nnot yet",
        )

        # Both criteria declare `tests/*` (dispatch_mode/build_paths_for widen to the directory glob), which
        # collide - swarm.plan defers one to a later wave, so only one is actually dispatched this beat.
        assert len(seen["task_ids"]) == 1
        assert not isinstance(chose, list)


class TestSingleDispatchIsUnchanged:
    """The width machinery must be inert whenever there is nothing more to add - the common case, since most
    test workspaces (and most real beats, per S5's own survey) have only one eligible request in play."""

    def test_a_lone_eligible_request_dispatches_exactly_as_before_even_with_width_two(
        self, repo: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        requests.capture(repo, "the only ask", request_id="r-1",
                          criteria=[requests.Criterion(text="do the thing", source="operator")])
        monkeypatch.setattr(heartbeat, "_effective_width", lambda root: (2, ""))

        def fake_run_wave(build_agent: Any, wave: list[Any], **kw: Any) -> list[Any]:
            from pravrudhi.application.delegate import Verdict

            assert len(wave) == 1, "nothing else was eligible; the wave must not invent a second task"
            return [Verdict(task_id=wave[0].spec.task_id, agent="fake", accepted=True,
                             files=["proposals/requests/r-1/0/README.md"])]

        monkeypatch.setattr(heartbeat.swarm, "run_wave", fake_run_wave)
        chose, reason, result = heartbeat._beat_obligations(
            repo, lambda _n, _m=None: object(), judge=lambda **_kw: "VERDICT: met\nfine",
        )

        assert chose == {"request": "r-1", "criterion": "0"}, "single-dispatch shape, not a one-element list"
        assert isinstance(result, dict) and "kind" not in result, "single-dispatch shape, not the batch wrapper"
        assert "dispatched 2 independent criteria" not in reason
