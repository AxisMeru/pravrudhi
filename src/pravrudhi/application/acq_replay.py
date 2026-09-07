"""Grade a selection rule on nights already run, off-policy, for no GPU time at all.

The engine holds 189 proposals and 526 observations, and eleven of its nights measured every candidate they
proposed. On those nights a counterfactual selection rule can be graded exactly rather than simulated: rebuild
what the loop believed before it chose, let a different rule choose from the same pool, and look up what the
candidate it picked actually scored. Every number comes from a kernel-written observation that already exists.

This is what makes the meta level affordable. Measuring a selection policy live means running nights, and the
sealed pools are nearly spent; a comparison powered for the effect sizes this project actually sees would need
several times the rotations that remain. Replay costs seconds of CPU, so hundreds of rules can be compared before
one rotation is committed.

**Fitness is the anchored score, not the paired delta.** A delta is a comparison against whichever incumbent was
standing at the time, and a bench that spans nights spans incumbents. `anchor.py` gives a score defined against
the item pool instead, which is both comparable across generations and, measured on this ledger, the least noisy
of the three available scales.

**The statistic is the area under the best-so-far curve at matched evaluation count.** A selection rule's job is
to find the good candidate sooner, not to be handed more tries, so the area is a mean rather than a sum and a rule
gains nothing from a longer run. Scores are carried signed: most candidates here score below the incumbent, and
clipping would hide what an arm actually costs.

**Two biases are declared rather than corrected, because they cannot be corrected from recorded data.**

The measured set was curated by the arm that actually ran. A challenger is re-ordering a shortlist its rival drew
up, so a win is conservative evidence and a loss is weak evidence. That asymmetry is a property of the bench and
must be stated wherever its numbers are.

The arm that ran also chose which candidates earned repeat seeds. Scoring on every observation would credit it for
its own budget decisions, so each candidate is scored on its **first** observation only. Every candidate on a
covered night has one, so no arm is helped or hurt by seed allocation.

The bench measures acquisition, not generation: it can only re-order candidates that were proposed. If the real
bottleneck is the proposer, every rule will score alike, and that outcome is informative rather than disappointing.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from pravrudhi.application.anchor import anchored, difficulty, load_per_item
from pravrudhi.application.archive import parent_map
from pravrudhi.application.policies import BASELINES, rank_scores

AS_RUN = "as-run"
"""The order the loop actually selected in, read from its select rows. The arm every challenger must beat."""


def best_so_far(scores: list[float]) -> list[float]:
    """The best score seen at or before each evaluation. Monotone by construction."""
    out: list[float] = []
    top: float | None = None
    for s in scores:
        top = s if top is None or s > top else top
        out.append(top)
    return out


def auc(curve: list[float]) -> float:
    """Mean height of a best-so-far curve, so a rule cannot win by being given more evaluations."""
    return sum(curve) / len(curve) if curve else 0.0


@dataclass(frozen=True)
class Scored:
    """One candidate's real, recorded outcome, on the scale the bench compares."""

    cid: str
    night: int
    bench: str
    value: float
    anchored: float
    epoch: str


@dataclass(frozen=True)
class ReplayResult:
    policy: str
    nights: tuple[int, ...]
    n_selections: int
    auc: float
    mean_selected: float
    best_found: float
    difficulty_epoch: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "policy": self.policy,
            "nights": list(self.nights),
            "n_selections": self.n_selections,
            "auc": round(self.auc, 6),
            "mean_selected": round(self.mean_selected, 6),
            "best_found": round(self.best_found, 6),
            "difficulty_epoch": self.difficulty_epoch,
        }


def first_observations(ledger: Path) -> dict[str, dict[str, Any]]:
    """Each candidate's earliest observation, which is the one the bench scores it on."""
    out: dict[str, dict[str, Any]] = {}
    for line in Path(ledger).read_text().splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("kind") != "observe":
            continue
        cid = row.get("candidate_id")
        if not isinstance(cid, str) or cid in out:
            continue
        observed = row.get("payload", {}).get("observed") or {}
        if observed.get("per_item_scores_ref") and observed.get("value") is not None:
            out[cid] = row
    return out


def scored_candidates(ledger: Path) -> dict[str, Scored]:
    """Every candidate with a recorded first observation, on the anchored scale, keyed per evaluation bench.

    Difficulty is estimated per bench. Pass rates on two different pools are not on one scale and anchoring
    across them would manufacture a comparison the evidence does not support.
    """
    firsts = first_observations(ledger)
    per_bench: dict[str, list[dict[str, int]]] = defaultdict(list)
    loaded: dict[str, tuple[str, dict[str, int], float, int]] = {}

    for cid, row in firsts.items():
        observed = row["payload"]["observed"]
        bench = (row.get("bucket") or {}).get("task_family") or "?"
        items = load_per_item(observed["per_item_scores_ref"])
        if not items:
            continue
        loaded[cid] = (bench, items, float(observed["value"]), int(row.get("night") or 0))
        per_bench[bench].append(items)

    tables = {b: difficulty(rows) for b, rows in per_bench.items()}
    out: dict[str, Scored] = {}
    for cid, (bench, items, value, night) in loaded.items():
        a = anchored(items, value, tables[bench])
        if a.anchored is not None:
            out[cid] = Scored(cid, night, bench, value, a.anchored, a.epoch)
    return out


def _rows(ledger: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for line in Path(ledger).read_text().splitlines():
        row = _row(line)
        if row is not None:
            out.append(row)
    return out


def _row(line: str) -> dict[str, Any] | None:
    try:
        out = json.loads(line)
    except json.JSONDecodeError:
        return None
    return out if isinstance(out, dict) else None


@dataclass(frozen=True)
class Night:
    """One replayable decision: what the loop could have chosen from, and how much it could afford."""

    night: int
    pool: tuple[str, ...]
    budget: int

    @property
    def binding(self) -> bool:
        """Whether the budget forced a choice. A night that could afford its whole pool decides nothing."""
        return 0 < self.budget < len(self.pool)


def decisions(ledger: Path, scored: dict[str, Scored]) -> list[Night]:
    """Reconstruct each night's live pool and the budget the loop actually spent on it.

    The live pool is not the night's own proposals. Candidates persist: `deliberate` draws from everything
    proposed so far that has not been pruned or promoted, which on this ledger reaches 77 candidates while a
    night's budget buys 8. Scoring only the night's own proposals — the first way this was written — made the
    budget look non-binding and every arm look identical, which was an artefact of the mistake.

    The pool is then narrowed to candidates carrying a real measurement, because a counterfactual pick can only
    be scored against something the kernel actually observed. That narrowing is the bench's central limitation
    and its direction is known: the measured set is the set the arm that ran chose.

    A measurement taken on a different night still scores a pick here, and that is sound only because the score
    is anchored. A paired delta would be a comparison against a different incumbent; an anchored score is defined
    against the item pool and does not move when the incumbent does.

    The pool is finally restricted to the evaluation benches the night was actually working on. Anchored scores
    are comparable within a pool and not across pools — measured on this ledger the four benches differ in spread
    by a factor of nearly five — so a candidate evaluated on one is not an alternative to a candidate evaluated
    on another. Without this restriction every counterfactual arm reached back to a handful of night-3 candidates
    on the widest-spread bench and appeared to beat the arm that ran by a wide margin, which was an artefact of
    pooling and not a result.
    """
    rows = _rows(ledger)
    proposed: dict[str, int] = {}
    pruned: dict[str, int] = {}
    promoted: dict[str, int] = {}
    selected: dict[int, set[str]] = defaultdict(set)

    for row in rows:
        cid, night, kind = row.get("candidate_id"), row.get("night"), row.get("kind")
        if not isinstance(cid, str) or night is None:
            continue
        night = int(night)
        if kind == "propose":
            proposed.setdefault(cid, night)
        elif kind == "prune":
            pruned.setdefault(cid, night)
        elif kind == "promote":
            promoted.setdefault(cid, night)
        elif kind == "select":
            selected[night].add(cid)

    out: list[Night] = []
    for night in sorted(selected):
        benches = {scored[c].bench for c in selected[night] if c in scored}
        if not benches:
            continue
        live = [
            cid
            for cid, born in proposed.items()
            if born <= night
            and pruned.get(cid, night + 1) >= night
            and promoted.get(cid, night + 1) >= night
            and cid in scored
            and scored[cid].bench in benches
        ]
        out.append(Night(night, tuple(sorted(live)), len(selected[night])))
    return out


def replay(
    root: Path,
    *,
    policies: tuple[str, ...] = (AS_RUN, *BASELINES),
    rng_seed: int = 0,
    citta_for: Any = None,
    binding_only: bool = True,
) -> dict[str, ReplayResult]:
    """Run every policy against the same budgeted decisions and score what each one would have picked.

    `citta_for(night)` supplies the beliefs the loop held before that night chose. The caller provides it so the
    bench does not depend on how a Citta is assembled, and an arm that cannot get one is skipped rather than
    scored on a guess.

    `binding_only` drops nights whose budget covered the whole pool. Those nights contain no decision, and
    including them pads every arm with identical picks, which flattens real differences toward zero.
    """
    ledger = Path(root) / "research" / "ledger.jsonl"
    scored = scored_candidates(ledger)
    nights = [d for d in decisions(ledger, scored) if d.binding or not binding_only]
    lineage = parent_map(ledger)
    as_run = _as_run_selection(ledger)
    epoch = next(iter(scored.values())).epoch if scored else ""

    results: dict[str, ReplayResult] = {}
    for policy in policies:
        picked: list[float] = []
        for d in nights:
            if policy == AS_RUN:
                order = [c for c in as_run.get(d.night, []) if c in scored]
            else:
                citta = citta_for(d.night) if citta_for else None
                if citta is None:
                    continue
                rng = np.random.default_rng(rng_seed + d.night)
                s = rank_scores(policy, citta, list(d.pool), rng, lineage)
                order = sorted(d.pool, key=lambda c: (-s.get(c, 0.0), c))[: d.budget]
            picked.extend(scored[c].anchored for c in order)
        curve = best_so_far(picked)
        results[policy] = ReplayResult(
            policy=policy,
            nights=tuple(d.night for d in nights),
            n_selections=len(picked),
            auc=auc(curve),
            mean_selected=sum(picked) / len(picked) if picked else 0.0,
            best_found=max(picked) if picked else 0.0,
            difficulty_epoch=epoch,
        )
    return results


def _as_run_selection(ledger: Path) -> dict[int, list[str]]:
    """What the loop actually selected each night, in ledger order."""
    out: dict[int, list[str]] = defaultdict(list)
    for row in _rows(ledger):
        cid, night = row.get("candidate_id"), row.get("night")
        ok = row.get("kind") == "select" and isinstance(cid, str) and night is not None
        if ok and cid not in out[int(night)]:  # type: ignore[arg-type]
            out[int(night)].append(cid)  # type: ignore[arg-type]
    return dict(out)


__all__ = [
    "AS_RUN", "Night", "ReplayResult", "Scored", "auc", "best_so_far", "decisions",
    "first_observations", "replay", "scored_candidates",
]
