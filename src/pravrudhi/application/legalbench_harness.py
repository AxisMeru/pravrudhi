"""Track A P3 (`research/prereg/B1-tier1-amendment-2.md`): closed-book unaided-accuracy scoring for
LegalBench's rule-application tasks (`hearsay`, `personal_jurisdiction` first). Binary Yes/No
classification, not the conduct-with-missing-element shape `nyaya_lean_elements.py`'s `A3E` path checks --
`load_task` reads a task's own `.tsv` shape (`index`, `answer`, `text`, `slice`), `score_unaided` compares a
vendor's raw Yes/No answers against gold. A task file with no `slice` column of its own gets one slice named
for the task, flagged as such rather than passed off as a slice the data drew.

Every result is labelled `"model-read"` (`result["labelled"]`) so no caller can present this as a
Lean-checked verdict by omission -- per the amendment, a Lean-checked claim on this corpus needs a per-rule
element `Contract` authored from the rule's black-letter text AND an independent fact-to-element extraction
step, neither of which exists yet (deliberately not built here -- see the amendment for why building the
Lean path without that extraction step would be exactly the overclaim `nyaya_lean_elements.py` already
refuses to make on the citation path).

Per-slice accuracy is always broken out and never pooled into the headline number (ADR-0001's own reasoning
for never pooling across vendors applies here too: a vendor could score well on one branch of a rule and
badly on another, and pooling would hide that).
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class NoItemsError(ValueError):
    """`score_unaided` was called with no items to score -- refused rather than silently reporting an
    empty result as a real (vacuous) accuracy number."""


@dataclass(frozen=True)
class LegalBenchItem:
    index: int
    answer: str
    text: str
    slice: str
    slice_is_task_fallback: bool = False
    """True when the task file carried no `slice` column and `slice` is the task's own name instead --
    recorded on every item so no caller reads a task-named bucket as a slice the data drew."""


def load_task(tsv_path: Path) -> list[LegalBenchItem]:
    """Read one LegalBench task's `.tsv` (`index`, `answer`, `text`, and usually `slice`).

    A task file with no `slice` column (the `diversity_*` files, whose own columns are `index`, `text`,
    `answer`, `parties_are_diverse`, `aic_is_met`) is neither pooled into an unnamed bucket nor given
    invented structure: every row gets the single slice named for the task (`tsv_path`'s parent
    directory), and every item carries `slice_is_task_fallback=True` so a caller -- and any result
    written from those items -- can tell the task had no slices of its own.

    Only the `slice` column has a fallback. A file with no `answer` or `text` column still raises.
    """
    task_name = tsv_path.parent.name
    with tsv_path.open(newline="") as f:
        reader = csv.DictReader(f, delimiter="\t")
        has_own_slices = "slice" in (reader.fieldnames or [])
        return [
            LegalBenchItem(
                index=int(row["index"]),
                answer=row["answer"],
                text=row["text"],
                slice=row["slice"] if has_own_slices else task_name,
                slice_is_task_fallback=not has_own_slices,
            )
            for row in reader
        ]


def score_unaided(vendor_answers: dict[int, str], items: list[LegalBenchItem]) -> dict[str, Any]:
    """Score `vendor_answers` (item index -> raw answer text) against `items`' gold `answer` column.

    Comparison is case-insensitive exact match (`"Yes"`/`"yes"`/`"YES"` all match gold `"Yes"`) -- vendors
    are not penalized for casing, but no other normalization is applied (a hedged or explained answer that
    isn't a bare Yes/No is scored as given, not parsed for one).

    An item with no entry in `vendor_answers` counts in `n` and counts as wrong -- an unanswered item is a
    real datum (the vendor did not answer), never silently excluded from the denominator.

    Raises `NoItemsError` if `items` is empty.

    Returns `{"accuracy", "n", "per_slice": {slice: {"correct", "n"}}, "slice_is_task_fallback",
    "labelled": "model-read"}`. `slice_is_task_fallback` is True when any item's slice is the task's own
    name because its file had no `slice` column, so a written result never reads as though the breakdown
    were over slices the data drew.
    """
    if not items:
        raise NoItemsError("no items to score")

    per_slice: dict[str, dict[str, int]] = {}
    total_correct = 0
    for item in items:
        given = vendor_answers.get(item.index)
        is_correct = given is not None and given.strip().lower() == item.answer.strip().lower()
        total_correct += int(is_correct)
        bucket = per_slice.setdefault(item.slice, {"correct": 0, "n": 0})
        bucket["n"] += 1
        bucket["correct"] += int(is_correct)

    return {
        "accuracy": total_correct / len(items),
        "n": len(items),
        "per_slice": per_slice,
        "slice_is_task_fallback": any(item.slice_is_task_fallback for item in items),
        "labelled": "model-read",
    }
