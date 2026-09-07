"""When a paid account is spent, the free-tier sentinel must carry the work.

The operator's requirement of 2026-09-06: "let pravrudhi system be robust enough to completely work irrespective
of paid models hitting session limits (run 24x7)". Before this, a usage limit was recorded as an ordinary failure:
the route lost a point it did not deserve, the task died, and the loop stopped until a person noticed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pravrudhi.application import availability, continuity, routing
from pravrudhi.application.delegate import TaskSpec, Verdict
from pravrudhi.application.swarm import SwarmTask, run_wave


class _Agent:
    """An agent that reports a vendor usage-limit message, or succeeds, depending on how it was built."""

    def __init__(self, name: str, *, limited: bool) -> None:
        self.name = name
        self._limited = limited

    def run(self, prompt: str, workspace: Path, timeout_s: int = 1800) -> Any:  # pragma: no cover - unused
        raise AssertionError("run_wave must go through dispatch, which is patched in these tests")


def _factory(calls: list[str]) -> Any:
    def build(name: str, model: str | None) -> Any:
        calls.append(name)
        return _Agent(name, limited=(name == "claude-code"))

    return build


def test_a_usage_limit_moves_the_task_to_the_sentinel(tmp_path: Path, monkeypatch: Any) -> None:
    dispatched: list[str] = []

    def fake_dispatch(agent: Any, spec: TaskSpec, *, log: Any = print) -> Verdict:
        dispatched.append(agent.name)
        if agent._limited:
            return Verdict(spec.task_id, agent.name, False, ["Claude usage limit reached. Your limit will reset"])
        return Verdict(spec.task_id, agent.name, True, [], wall_s=1.0)

    monkeypatch.setattr("pravrudhi.application.swarm.dispatch", fake_dispatch)
    calls: list[str] = []
    task = SwarmTask(TaskSpec("t1", "do a thing", ("x.py",), "true", 60), "standard", "why")

    out = run_wave(_factory(calls), [task], log=lambda *_: None, root=tmp_path)

    # The requirement is that work continues when the paid account is spent, not that one particular free route
    # serves it. This asserted `hosted` because the single-shot writer was the only free route that existed. A
    # verified Qwen tool loop now sits ahead of it, which is a better fallback for agentic work and leaves the
    # sentinel behind it for when the loop is itself spent or broken.
    assert len(out) == 1
    assert out[0].accepted, out[0].reasons
    assert out[0].agent == "opencode:alibaba-plan", f"a free route should have taken over, got {out[0].agent}"
    assert dispatched == ["claude-code", "opencode:alibaba-plan"], dispatched


def test_the_single_shot_sentinel_still_stands_behind_the_loop(tmp_path: Path, monkeypatch: Any) -> None:
    """If the tool loop is spent too, the one-shot writer is what keeps the night moving."""
    dispatched: list[str] = []

    def fake_dispatch(agent: Any, spec: TaskSpec, *, log: Any = print) -> Verdict:
        dispatched.append(agent.name)
        if agent.name == "claude-code":
            return Verdict(spec.task_id, agent.name, False, ["Claude usage limit reached. Your limit will reset"])
        if agent.name.startswith("opencode:alibaba"):
            # What DashScope actually says when a quota is spent, not a Claude message wearing its name. Both
            # Alibaba routes are spent here: the Lite Plan and the free tier have separate quotas, so exhausting
            # one says nothing about the other and the chain has to walk past both.
            return Verdict(spec.task_id, agent.name, False, ["Error: Requests rate limit exceeded"])
        return Verdict(spec.task_id, agent.name, True, [], wall_s=1.0)

    monkeypatch.setattr("pravrudhi.application.swarm.dispatch", fake_dispatch)
    task = SwarmTask(TaskSpec("t1", "do a thing", ("x.py",), "true", 60), "standard", "why")

    out = run_wave(_factory([]), [task], log=lambda *_: None, root=tmp_path)

    assert out[0].accepted, out[0].reasons
    assert out[0].agent == "hosted", f"the sentinel should be the last resort, got {out[0].agent}"
    assert dispatched == ["claude-code", "opencode:alibaba-plan", "opencode:alibaba", "hosted"], dispatched


def test_the_limited_route_is_cooled_and_not_blamed(tmp_path: Path, monkeypatch: Any) -> None:
    def fake_dispatch(agent: Any, spec: TaskSpec, *, log: Any = print) -> Verdict:
        if agent._limited:
            return Verdict(spec.task_id, agent.name, False, ["Claude usage limit reached"])
        return Verdict(spec.task_id, agent.name, True, [], wall_s=1.0)

    monkeypatch.setattr("pravrudhi.application.swarm.dispatch", fake_dispatch)
    task = SwarmTask(TaskSpec("t2", "do a thing", ("x.py",), "true", 60), "standard", "why")
    run_wave(_factory([]), [task], log=lambda *_: None, root=tmp_path)

    assert "claude-code" in availability.cooling(tmp_path), "the spent account must cool down"

    # a limit is not evidence about quality, so no losing outcome may be recorded against the route
    outcomes = routing.outcomes(tmp_path)
    assert not [o for o in outcomes if o.route_id == "sonnet" and not o.accepted], outcomes

    kinds = [e.kind for e in continuity.entries(tmp_path, 10)]
    assert "limited" in kinds and "fallback" in kinds, kinds


def test_a_sentinel_never_wins_on_price(tmp_path: Path) -> None:
    """A standby must not be chosen simply for being cheap.

    Declaring it last was not enough. The chooser prefers the cheapest route whose interval overlaps the best,
    and a free route with no trials is always cheapest and never rules itself out, so the sentinel was picked for
    four complex multi-file tasks that a single-shot file writer cannot do.
    """
    table = routing.load_table()
    rows = routing.outcomes(tmp_path)
    for tier in ("mechanical", "standard", "design", "critical"):
        choice = routing.choose(table, rows, tier)
        assert not choice.route.sentinel, f"{tier} chose the standby {choice.route.id}: {choice.reason}"


def test_the_sentinel_takes_over_when_every_ordinary_route_is_cooling(tmp_path: Path) -> None:
    table = routing.load_table()
    for route in table.permitted("critical"):
        if not route.sentinel:
            availability.mark_limited(tmp_path, route.agent)
    choice = routing.choose(table, routing.outcomes(tmp_path), "critical", root=tmp_path)
    assert choice.route.sentinel, choice.reason
    assert "takes over" in choice.reason


class TestARealUsageLimitIsRecognised:
    """Astra hit its limit for real and four tasks were recorded as having failed on their merits.

    The rejection reason carries the first 200 characters of a 2000-character tail. A coding agent echoes the
    prompt it was given and then, much later, prints why it stopped — so "you've hit your usage limit" was
    always beyond the slice the classifier saw. The fallback existed, was tested, and could not fire on the one
    failure it was built for, because the evidence never reached it.
    """

    def test_a_limit_announced_at_the_end_of_a_long_run_is_still_seen(self) -> None:
        from pravrudhi.application.availability import classify

        # What codex actually prints: the brief first, the reason last.
        output = ("Give the desktop application the six things it is for. " * 60) + (
            "\nERROR: You've hit your usage limit. Upgrade to Pro or try again at 3:51 PM."
        )
        assert len(output) > 2000, "the point of this test is that the message is far past any short slice"
        assert classify("codex", output, 1) == "limited"
        assert classify("codex", output[:200], 1) != "limited", (
            "this is the slice the old code classified, and it is why the fallback never fired"
        )

    def test_the_verdict_carries_the_decision_rather_than_the_swarm_reinferring_it(self) -> None:
        from pravrudhi.application.delegate import Verdict
        from pravrudhi.application.swarm import _hit_a_usage_limit

        spent = Verdict("t", "codex", False, ["agent exited non-zero: ...prompt echo..."], limited=True)
        assert _hit_a_usage_limit("codex", spent)

        broken = Verdict("t", "codex", False, ["validation failed"], limited=False)
        assert not _hit_a_usage_limit("codex", broken), "a real failure must not be mistaken for a spent account"

    def test_an_accepted_run_is_never_treated_as_limited(self) -> None:
        from pravrudhi.application.delegate import Verdict
        from pravrudhi.application.swarm import _hit_a_usage_limit

        assert not _hit_a_usage_limit("codex", Verdict("t", "codex", True, [], limited=True))
