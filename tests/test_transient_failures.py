"""A seat that stumbled must be told apart from a seat that failed, and a spent seat must leave a trace.

Counted across all 157 outcomes in `.pravrudhi/routing.jsonl` on 2026-09-09:

  accepted        116
  rejected         41   wall_s min 0.0 · median 120.0 · max 1800.1, three at the wall-clock ceiling
  limited           0

Two faults in that table.

`limited` is zero not because no seat was ever rate limited — the Lite Plan was exhausted at the time of the
count — but because `swarm.run_wave` guards recording with `not limited`, so a quota event is excluded from the
record by construction. The guard was redundant: `routing.records` already drops limited rows so they cannot
move a route's trial count. Defence at the wrong layer cost the evidence instead of protecting it.

And the 41 rejections are one undifferentiated class. `availability.classify` returns `ok | limited | failed`,
so a connection reset, a 30-minute timeout and a genuinely bad answer are the same verdict: no fallback, and a
loss recorded against a route that may have done nothing wrong. Only `limited` reaches `_retry_elsewhere`, the
fallback chain that already exists and works. A transient fault should reach it too — try elsewhere rather than
pay the same wall clock again on the seat that just stumbled.

Spec: docs/superpowers/specs/2026-09-09-dispatch-cost-instrumentation.md, card M6.2.
"""

from __future__ import annotations

from pathlib import Path

from pravrudhi.application import availability, routing
from pravrudhi.application.routing import Outcome


class TestATransientFaultIsItsOwnClass:
    def test_a_network_stumble_is_transient_not_failed(self) -> None:
        for text in ("connection reset by peer",
                     "fetch failed: ECONNRESET",
                     "read ETIMEDOUT",
                     "502 Bad Gateway",
                     "503 Service Unavailable",
                     "upstream connect error",
                     "temporarily unavailable"):
            assert availability.classify("opencode:alibaba", text, 1) == "transient", text

    def test_a_usage_limit_still_outranks_a_transient_phrase(self) -> None:
        """A 429 is a limit, not a stumble, even though both are worth trying elsewhere: the cooldown differs."""
        assert availability.classify("opencode:alibaba", "429 rate limit, connection reset", 1) == "limited"

    def test_an_ordinary_bad_answer_is_still_failed(self) -> None:
        assert availability.classify("opencode:alibaba", "the tests did not pass", 1) == "failed"

    def test_success_is_untouched(self) -> None:
        assert availability.classify("opencode:alibaba", "all good", 0) == "ok"

    def test_an_unknown_agent_can_still_stumble(self) -> None:
        """Transient patterns are about the transport, not the vendor, so they are not keyed per agent."""
        assert availability.classify("some-new-agent", "connection reset by peer", 1) == "transient"

    def test_the_patterns_come_from_config_not_from_code(self) -> None:
        """Constants live in configs/ in this repository; a vendor phrase must be editable without a release."""
        assert availability.TRANSIENT_PATTERNS, "transient phrases must be loaded from limits.yaml"
        assert any("reset" in p.lower() for p in availability.TRANSIENT_PATTERNS)


class TestASpentSeatLeavesATrace:
    def test_a_limited_outcome_is_recorded(self, tmp_path: Path) -> None:
        routing.record_outcome(tmp_path, Outcome(
            tier="standard", route_id="qwen-lite-flash", task_id="t1", accepted=False, wall_s=3.0,
            at="2026-09-09T12:00:00Z", limited=True))
        (back,) = routing.outcomes(tmp_path)
        assert back.limited is True, "a quota event that leaves no trace cannot be counted or learned from"

    def test_a_limited_outcome_still_does_not_count_as_a_trial(self, tmp_path: Path) -> None:
        """Recording it must not undo the protection `records` provides: the route is not judged for being spent."""
        table = routing.load_table()
        route_id, route = next(iter(table.routes.items()))
        tier = route.tiers[0]
        rows = [
            Outcome(tier=tier, route_id=route_id, task_id="a", accepted=False, wall_s=1.0,
                    at="2026-09-09T12:00:00Z", limited=True),
            Outcome(tier=tier, route_id=route_id, task_id="b", accepted=True, wall_s=1.0,
                    at="2026-09-09T12:00:00Z", limited=False),
        ]
        record = next(r for r in routing.records(table, rows, tier) if r.route_id == route_id)
        assert record.trials == 1, "the limited dispatch must not appear as a trial"

    def test_limited_outcomes_are_countable_from_the_record(self, tmp_path: Path) -> None:
        for i, lim in enumerate((True, True, False)):
            routing.record_outcome(tmp_path, Outcome(
                tier="standard", route_id="qwen-lite-flash", task_id=f"t{i}", accepted=not lim, wall_s=1.0,
                at="2026-09-09T12:00:00Z", limited=lim))
        assert sum(1 for r in routing.outcomes(tmp_path) if r.limited) == 2


class TestTheWaveActsOnTheNewClass:
    """The two defects, at the layer where they actually live: `swarm.run_wave`."""

    @staticmethod
    def _task():
        from pravrudhi.application.delegate import TaskSpec
        from pravrudhi.application.swarm import SwarmTask
        return SwarmTask(TaskSpec("t1", "do a thing", ("x.py",), "true", 60), "standard", "why")

    @staticmethod
    def _factory(calls: list[str]):
        class _Agent:
            def __init__(self, name: str) -> None:
                self.name = name

        def build(name: str, model: str | None):
            calls.append(name)
            return _Agent(name)
        return build

    def test_a_spent_seat_now_leaves_a_row_in_the_record(self, tmp_path: Path, monkeypatch) -> None:
        """`run_wave` guarded recording with `not limited`, so no quota event was ever recorded — zero of 157.

        The guard was redundant: `routing.records` already drops limited rows so they cannot move a route's
        trial count. Defending at the recording layer discarded the evidence instead of protecting the statistic.
        """
        from pravrudhi.application.delegate import TaskSpec, Verdict
        from pravrudhi.application.swarm import run_wave

        def fake_dispatch(agent, spec: TaskSpec, *, log=print) -> Verdict:
            if agent.name == "opencode:alibaba-plan":
                return Verdict(spec.task_id, agent.name, False, ["Error: Requests rate limit exceeded"])
            return Verdict(spec.task_id, agent.name, True, [], wall_s=1.0)

        monkeypatch.setattr("pravrudhi.application.swarm.dispatch", fake_dispatch)
        run_wave(self._factory([]), [self._task()], log=lambda *_: None, root=tmp_path)

        limited = [o for o in routing.outcomes(tmp_path) if o.limited]
        assert limited, "a quota event that leaves no trace cannot be counted, cooled or learned from"
        assert limited[0].route_id, "and it must say which route was spent"

    def test_a_transient_fault_moves_the_work_elsewhere(self, tmp_path: Path, monkeypatch) -> None:
        """A 1800s timeout on the plan seat cost thirty minutes and produced nothing, then was chosen again.

        It is not a verdict on the route, so it must reach the same fallback chain a usage limit reaches rather
        than being recorded as an ordinary loss.
        """
        from pravrudhi.application.delegate import TaskSpec, Verdict
        from pravrudhi.application.swarm import run_wave

        dispatched: list[str] = []

        def fake_dispatch(agent, spec: TaskSpec, *, log=print) -> Verdict:
            dispatched.append(agent.name)
            if agent.name == "opencode:alibaba-plan":
                return Verdict(spec.task_id, agent.name, False,
                               ["agent exited non-zero: timeout after 1800s", "no change produced"])
            return Verdict(spec.task_id, agent.name, True, [], wall_s=1.0)

        monkeypatch.setattr("pravrudhi.application.swarm.dispatch", fake_dispatch)
        out = run_wave(self._factory([]), [self._task()], log=lambda *_: None, root=tmp_path)

        assert len(dispatched) > 1, "a stumble must not end the task on the seat that stumbled"
        assert out[0].accepted, out[0].reasons
        assert out[0].agent != "opencode:alibaba-plan"

    def test_a_transient_fault_holds_the_seat_only_briefly(self, tmp_path: Path, monkeypatch) -> None:
        """The account is fine, so an hour's cooldown would throw away a working route over one bad connection."""
        from pravrudhi.application.delegate import TaskSpec, Verdict
        from pravrudhi.application.swarm import run_wave

        def fake_dispatch(agent, spec: TaskSpec, *, log=print) -> Verdict:
            if agent.name == "opencode:alibaba-plan":
                return Verdict(spec.task_id, agent.name, False, ["fetch failed: ECONNRESET"])
            return Verdict(spec.task_id, agent.name, True, [], wall_s=1.0)

        monkeypatch.setattr("pravrudhi.application.swarm.dispatch", fake_dispatch)
        run_wave(self._factory([]), [self._task()], log=lambda *_: None, root=tmp_path)

        cooling = availability.cooling(tmp_path)
        assert cooling, "a stumbling seat is held out briefly so the next task does not walk into it"
        assert availability.transient_cooldown_minutes() < 10, "briefly, not for a quota's hour"
