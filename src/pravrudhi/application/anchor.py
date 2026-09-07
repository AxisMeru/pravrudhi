"""Score an artifact against the items it drew, so two generations can be compared without spending a rotation.

Every measurement this project holds is a pass rate on one rotation of a sealed pool, and rotations differ in
difficulty. The loop copes by pairing: a candidate and the incumbent run on the same rotation with the same seed,
and the sequential boundary reads the difference. That is sound and nothing here changes it.

It stops being sufficient the moment candidates are kept across generations. A delta is a comparison against
whichever incumbent was standing when it was taken, so two deltas measured against two different incumbents are
on two different scales. Pooling them is precisely the defect the kernel's rebase path was written to prevent —
the path that never fires, because the key it reads is never written. An archive that keeps old candidates makes
that latent defect load-bearing.

The way out is to anchor on the items instead of on the incumbent. An item's difficulty is estimated from every
observation that ever scored it; an artifact's anchored score is its pass rate minus the mean difficulty of the
items it happened to draw. That quantity is defined against the pool rather than against a moving reference, so
it is comparable across generations, and it costs nothing to compute because every number it needs is already in
the ledger and in the per-item score files the kernel already writes.

Three properties are deliberate.

**Leave-one-out is the correctness argument, not a refinement.** An in-sample difficulty estimate lets an
observation help set the baseline it is then scored against, which flatters an artifact evaluated on items
nothing else has touched. Each item's baseline therefore excludes the observation being scored.

**An item with no evidence outside the observation being scored is not anchorable**, and is dropped from the mean
rather than given a made-up baseline. The count of what was dropped is returned, because a quietly shrinking
denominator is how a number stops meaning what its name says.

**The table carries a hash.** Difficulty estimates sharpen as more nights run, so the anchored score of a fixed
measurement moves as evidence arrives. That is correct — the estimate is improving — but it means a bare anchored
score is not reproducible. Any document citing one must cite `epoch` beside it.

This module states no verdict and writes no rows. It re-reads measurements the kernel already took.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Difficulty:
    """How often each item has been passed, across every observation that scored it."""

    successes: Mapping[str, int]
    trials: Mapping[str, int]
    epoch: str

    def leave_one_out(self, item: str, score: int) -> float | None:
        """The item's pass rate with one observation's contribution removed.

        `None` when the item carries no evidence beyond the observation being scored, which is the honest answer:
        an artifact cannot be graded against a baseline it is the only source of.
        """
        n = self.trials.get(item, 0)
        if n <= 1:
            return None
        return (self.successes.get(item, 0) - score) / (n - 1)


@dataclass(frozen=True)
class Anchored:
    """A raw pass rate beside the same rate net of the difficulty of the items drawn."""

    value: float
    anchored: float | None
    n_items: int
    n_anchored: int
    epoch: str

    @property
    def complete(self) -> bool:
        """Whether every item scored could be anchored. A partial anchor is usable but must be reported."""
        return self.n_items > 0 and self.n_anchored == self.n_items

    def to_dict(self) -> dict[str, object]:
        return {
            "value": self.value,
            "anchored": self.anchored,
            "n_items": self.n_items,
            "n_anchored": self.n_anchored,
            "difficulty_epoch": self.epoch,
        }


def difficulty(observations: Iterable[Mapping[str, int]]) -> Difficulty:
    """Fold every per-item result into a per-item success count and trial count.

    Scores are the kernel's own binary per-item outcomes; anything that is not a 1 counts as a trial the item was
    not passed, which is what the pass-rate denominator already does.
    """
    successes: Counter[str] = Counter()
    trials: Counter[str] = Counter()
    for obs in observations:
        for item, score in obs.items():
            trials[item] += 1
            if score == 1:
                successes[item] += 1
    table = [[i, successes.get(i, 0), trials[i]] for i in sorted(trials)]
    epoch = hashlib.sha256(json.dumps(table, sort_keys=True).encode()).hexdigest()
    return Difficulty(dict(successes), dict(trials), epoch)


def anchored(per_item: Mapping[str, int], value: float, table: Difficulty) -> Anchored:
    """`value` less the mean leave-one-out difficulty of the items this observation actually scored."""
    baselines = [b for item, score in per_item.items() if (b := table.leave_one_out(item, score)) is not None]
    mean = sum(baselines) / len(baselines) if baselines else None
    return Anchored(
        value=value,
        anchored=None if mean is None else value - mean,
        n_items=len(per_item),
        n_anchored=len(baselines),
        epoch=table.epoch,
    )


def load_per_item(path: Path | str) -> dict[str, int]:
    """Read one kernel-written `per_item_scores.jsonl`, tolerating a file that is gone.

    A missing file is an empty result rather than an error: 526 observations reference one, the job directories
    are disposable by design, and an archive that crashed on the first reaped directory would be unusable.
    """
    p = Path(path)
    if not p.exists():
        return {}
    out: dict[str, int] = {}
    for line in p.read_text().splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        item, score = row.get("id"), row.get("score")
        if isinstance(item, str) and isinstance(score, int):
            out[item] = score
    return out


__all__ = ["Anchored", "Difficulty", "anchored", "difficulty", "load_per_item"]
