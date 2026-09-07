"""Read back the parent the engine has been writing down and never once consulted.

Every candidate this project has proposed carries a `lineage` field, set to `[incumbent_id]` when the candidate is
built. Nothing reads it. The consequence is visible the moment the real ledger is folded: 189 proposals, 188 of
them carrying a lineage, every one of length one, and exactly two distinct parents across the entire recorded
history. The search is a two-node star. It is a careful, well-measured hill climb from one point, and then from a
second point after a promotion moved it.

That matters because two selection arms were written against ancestry and have never received any. `gear_scores`
ranks a candidate by the ordinal standing of its nearest archived ancestor; `hgm_scores` shrinks a candidate's
posterior toward its lineage's, weighted by how much evidence the lineage carries. Both walk a parent chain
through `_ancestors`, both take the map as an optional argument, and both fall back to a stated degenerate rule
when it is absent — `gear` calls every unmeasured candidate equally novel, `hgm` shrinks toward a leave-one-out
archive mean. Their docstrings ask for the real thing in as many words.

This module is that map, and nothing more. It folds propose rows into `child -> parent`, which is the shape
`_ancestors` walks, and reports the shape of the resulting graph so a claim about the search's breadth can be
checked rather than asserted. It computes no evidence and writes no rows: the ledger remains the only source of
number, and the kernel remains untouched.

Two details are load-bearing. A lineage is written root-first, so the *nearest* parent is its final element, and
walking from the wrong end would invert every chain. And a candidate naming itself is dropped rather than stored:
a ledger repair once made that possible, and while `_ancestors` carries a seen-set that would survive it, a
self-edge is not ancestry and storing one would quietly corrupt every depth this module reports.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class AncestryReport:
    """The shape of the search, in the terms that distinguish a population from a ratchet."""

    nodes: int
    roots: int
    max_depth: int
    distinct_parents: int
    widest: tuple[str, int] | None

    def to_dict(self) -> dict[str, object]:
        return {
            "nodes": self.nodes,
            "roots": self.roots,
            "max_depth": self.max_depth,
            "distinct_parents": self.distinct_parents,
            "widest": list(self.widest) if self.widest else None,
        }

    @property
    def is_star(self) -> bool:
        """A search that never went deeper than one generation, however many candidates it tried."""
        return self.max_depth <= 1


def parent_map(path: Path) -> dict[str, str | None]:
    """Fold a ledger's propose rows into `child -> nearest parent`, the shape the selection arms walk.

    Only propose rows carry ancestry. Later rows about the same candidate — observations, prunes, promotions —
    may mention a lineage in passing, and reading those would let a downstream row rewrite a candidate's parent
    after the fact. A candidate's parent is fixed when it is proposed.

    A candidate with no lineage, an empty one, or one naming only itself is a root: it is recorded with `None`
    rather than omitted, so a caller can tell "this candidate is a root" from "this candidate is unknown".
    """
    parents: dict[str, str | None] = {}
    if not Path(path).exists():
        return parents

    for line in Path(path).read_text().splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict) or row.get("kind") != "propose":
            continue
        cid = row.get("candidate_id")
        if not isinstance(cid, str):
            continue
        payload = row.get("payload")
        lineage = payload.get("lineage") if isinstance(payload, dict) else None
        parents[cid] = _nearest(cid, lineage)
    return parents


def _nearest(cid: str, lineage: object) -> str | None:
    """The last element of a root-first lineage, provided it is another candidate's id."""
    if not isinstance(lineage, list):
        return None
    for entry in reversed(lineage):
        if isinstance(entry, str) and entry and entry != cid:
            return entry
    return None


def depth(cid: str, parents: dict[str, str | None]) -> int:
    """Generations between a candidate and its root, counting a root as zero.

    The walk carries a seen-set for the same reason `_ancestors` does: a cycle introduced by a ledger repair must
    fail the measurement, not hang the night that asked for it.
    """
    seen = {cid}
    node, out = parents.get(cid), 0
    while node is not None and node not in seen:
        seen.add(node)
        out += 1
        node = parents.get(node)
    return out


def ancestry_report(parents: dict[str, str | None]) -> AncestryReport:
    """Describe the graph, so "the search is a star" is a measurement rather than an impression."""
    named = Counter(p for p in parents.values() if p is not None)
    return AncestryReport(
        nodes=len(parents),
        roots=sum(1 for p in parents.values() if p is None),
        max_depth=max((depth(c, parents) for c in parents), default=0),
        distinct_parents=len(named),
        widest=named.most_common(1)[0] if named else None,
    )


@dataclass(frozen=True)
class Pressure:
    """One night's choice: how many live candidates it could have run, and how many it could afford."""

    night: int
    live: int
    selected: int

    @property
    def declined(self) -> int:
        return max(0, self.live - self.selected)

    @property
    def binding(self) -> bool:
        """Whether the budget forced a choice at all."""
        return self.declined > 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "night": self.night, "live": self.live, "selected": self.selected,
            "declined": self.declined, "binding": self.binding,
        }


def selection_pressure(path: Path) -> list[Pressure]:
    """Per night, how much the selection rule actually had to decide.

    A selection rule earns its keep only when the live pool exceeds what the budget can run. This measures that
    directly, and on this ledger the answer is uncomfortable: since night 7 the loop has proposed about as many
    candidates as it can afford, so the pool has equalled the budget on most nights and the controller has ranked
    a set it was going to run in full regardless.

    The live pool is every candidate proposed on or before the night that has not been pruned or promoted,
    restricted to the evaluation benches that night worked on. The restriction matters: a candidate measured on
    another pool is not an alternative to one measured on this pool, and counting it inflates the apparent choice.
    """
    proposed: dict[str, int] = {}
    pruned: dict[str, int] = {}
    promoted: dict[str, int] = {}
    selected: dict[int, set[str]] = defaultdict(set)
    bench: dict[str, str | None] = {}

    if not Path(path).exists():
        return []

    for line in Path(path).read_text().splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        cid, night, kind = row.get("candidate_id"), row.get("night"), row.get("kind")
        if not isinstance(cid, str) or night is None:
            continue
        night = int(night)
        if kind == "propose":
            proposed.setdefault(cid, night)
            bench.setdefault(cid, (row.get("bucket") or {}).get("task_family"))
        elif kind == "prune":
            pruned.setdefault(cid, night)
        elif kind == "promote":
            promoted.setdefault(cid, night)
        elif kind == "select":
            selected[night].add(cid)

    out: list[Pressure] = []
    for night in sorted(selected):
        worked = {bench.get(c) for c in selected[night]}
        live = [
            cid for cid, born in proposed.items()
            if born <= night
            and pruned.get(cid, night + 1) >= night
            and promoted.get(cid, night + 1) >= night
            and bench.get(cid) in worked
        ]
        out.append(Pressure(night, len(live), len(selected[night])))
    return out


__all__ = [
    "AncestryReport", "Pressure", "ancestry_report", "depth", "parent_map", "selection_pressure",
]
