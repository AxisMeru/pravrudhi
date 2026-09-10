"""A procedure-transition graph replayed from the routing log, never mutated in place.

The scenario mirrors a real strand already on disk: `qwen-lite-max` timed out twice on the same task
(`request:r-5795501a:8`, 2026-09-09) before `sonnet` picked it up and the diff was accepted. That is one
`"limited"`-then-`"stumbled_or_rejected"`-shaped hop repeated, plus a same-route retry that must not itself
become an edge.
"""

from __future__ import annotations

import json
from pathlib import Path

from pravrudhi.application.procedure_graph import build_graph


def _write(root: Path, rows: list[dict]) -> None:
    p = root / ".pravrudhi" / "routing.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")


def _row(task_id: str, route_id: str, at: str, *, accepted: bool, limited: bool = False) -> dict:
    return {"tier": "standard", "route_id": route_id, "task_id": task_id, "accepted": accepted,
            "wall_s": 1.0, "at": at, "limited": limited}


def test_no_log_is_an_empty_graph_not_an_error(tmp_path: Path) -> None:
    g = build_graph(tmp_path)
    assert g.nodes == frozenset()
    assert g.edges == ()


def test_a_single_outcome_contributes_no_edge(tmp_path: Path) -> None:
    _write(tmp_path, [_row("t1", "sonnet", "2026-09-09T18:00:00Z", accepted=True)])
    g = build_graph(tmp_path)
    assert g.nodes == frozenset({"sonnet"})
    assert g.edges == ()


def test_same_route_retry_is_not_a_transition(tmp_path: Path) -> None:
    # Two attempts on the same seat -- a cooldown-and-retry, which `swarm._retry_elsewhere` already accounts
    # for elsewhere. It must not appear as a self-loop edge here.
    _write(tmp_path, [
        _row("t1", "sonnet", "2026-09-09T18:00:00Z", accepted=False),
        _row("t1", "sonnet", "2026-09-09T18:05:00Z", accepted=True),
    ])
    g = build_graph(tmp_path)
    assert g.edges == ()


def test_a_limited_hop_is_recorded_with_its_reason_and_outcome(tmp_path: Path) -> None:
    # The real shape on disk 2026-09-09: qwen-lite-max hit its limit, sonnet finished the task.
    _write(tmp_path, [
        _row("request:r-5795501a:8", "qwen-lite-max", "2026-09-09T17:45:03Z", accepted=False, limited=True),
        _row("request:r-5795501a:8", "sonnet", "2026-09-09T18:48:09Z", accepted=True),
    ])
    g = build_graph(tmp_path)
    assert g.nodes == frozenset({"qwen-lite-max", "sonnet"})
    assert len(g.edges) == 1
    edge = g.edges[0]
    assert (edge.from_route, edge.to_route, edge.reason) == ("qwen-lite-max", "sonnet", "limited")
    assert edge.count == 1
    assert edge.accepted == 1
    assert edge.success_rate == 1.0


def test_a_stumble_is_distinguished_from_a_usage_limit(tmp_path: Path) -> None:
    _write(tmp_path, [
        _row("t2", "haiku", "2026-09-09T10:00:00Z", accepted=False, limited=False),
        _row("t2", "sonnet", "2026-09-09T10:05:00Z", accepted=False),
    ])
    g = build_graph(tmp_path)
    assert g.edges[0].reason == "stumbled_or_rejected"
    assert g.edges[0].accepted == 0
    assert g.edges[0].success_rate == 0.0


def test_escalation_between_two_accepted_attempts_is_its_own_reason(tmp_path: Path) -> None:
    # Same task_id, two genuinely separate accepted attempts (e.g. a follow-up criterion on the same request) --
    # not a failure hop.
    _write(tmp_path, [
        _row("t3", "haiku", "2026-09-09T09:00:00Z", accepted=True),
        _row("t3", "sonnet", "2026-09-09T09:30:00Z", accepted=True),
    ])
    g = build_graph(tmp_path)
    assert g.edges[0].reason == "escalated"


def test_edges_aggregate_across_tasks_and_suggest_next_ranks_by_success_then_count(tmp_path: Path) -> None:
    _write(tmp_path, [
        _row("t4", "qwen-lite-max", "2026-09-09T01:00:00Z", accepted=False, limited=True),
        _row("t4", "sonnet", "2026-09-09T01:05:00Z", accepted=True),
        _row("t5", "qwen-lite-max", "2026-09-09T02:00:00Z", accepted=False, limited=True),
        _row("t5", "astra", "2026-09-09T02:05:00Z", accepted=False),
        _row("t6", "qwen-lite-max", "2026-09-09T03:00:00Z", accepted=False, limited=True),
        _row("t6", "sonnet", "2026-09-09T03:05:00Z", accepted=True),
    ])
    g = build_graph(tmp_path)
    ranked = g.suggest_next("qwen-lite-max")
    assert [e.to_route for e in ranked] == ["sonnet", "astra"]
    sonnet_edge = ranked[0]
    assert sonnet_edge.count == 2 and sonnet_edge.accepted == 2 and sonnet_edge.success_rate == 1.0


def test_a_corrupt_log_line_does_not_blind_the_graph(tmp_path: Path) -> None:
    # `routing.outcomes` already tolerates a corrupt line; this pins that the graph inherits that tolerance
    # rather than crashing on a half-written row from a killed process.
    p = tmp_path / ".pravrudhi" / "routing.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a") as fh:
        fh.write("{not json\n")
        fh.write(json.dumps(_row("t7", "sonnet", "2026-09-09T00:00:00Z", accepted=True)) + "\n")
    g = build_graph(tmp_path)
    assert g.nodes == frozenset({"sonnet"})
