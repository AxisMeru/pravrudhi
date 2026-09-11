"""A vendor usage limit must be told apart from an ordinary failure, and remembered for a while so the engine does
not walk straight back into it on the very next dispatch."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import yaml

from pravrudhi.application import availability, routing


def test_every_configured_vendor_phrase_classifies_as_limited() -> None:
    for agent_id, phrases in availability.LIMIT_PATTERNS.items():
        for phrase in phrases:
            assert availability.classify(agent_id, f"noise before {phrase} noise after", 1) == "limited"
            # case must not matter: vendors do not promise a consistent case in their own output
            assert availability.classify(agent_id, phrase.upper(), 1) == "limited"


def test_an_ordinary_success_and_an_ordinary_failure_are_not_limited() -> None:
    assert availability.classify("claude-code", "diff applied", 0) == "ok"
    assert availability.classify("claude-code", "SyntaxError: unexpected token", 1) == "failed"


def test_a_cooldown_expires(tmp_path: Path) -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    availability.mark_limited(tmp_path, "claude-code", minutes=30, now=now)
    assert availability.is_cool(tmp_path, "claude-code", now=now + timedelta(minutes=10))
    assert not availability.is_cool(tmp_path, "claude-code", now=now + timedelta(minutes=31))


def test_cooling_lists_only_agents_still_inside_their_window(tmp_path: Path) -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    availability.mark_limited(tmp_path, "claude-code", minutes=60, now=now)
    availability.mark_limited(tmp_path, "codex", minutes=1, now=now)
    later = now + timedelta(minutes=5)
    assert availability.cooling(tmp_path, now=later) == {"claude-code": availability.cooling(tmp_path, now=now)["claude-code"]}


def test_clear_ends_a_cooldown_early(tmp_path: Path) -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    availability.mark_limited(tmp_path, "hosted", minutes=60, now=now)
    assert availability.is_cool(tmp_path, "hosted", now=now)
    availability.clear(tmp_path, "hosted")
    assert not availability.is_cool(tmp_path, "hosted", now=now)


def test_mark_limited_uses_the_configured_default_cooldown_per_agent(tmp_path: Path) -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    availability.mark_limited(tmp_path, "claude-code", now=now)  # no explicit minutes: falls back to config
    availability.mark_limited(tmp_path, "hosted", now=now)
    assert availability.is_cool(tmp_path, "claude-code", now=now + timedelta(minutes=200))
    assert not availability.is_cool(tmp_path, "hosted", now=now + timedelta(minutes=90))


def test_usable_routes_drops_a_cooling_agent_and_keeps_the_rest(tmp_path: Path) -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    table = routing.load_table()
    routes = table.permitted("critical")
    cooling_route = next(r for r in routes if r.agent == "claude-code")
    availability.mark_limited(tmp_path, cooling_route.agent, minutes=60, now=now)
    usable = availability.usable_routes(tmp_path, routes, now=now)
    assert cooling_route.id not in [r.id for r in usable]
    assert {r.agent for r in usable}.isdisjoint({cooling_route.agent})
    assert len(usable) == len(routes) - sum(1 for r in routes if r.agent == cooling_route.agent)


def test_all_cooling_still_returns_a_route(tmp_path: Path) -> None:
    # No fixed `now` here: `choose` has no way to be told what "now" is, so it always checks cooldowns against the
    # real clock, and the cooldowns marked below must still be active when it does.
    table = routing.load_table()
    routes = table.permitted("critical")
    for agent in {r.agent for r in routes}:
        availability.mark_limited(tmp_path, agent, minutes=60)
    usable = availability.usable_routes(tmp_path, routes)
    assert usable == []
    choice = routing.choose(table, [], "critical", root=tmp_path)
    assert choice.route.id in [r.id for r in routes]
    assert "forcing" in choice.reason and "cooling down" in choice.reason


def test_a_limited_outcome_does_not_move_the_success_rate(tmp_path: Path) -> None:
    doc = {
        "minimum_trials": 3,
        "confidence": 0.95,
        "routes": [{"id": "cheap", "agent": "claude-code", "model": "m", "relative_cost": 1.0, "tiers": ["standard"]}],
        "declared": {"standard": ["cheap"]},
    }

    p = tmp_path / "routing.yaml"
    p.write_text(yaml.safe_dump(doc))
    table = routing.load_table(p)

    rows = [routing.Outcome("standard", "cheap", "a", True, 1.0), routing.Outcome("standard", "cheap", "b", True, 1.0)]
    limited = routing.Outcome("standard", "cheap", "c", False, 1.0, limited=True)

    before = routing.records(table, rows, "standard")[0]
    after = routing.records(table, [*rows, limited], "standard")[0]
    assert before.trials == after.trials == 2
    assert before.rate == after.rate == 1.0


def test_recording_a_limited_outcome_starts_a_cooldown(tmp_path: Path) -> None:
    table = routing.load_table()
    route = table.routes["sonnet"]
    routing.record_outcome(
        tmp_path, routing.Outcome("standard", route.id, "t", False, 1.0, limited=True), table=table
    )
    assert availability.is_cool(tmp_path, route.agent)

def test_a_long_cooldown_is_offered_again_after_the_reprobe_span(tmp_path: Path) -> None:
    """2026-09-11: the Lite Plan quota was reset by hand on the console while the router held a reset time it
    had parsed three days earlier. What the vendor said was true when it said it; the only way to learn of an
    early reset is to try, once per span."""
    now = datetime(2026, 9, 11, 12, tzinfo=UTC)
    availability.mark_limited(tmp_path, "opencode:alibaba-plan", now=now, until=now + timedelta(days=3))
    span = timedelta(hours=availability.reprobe_hours())
    assert availability.is_cool(tmp_path, "opencode:alibaba-plan", now=now + span / 2)
    assert not availability.is_cool(tmp_path, "opencode:alibaba-plan", now=now + span)
    # the probe failed and the vendor still names the same day: held for another span from that answer
    availability.mark_limited(tmp_path, "opencode:alibaba-plan", now=now + span, until=now + timedelta(days=3))
    assert availability.is_cool(tmp_path, "opencode:alibaba-plan", now=now + span + timedelta(hours=1))


def test_a_short_cooldown_is_simply_honoured(tmp_path: Path) -> None:
    now = datetime(2026, 9, 11, 12, tzinfo=UTC)
    availability.mark_limited(tmp_path, "claude-code", minutes=90, now=now)
    assert availability.is_cool(tmp_path, "claude-code", now=now + timedelta(minutes=89))
    assert not availability.is_cool(tmp_path, "claude-code", now=now + timedelta(minutes=91))


def test_a_bare_until_written_before_the_rule_still_reads_and_is_due_for_a_retry(tmp_path: Path) -> None:
    (tmp_path / ".pravrudhi").mkdir()
    (tmp_path / ".pravrudhi" / "agent_cooldown.json").write_text(
        '{"opencode:alibaba-plan": "2026-09-14T16:14:00Z", "hosted": "2026-09-11T17:28:43Z"}'
    )
    now = datetime(2026, 9, 11, 17, tzinfo=UTC)
    cooling = availability.cooling(tmp_path, now=now)
    assert "hosted" in cooling, "a short window with no mark is still honoured until it ends"
    assert "opencode:alibaba-plan" not in cooling, "a days-long window with no mark is older than any span"
