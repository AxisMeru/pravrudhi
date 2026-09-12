"""ADR-0053 §6: convergence, not liveness, is a loop's health metric.

The operator's instruction of 2026-09-12 is that every lead status opens with, per loop, the criteria closed and
proposals accepted in the trailing 24 h, the last beat time, and the current failure mode -- read from the
records, never estimated. Until now nothing in the engine computed that; the figures were produced by an ad-hoc
script, and on 2026-09-12 evening that script silently undercounted every loop's met criteria for a full evening
because it read `result["judged"]` at the top level only. A beat that dispatches several criteria writes
`{"kind": "batch", "dispatches": [...]}` (`heartbeat.py`'s multi-dispatch branch), so each criterion's verdict is
nested one level down and a top-level read sees nothing. Studio's convergence was understated by about 30%.

The batch case is therefore the first test here, not an afterthought: it is the shape that actually broke.
"""

from __future__ import annotations

import json
from pathlib import Path

from pravrudhi.application import heartbeat


def _beat(at: str, result: dict | None) -> dict:
    return {
        "at": at, "looked_at": [], "chose": None, "reason": "", "result": result,
        "drive": None, "drive_deficit": None, "sentence": "",
    }


def _write(root: Path, beats: list[dict]) -> None:
    path = heartbeat.log_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(b, sort_keys=True) + "\n" for b in beats))


class TestConvergenceCountsNestedBatchDispatches:
    """The regression that motivated this module: a met criterion inside a batch beat must be counted."""

    def test_a_met_criterion_inside_a_batch_beat_is_counted(self, tmp_path: Path) -> None:
        from pravrudhi.application.convergence import convergence

        _write(tmp_path, [
            _beat("2026-09-12T10:00:00Z", {
                "kind": "batch",
                "dispatches": [
                    {"accepted": True, "judged": "met"},
                    {"accepted": True, "judged": "not met"},
                ],
            }),
        ])
        c = convergence(tmp_path, hours=24, now="2026-09-12T12:00:00Z")
        assert c.met == 1, "a met criterion nested in a batch beat must be counted, not skipped"
        assert c.not_met == 1
        assert c.dispatches == 2, "a two-wide beat is two dispatches, not one"
        assert c.beats == 1

    def test_a_single_dispatch_beat_still_counts_once(self, tmp_path: Path) -> None:
        from pravrudhi.application.convergence import convergence

        _write(tmp_path, [_beat("2026-09-12T10:00:00Z", {"accepted": True, "judged": "met"})])
        c = convergence(tmp_path, hours=24, now="2026-09-12T12:00:00Z")
        assert (c.met, c.dispatches, c.beats) == (1, 1, 1)


class TestConvergenceWindowAndShape:
    def test_beats_outside_the_window_are_excluded(self, tmp_path: Path) -> None:
        from pravrudhi.application.convergence import convergence

        _write(tmp_path, [
            _beat("2026-09-11T09:00:00Z", {"accepted": True, "judged": "met"}),   # 27 h before `now`
            _beat("2026-09-12T10:00:00Z", {"accepted": True, "judged": "met"}),
        ])
        c = convergence(tmp_path, hours=24, now="2026-09-12T12:00:00Z")
        assert c.met == 1, "only the beat inside the trailing window counts"
        assert c.beats == 1

    def test_a_beat_that_dispatched_nothing_is_a_beat_but_not_a_dispatch(self, tmp_path: Path) -> None:
        """Quiet hours, a parked request and a rebase conflict all write a beat with result None or empty.
        Liveness must still be visible (the loop ticked) without inflating the dispatch count."""
        from pravrudhi.application.convergence import convergence

        _write(tmp_path, [
            _beat("2026-09-12T10:00:00Z", None),
            _beat("2026-09-12T11:00:00Z", {}),
        ])
        c = convergence(tmp_path, hours=24, now="2026-09-12T12:00:00Z")
        assert c.beats == 2
        assert c.dispatches == 0
        assert (c.met, c.accepted) == (0, 0)

    def test_last_beat_is_reported_even_when_nothing_converged(self, tmp_path: Path) -> None:
        from pravrudhi.application.convergence import convergence

        _write(tmp_path, [
            _beat("2026-09-12T10:00:00Z", None),
            _beat("2026-09-12T11:30:00Z", None),
        ])
        c = convergence(tmp_path, hours=24, now="2026-09-12T12:00:00Z")
        assert c.last_beat == "2026-09-12T11:30:00Z"

    def test_no_log_at_all_is_zeroes_not_a_crash(self, tmp_path: Path) -> None:
        from pravrudhi.application.convergence import convergence

        c = convergence(tmp_path, hours=24, now="2026-09-12T12:00:00Z")
        assert (c.beats, c.dispatches, c.met, c.last_beat) == (0, 0, 0, None)

    def test_a_corrupt_line_is_skipped_not_fatal(self, tmp_path: Path) -> None:
        """Same tolerance `history` already has -- a truncated write must not blind the health metric."""
        from pravrudhi.application.convergence import convergence

        path = heartbeat.log_path(tmp_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(_beat("2026-09-12T10:00:00Z", {"accepted": True, "judged": "met"})) + "\n"
            + '{"at": "truncated\n'
        )
        c = convergence(tmp_path, hours=24, now="2026-09-12T12:00:00Z")
        assert c.met == 1


class TestConvergenceFailureMode:
    """A zero on criteria closed outranks every card, so the report must say WHY it is zero, not only that it is."""

    def test_zero_met_with_dispatches_reports_judged_not_met(self, tmp_path: Path) -> None:
        from pravrudhi.application.convergence import convergence

        _write(tmp_path, [_beat("2026-09-12T10:00:00Z", {"accepted": True, "judged": "not met"})])
        c = convergence(tmp_path, hours=24, now="2026-09-12T12:00:00Z")
        assert c.met == 0
        assert c.failure_mode is not None and "not met" in c.failure_mode

    def test_zero_met_with_no_accepted_dispatch_reports_that_instead(self, tmp_path: Path) -> None:
        """The product loop's 2026-09-12 state: dispatching, nothing accepted. A different failure from
        'accepted but judged not met', and the remedy differs, so the report must not conflate them."""
        from pravrudhi.application.convergence import convergence

        _write(tmp_path, [_beat("2026-09-12T10:00:00Z", {"accepted": False, "reasons": ["no change produced"]})])
        c = convergence(tmp_path, hours=24, now="2026-09-12T12:00:00Z")
        assert c.met == 0
        assert c.failure_mode is not None and "accepted" in c.failure_mode

    def test_converging_loop_has_no_failure_mode(self, tmp_path: Path) -> None:
        from pravrudhi.application.convergence import convergence

        _write(tmp_path, [_beat("2026-09-12T10:00:00Z", {"accepted": True, "judged": "met"})])
        c = convergence(tmp_path, hours=24, now="2026-09-12T12:00:00Z")
        assert c.failure_mode is None
