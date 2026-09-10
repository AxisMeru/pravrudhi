"""A procedure-transition graph, replayed read-only from the routing log.

arXiv:2609.09153 ("Procedural Graphs: Self-Evolving Execution Structures for LLM Agents", Lu et al.,
2026-09-08) represents an agent's task structure as `(procedure, relation, procedure)` triplets and lets an LLM
component rewrite the graph from observed trajectories. `docs/superpowers/specs/2026-09-09-dispatch-cost-
instrumentation.md` already surveyed the adjacent multi-agent-framework literature and refused the piece of it
that mutates shared state in place: "mutable state reducers (LangGraph) -- a destructive merge loses the
provenance the pramana tags exist to carry." A graph an LLM edits in place is the same shape of risk.

This module takes the triplet idea without the mutation: it never writes a graph, it *replays* one from
`.pravrudhi/routing.jsonl` on every call, exactly as `pravrudhi_kernel/ledger/replay.py` rebuilds citta from
the ledger rather than trusting a cached copy. A node is a route id that has appeared in the log. An edge is a
transition this engine actually made -- route A handled part of a task, then route B did -- aggregated with how
often it happened and how often the task was eventually accepted.

Two things this module is not, on purpose:

- Not evidence. `routing.py`'s own docstring already says the routing log "carries no pramana tag, it is not
  replayed [by the kernel], and no evidence document may cite it." This module reads that log, so the same
  limit applies to everything it returns: operational visibility, never a claim for `research/`.
- Not a control input. `ProcedureGraph.suggest_next` ranks the edges leaving a route; nothing in `swarm.py` or
  `routing.py` calls it. Wiring a ranking like this into dispatch is a bigger decision -- it would need its own
  spec card, the way M6.1 and M6.2/M6.3 each got one -- and is deliberately left undone here. See
  `docs/superpowers/specs/2026-09-10-agent-architecture-survey.md` for that follow-on scope.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from pravrudhi.application.routing import Outcome, outcomes


@dataclass(frozen=True)
class Edge:
    """An aggregated (procedure, relation, procedure) triplet: how often, and how it fared.

    `reason` names why the dispatcher moved off `from_route` rather than retrying it: `"limited"` (a vendor
    usage limit -- see `availability.classify`), `"stumbled_or_rejected"` (a transport fault or a rejected
    diff), or `"escalated"` (the prior route was itself accepted; this is a later, separate attempt of the same
    task_id, not a failure hop).
    """

    from_route: str
    to_route: str
    reason: str
    count: int
    accepted: int

    @property
    def success_rate(self) -> float:
        """Share of these transitions whose destination attempt was accepted. Zero trials reads as zero, not
        as an undefined ratio -- there is nothing to divide by, and `nan` would need a caller to special-case."""
        return self.accepted / self.count if self.count else 0.0


@dataclass(frozen=True)
class ProcedureGraph:
    """The dispatcher's observed behaviour, as a graph -- a report, never a control input.

    Rebuilt fresh by `build_graph` on every call. There is no `add_edge`, no mutation, no persisted copy: the
    routing log is the only thing that can add to what this graph says, matching the discipline the ledger's
    citta layer already applies.
    """

    nodes: frozenset[str]
    edges: tuple[Edge, ...]

    def outgoing(self, route_id: str) -> tuple[Edge, ...]:
        return tuple(e for e in self.edges if e.from_route == route_id)

    def suggest_next(self, route_id: str) -> tuple[Edge, ...]:
        """Edges leaving `route_id`, ranked by observed success rate then by how often it happened.

        A ranking to read, not a recommendation to act on -- nothing in `swarm.py` calls this. It exists so a
        person, or a later card that promotes this module from report to input, has something to rank against.
        """
        return tuple(sorted(self.outgoing(route_id), key=lambda e: (-e.success_rate, -e.count)))


def _reason(prev: Outcome) -> str:
    """Why the dispatcher moved off `prev`, which depends only on how `prev` ended."""
    if prev.limited:
        return "limited"
    if not prev.accepted:
        return "stumbled_or_rejected"
    return "escalated"


def build_graph(root: Path) -> ProcedureGraph:
    """Replay `.pravrudhi/routing.jsonl` into a procedure-transition graph.

    Outcomes are grouped by `task_id`, then ordered by `at` (ISO-8601 `Z`-suffixed, so lexical order is
    chronological order). Each consecutive pair of outcomes for the same task becomes one observed transition,
    unless both rows name the same route -- that is a same-seat retry, not a move between procedures, and
    `_retry_elsewhere` in `swarm.py` already accounts for it as a cooldown, not a graph edge. A task with only
    one recorded outcome contributes no edge: it never moved.
    """
    rows = outcomes(root)
    by_task: dict[str, list[Outcome]] = defaultdict(list)
    for o in rows:
        by_task[o.task_id].append(o)

    counts: dict[tuple[str, str, str], list[int]] = defaultdict(lambda: [0, 0])
    nodes: set[str] = set()
    for task_rows in by_task.values():
        ordered = sorted(task_rows, key=lambda o: o.at)
        for o in ordered:
            nodes.add(o.route_id)
        # `strict=False` on purpose: the sequences are deliberately unequal (a sliding pair over one
        # list), so `strict=True` -- the reflex fix for B905 -- would raise on every task.
        for prev, cur in zip(ordered, ordered[1:], strict=False):
            if prev.route_id == cur.route_id:
                continue
            key = (prev.route_id, cur.route_id, _reason(prev))
            bucket = counts[key]
            bucket[0] += 1
            if cur.accepted:
                bucket[1] += 1

    edges = tuple(
        Edge(from_route=f, to_route=t, reason=r, count=c, accepted=a)
        for (f, t, r), (c, a) in sorted(counts.items())
    )
    return ProcedureGraph(nodes=frozenset(nodes), edges=edges)


#: How many observations an edge needs before its success rate could tie-break anything.
#:
#: Not a taste threshold. `routing.choose` already aggregates the same log per route, so a transition edge only
#: earns a say if it says something the per-route rate does not -- and to distinguish two rates at all you need
#: enough trials that their intervals can fail to overlap. At n=5 a Wilson interval on a proportion spans
#: roughly 0.5; below that an edge cannot separate itself from the baseline whatever it observed.
TIEBREAK_MIN_COUNT = 5


def tiebreak_readiness(graph: ProcedureGraph, min_count: int = TIEBREAK_MIN_COUNT) -> dict[str, object]:
    """Whether this graph yet carries evidence that could tie-break a route choice, and what is missing.

    The question card M6.4 turns on, made runnable instead of argued. The graph is a RE-AGGREGATION of the
    same `routing.jsonl` that `routing.choose` already reads per route -- so it adds a different view, not new
    information, and the view only earns a vote once some transition has been seen often enough to distinguish
    itself from the destination's overall rate.

    Run this before proposing M6.4 again. On 2026-09-10 it answered `ready: False` with 3 edges at count 1
    apiece from 161 routing rows, which is why that card was deferred rather than built: a control input backed
    by single observations is the n=1 inference the sequential boundary exists to prevent everywhere else.
    """
    eligible = tuple(e for e in graph.edges if e.count >= min_count)
    return {
        "ready": bool(eligible),
        "min_count": min_count,
        "n_edges": len(graph.edges),
        "max_edge_count": max((e.count for e in graph.edges), default=0),
        "eligible_edges": tuple(f"{e.from_route}->{e.to_route}" for e in eligible),
        "why": (
            f"{len(eligible)} edge(s) at or above {min_count} observations"
            if eligible
            else f"no edge has reached {min_count} observations (best is "
            f"{max((e.count for e in graph.edges), default=0)}); a tie-breaker backed by that many trials "
            f"cannot separate itself from the per-route rate `choose` already uses"
        ),
    }
