"""Manthana: the cow's rumination — deciding what a grazed item is worth, and running the one trial that can
promote it.

Design doc §6.3's state machine is `grazed -> shortlisted -> trial_planned -> trial_running -> ruminated`, with
`deferred`, `quarantined` and `forgotten` as explicit alternate dispositions (`grahana.STATES`,
`grahana.PINNED_STATES`). The rule this module exists to enforce is the one sentence in §6.3 that matters most:
"Fetching or an agent's summary cannot reach `ruminated`." There is no function here that sets an item to
`ruminated` directly. The only path is `record_trial_outcome`, and it only takes that path when it is handed a
completed, checked trial receipt with outcome `"useful"` — never a bare description of what happened.

This module reuses `grahana.py`'s holding store (`load_items`/`save_items`) rather than owning a second one;
design §3.1 puts both the catalogue and its lifecycle transitions under the same holding-store authority. A
full `TrialSpec` (design §6.3: source/candidate/base hashes, baseline, canaries, exposure budget) belongs to the
harness integration this task does not build; `TrialPlan` here is the minimal binding — which item, what
hypothesis, when — that a `trial_planned` state requires to exist before a trial can run.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pravrudhi.application.grahana import (
    IntakeItem,
    forget,
    load_items,
    save_items,
)

TrialOutcome = Literal["useful", "not_useful", "inconclusive", "invalid"]

# Legal state-machine edges (design §6.3). Any transition not listed here is refused by `advance`, including
# every direct route to "ruminated" other than the one `record_trial_outcome` takes after checking its receipt.
TRANSITIONS: dict[str, frozenset[str]] = {
    "grazed": frozenset({"shortlisted", "deferred", "quarantined", "forgotten"}),
    "shortlisted": frozenset({"trial_planned", "grazed", "deferred", "quarantined", "forgotten"}),
    "trial_planned": frozenset({"trial_running", "deferred", "quarantined"}),
    "trial_running": frozenset({"ruminated", "deferred", "quarantined"}),
    "deferred": frozenset({"shortlisted", "quarantined", "forgotten"}),
    "quarantined": frozenset({"forgotten", "deferred"}),
    "ruminated": frozenset(),
    "forgotten": frozenset(),
}


class ManthanaError(ValueError):
    """An illegal state transition, an unknown item, or a promotion attempted without a qualifying trial."""


def _iso(moment: datetime) -> str:
    aware = moment if moment.tzinfo else moment.replace(tzinfo=UTC)
    return aware.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _get(items: dict[str, IntakeItem], item_id: str) -> IntakeItem:
    item = items.get(item_id)
    if item is None:
        raise ManthanaError(f"no intake item {item_id!r}")
    return item


def advance(root: Path, item_id: str, new_state: str, **field_updates: Any) -> IntakeItem:
    """Move one item to `new_state` if and only if the state machine (`TRANSITIONS`) permits it from its current
    state. This is the single choke point every promotion in this module goes through — including the illegal
    ones tests exercise directly, such as `grazed -> ruminated`."""
    items = load_items(root)
    item = _get(items, item_id)
    allowed = TRANSITIONS.get(item.state, frozenset())
    if new_state not in allowed:
        raise ManthanaError(f"illegal intake transition {item.state!r} -> {new_state!r} for {item_id!r}")
    updated = replace(item, state=new_state, **field_updates)
    items[item_id] = updated
    save_items(root, items)
    return updated


def shortlist(root: Path, n: int, *, now: datetime | None = None) -> tuple[IntakeItem, ...]:
    """The top `n` `grazed` items (design §6.3): items linked to an open request or capability gap first, then
    the rest ranked by relevance total, ties broken by `item_id` for a deterministic, replayable choice. Moves
    each chosen item to `shortlisted` and persists it; a summary is never enough to reach this state either — an
    item still needs its own `grazed` relevance score."""
    if n <= 0:
        return ()
    del now  # accepted for interface symmetry with the rest of this module; ranking here needs no clock read
    items = load_items(root)
    candidates = sorted(
        (i for i in items.values() if i.state == "grazed"),
        key=lambda i: (0 if i.linked_requirements else 1, -i.relevance.total, i.item_id),
    )
    chosen_ids = [i.item_id for i in candidates[:n]]
    for iid in chosen_ids:
        items[iid] = replace(items[iid], state="shortlisted")
    if chosen_ids:
        save_items(root, items)
    return tuple(items[iid] for iid in chosen_ids)


@dataclass(frozen=True)
class TrialPlan:
    """The minimal binding design §6.3 requires before `trial_planned`: which item, what question a real trial
    would answer, and when it was bound. Not a `TrialSpec` — no harness/base-hash binding happens here."""

    item_id: str
    planned_at: str
    hypothesis: str

    def to_dict(self) -> dict[str, Any]:
        return {"item_id": self.item_id, "planned_at": self.planned_at, "hypothesis": self.hypothesis}


def plan_trial(root: Path, item_id: str, *, hypothesis: str = "", now: datetime | None = None) -> TrialPlan:
    """`shortlisted -> trial_planned` (design §6.3). Refuses from any other state — most importantly `grazed`,
    which has never been ranked or linked to anything and so cannot skip straight to planning a trial."""
    as_of = now or datetime.now(UTC)
    advance(root, item_id, "trial_planned")
    return TrialPlan(item_id=item_id, planned_at=_iso(as_of), hypothesis=hypothesis)


def start_trial(root: Path, item_id: str) -> IntakeItem:
    """`trial_planned -> trial_running` (design §6.3)."""
    return advance(root, item_id, "trial_running")


def record_trial_outcome(
    root: Path, item_id: str, *, outcome: TrialOutcome, trial_id: str, completed: bool, checked: bool,
) -> IntakeItem:
    """The only path to `ruminated` (design §6.3): `outcome == "useful"` from a trial that actually completed
    and was independently checked. Anything else — an incomplete run, an unchecked run, or a non-useful outcome
    — moves the item to `deferred` (transport failure, inconclusive, or simply not useful yet — design §6.3 says
    a transport failure returns to `deferred` with the same trial identity) or `quarantined` (`invalid`: a real
    problem with the trial itself, such as a licence or isolation violation, not merely a bad result)."""
    if item_id not in load_items(root):
        raise ManthanaError(f"no intake item {item_id!r}")
    if outcome == "useful":
        if not (completed and checked and trial_id):
            raise ManthanaError(
                f"trial {trial_id!r} for {item_id!r} cannot propose admission: only a completed, checked trial "
                "receipt can move an item to 'ruminated' — an incomplete or unchecked run cannot"
            )
        return advance(root, item_id, "ruminated", supersession_reason="")
    if outcome == "invalid":
        return advance(root, item_id, "quarantined", supersession_reason=f"trial {trial_id!r} was invalid")
    if outcome in ("not_useful", "inconclusive"):
        return advance(root, item_id, "deferred", supersession_reason=f"trial {trial_id!r} outcome: {outcome}")
    raise ManthanaError(f"unknown trial outcome {outcome!r}")


def propose_admission(root: Path, item_id: str, *, note: str) -> None:
    """What an agent's free-text summary of an item is worth toward admission: nothing. Design §6.3 is explicit
    that fetching or a summary can never reach `ruminated` — only `record_trial_outcome`, fed a completed and
    checked trial receipt, can. This function exists so that attempt has somewhere to go other than silently
    mutating state; it always refuses."""
    if item_id not in load_items(root):
        raise ManthanaError(f"no intake item {item_id!r}")
    raise ManthanaError(
        f"a summary ({note!r}) cannot promote intake item {item_id!r}; only a completed trial with outcome "
        "'useful' can propose admission"
    )


__all__ = [
    "TRANSITIONS", "ManthanaError", "TrialOutcome", "advance", "shortlist", "TrialPlan", "plan_trial",
    "start_trial", "record_trial_outcome", "propose_admission", "forget",
]
