"""Routing must spend cheaply until the log gives it a reason not to, and must never widen a route's permissions."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from pravrudhi.application import availability, routing
from pravrudhi.application.routing import (
    Outcome,
    RoutingError,
    choose,
    load_table,
    outcomes,
    record_outcome,
    records,
    report,
)

TABLE = {
    "minimum_trials": 3,
    "confidence": 0.95,
    "routes": [
        {"id": "cheap", "agent": "codex", "model": "m-cheap", "relative_cost": 1.0, "tiers": ["mechanical", "standard"]},
        {"id": "dear", "agent": "codex", "model": "m-dear", "relative_cost": 10.0,
         "tiers": ["mechanical", "standard", "critical"]},
        {"id": "banned", "agent": "orca:local", "model": "m-local", "relative_cost": 0.0, "tiers": []},
    ],
    "declared": {"mechanical": ["cheap", "dear"], "standard": ["cheap", "dear"], "critical": ["dear"]},
}


def _table(tmp_path: Path, doc: dict | None = None):
    p = tmp_path / "routing.yaml"
    p.write_text(yaml.safe_dump(doc or TABLE))
    return load_table(p)


def _rows(tier: str, route: str, wins: int, losses: int) -> list[Outcome]:
    return [Outcome(tier, route, f"t{i}", True, 10.0) for i in range(wins)] + [
        Outcome(tier, route, f"f{i}", False, 10.0) for i in range(losses)
    ]


def test_a_free_route_that_never_works_is_permitted_at_no_tier(tmp_path: Path) -> None:
    """The local model costs nothing and, on the measured evidence, does nothing. Cost alone must not select it."""
    t = _table(tmp_path)
    for tier in ("mechanical", "standard", "critical"):
        assert "banned" not in [r.id for r in t.permitted(tier)]
        assert choose(t, [], tier).route.id != "banned"


def test_with_no_evidence_the_declared_order_decides_and_says_so(tmp_path: Path) -> None:
    c = choose(_table(tmp_path), [], "mechanical")
    assert c.route.id == "cheap"
    assert "declared order" in c.reason


def test_a_cheap_route_is_tried_while_it_is_still_unmeasured(tmp_path: Path) -> None:
    t = _table(tmp_path)
    c = choose(t, _rows("mechanical", "dear", 5, 0), "mechanical")
    assert c.route.id == "cheap", "an unmeasured cheaper route is worth a trial before paying ten times"
    assert "too few to rule out" in c.reason


def test_a_cheap_route_that_is_clearly_worse_loses(tmp_path: Path) -> None:
    t = _table(tmp_path)
    rows = _rows("mechanical", "cheap", 0, 12) + _rows("mechanical", "dear", 12, 0)
    c = choose(t, rows, "mechanical")
    assert c.route.id == "dear"
    assert "best measured success rate" in c.reason


def test_a_cheap_route_that_merely_looks_worse_keeps_the_work(tmp_path: Path) -> None:
    """With few trials almost nothing is distinguishable, and that is the point: the expensive route has to earn
    the spend, not merely lead."""
    t = _table(tmp_path)
    rows = _rows("mechanical", "cheap", 3, 1) + _rows("mechanical", "dear", 4, 0)
    c = choose(t, rows, "mechanical")
    assert c.route.id == "cheap"
    assert "not yet justified" in c.reason


def test_evidence_at_one_tier_does_not_leak_to_another(tmp_path: Path) -> None:
    t = _table(tmp_path)
    rows = _rows("mechanical", "cheap", 0, 12)
    assert choose(t, rows, "standard").route.id == "cheap", "a failure at one tier says nothing about another"


def test_a_tier_with_no_permitted_route_is_an_error_not_a_silent_default(tmp_path: Path) -> None:
    doc = {**TABLE, "declared": {**TABLE["declared"], "design": []}}
    with pytest.raises(RoutingError):
        choose(_table(tmp_path, doc), [], "design")


def test_a_declared_order_naming_an_unknown_route_is_refused_at_load(tmp_path: Path) -> None:
    doc = {**TABLE, "declared": {**TABLE["declared"], "mechanical": ["cheap", "ghost"]}}
    with pytest.raises(RoutingError) as e:
        _table(tmp_path, doc)
    assert "ghost" in str(e.value)


def test_the_log_round_trips_and_survives_a_corrupt_line(tmp_path: Path) -> None:
    record_outcome(tmp_path, Outcome("standard", "cheap", "task-a", True, 12.5))
    (tmp_path / ".pravrudhi" / "routing.jsonl").open("a").write("{not json\n")
    record_outcome(tmp_path, Outcome("standard", "cheap", "task-b", False, 8.0))
    rows = outcomes(tmp_path)
    assert [o.task_id for o in rows] == ["task-a", "task-b"]
    assert rows[0].at, "every outcome is stamped"


def test_the_routing_log_is_not_the_ledger(tmp_path: Path) -> None:
    """It records what the engine spent on its own upkeep, not what an experiment measured, so it must not land in
    research/ where the evidence lives."""
    record_outcome(tmp_path, Outcome("standard", "cheap", "t", True, 1.0))
    assert (tmp_path / ".pravrudhi" / "routing.jsonl").exists()
    assert not (tmp_path / "research").exists()


def test_records_include_a_route_with_no_trials(tmp_path: Path) -> None:
    rs = records(_table(tmp_path), _rows("standard", "cheap", 2, 0), "standard")
    by = {r.route_id: r for r in rs}
    assert by["cheap"].trials == 2 and by["cheap"].measured
    assert by["dear"].trials == 0 and not by["dear"].measured


def test_the_shipped_table_reserves_the_dearest_model_for_the_hardest_work() -> None:
    """Permission, not selection. The router deliberately tries a cheaper route first at every tier, critical
    included, so asserting that critical *picks* the dearest route would contradict the whole design; what must
    hold is that the cheap tiers cannot reach it at all."""
    t = load_table()
    dearest = max(t.routes.values(), key=lambda r: r.relative_cost)
    for tier in ("mechanical", "standard"):
        assert dearest.id not in [r.id for r in t.permitted(tier)], f"{tier} can reach the dearest route"
    assert dearest.id in [r.id for r in t.permitted("critical")], "the dearest route must be available somewhere"
    for tier in ("mechanical", "standard", "design", "critical"):
        assert choose(t, [], tier).route.relative_cost <= dearest.relative_cost


def test_the_hosted_sentinel_is_permitted_everywhere_but_always_last() -> None:
    """The free-tier single-shot route is the sentinel: it takes over, it does not compete.

    It was permitted only at the mechanical tier once, which meant that when the paid accounts hit their usage
    limits at the tiers where the real work happens there was nothing to fall back to and the loop simply
    stopped. The operator's requirement of 2026-09-06 is that the system runs unattended when the paid models
    are spent, so it is permitted at every tier and declared last above mechanical.

    Adding a real Qwen tool loop beside it does not replace it. The loop is a separate route that can itself be
    spent or broken, and collapsing the two would remove the fallback while appearing to improve it.
    """
    t = routing.load_table()
    assert t.routes["qwen-coder"].agent == "hosted"
    for tier in ("mechanical", "standard", "design", "critical"):
        ids = [r.id for r in t.permitted(tier)]
        assert "qwen-coder" in ids, tier
    for tier in ("standard", "design", "critical"):
        assert [r.id for r in t.permitted(tier)][-1] == "qwen-coder", tier


def test_the_verified_alibaba_loop_carries_load_where_it_was_measured() -> None:
    """One verified tool call admits the loop to the tiers it was checked at, and no further."""
    t = routing.load_table()
    loop = t.routes["qwen-loop"]
    assert loop.agent == "opencode:alibaba"
    for tier in ("mechanical", "standard"):
        assert loop.id in [r.id for r in t.permitted(tier)], tier
    for tier in ("design", "critical"):
        assert loop.id not in [r.id for r in t.permitted(tier)], tier


def test_choose_drops_a_cooling_route_and_says_so(tmp_path: Path) -> None:
    doc = {
        "minimum_trials": 3,
        "confidence": 0.95,
        "routes": [
            {"id": "codex-cheap", "agent": "codex", "model": "m", "relative_cost": 1.0, "tiers": ["mechanical"]},
            {"id": "claude-dear", "agent": "claude-code", "model": "m", "relative_cost": 5.0, "tiers": ["mechanical"]},
        ],
        "declared": {"mechanical": ["codex-cheap", "claude-dear"]},
    }
    t = _table(tmp_path, doc)
    availability.mark_limited(tmp_path, "codex", minutes=60)
    c = choose(t, [], "mechanical", root=tmp_path)
    assert c.route.id == "claude-dear", "the only non-cooling route must be picked even though it costs more"
    assert "dropped cooling route" in c.reason


def test_choose_without_root_ignores_cooldowns(tmp_path: Path) -> None:
    """A caller that does not pass `root` gets the old, cooldown-blind behaviour -- required so every existing call
    site that has not been updated to pass `root` keeps choosing exactly as it did before."""
    t = _table(tmp_path)
    availability.mark_limited(tmp_path, "codex", minutes=60)
    c = choose(t, [], "mechanical")
    assert c.route.id == "cheap"


def test_report_covers_every_tier(tmp_path: Path) -> None:
    rows = report(tmp_path, _table(tmp_path))
    assert {r["tier"] for r in rows} == {"mechanical", "standard", "critical"}
    assert all(r.get("reason") for r in rows)


def test_the_toughest_tier_leads_with_astra() -> None:
    """The operator asked that astra handle the tough jobs; it had been listed last at that tier.

    Being permitted at a tier is not the same as being preferred there. With no track record the declared order
    decides, so astra behind opus and sonnet meant it was reached only once both were measured worse or cooling
    from a rate limit — a last resort, which is the inverse of the instruction.
    """
    table = routing.load_table()
    assert [r.id for r in table.permitted("critical")][0] == "astra"
    assert "astra" in [r.id for r in table.permitted("design")]


class TestAStatedPreferenceOutranksCost:
    """The operator said astra should handle the tough jobs; the chooser kept picking sonnet anyway.

    Declared order only breaks ties before there is evidence. Once a route has a track record the chooser prefers
    the cheapest whose interval overlaps the best, which is right in general and wrong when the operator has
    named the model for that work. Listing astra first was not enough — being permitted is not being preferred.
    """

    def test_the_named_route_wins_its_tier_regardless_of_cost(self, tmp_path: Path) -> None:
        table = routing.load_table()
        assert table.preferred("critical") == "astra"
        choice = routing.choose(table, routing.outcomes(tmp_path), "critical")
        assert choice.route.id == "astra"
        assert "declared for this tier by the operator" in choice.reason

    def test_a_tier_with_no_preference_still_chooses_on_evidence(self, tmp_path: Path) -> None:
        table = routing.load_table()
        assert table.preferred("standard") is None
        choice = routing.choose(table, routing.outcomes(tmp_path), "standard")
        assert choice.route.id != "astra"

    def test_a_preferred_route_that_is_cooling_yields_to_the_ordinary_rules(self, tmp_path: Path) -> None:
        table = routing.load_table()
        availability.mark_limited(tmp_path, "codex")
        choice = routing.choose(table, routing.outcomes(tmp_path), "critical", root=tmp_path)
        assert choice.route.id != "astra", "a preference must not send work to an account that is rate limited"
