"""Record a decision on a promotion pack, by a person or under the operator's delegation.

ADR-REF: ADR-0040. `api/server.py`'s `/inbox/sign` route already does this over HTTP, which is how the product
and studio apps sign. This is the same act from the CLI, and it deliberately shares the delegation and its
conditions rather than reimplementing them: a second path with its own idea of what is allowed would be a way
around the first, and the conditions are the whole substance of the delegation.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from pravrudhi.application.delegation import AGENT_IDENTITIES, load_delegation, unmet_conditions
from pravrudhi.application.night import inbox_listing
from pravrudhi_kernel.ledger import LedgerWriter, replay

DECISIONS = ("approve", "reject", "defer")


def record_decision(
    root: Path, *, pack: Path, decision: str, note: str = "", by: str = ""
) -> dict[str, Any]:
    """Append the `signoff` event for one pack. Returns what was written.

    `by` empty (or an agent identity) means autonomous, which needs a recorded delegation. A person's name
    signs as that person and is not subject to the delegation's conditions -- a human reading the pack IS the
    judgement those conditions stand in for.
    """
    root = Path(root)
    if decision not in DECISIONS:
        raise ValueError(f"decision must be one of {', '.join(DECISIONS)}; got {decision!r}")

    rows = inbox_listing(root)
    row = next((r for r in rows if Path(r["pack"]) == Path(pack)), None)
    if row is None:
        known = ", ".join(sorted(str(r["pack"]) for r in rows)) or "(none)"
        raise FileNotFoundError(f"{pack} is not a promotion pack in this workspace; known packs: {known}")
    if row["signed"]:
        # Not an error worth raising -- a second identical decision changes nothing -- but it must not look
        # like a fresh one either.
        raise ValueError(f"{pack} already carries a signoff; re-deciding it would append a second one")

    who, autonomous = by.strip(), False
    if not who or who.lower() in AGENT_IDENTITIES:
        delegation = load_delegation(root)
        if delegation is None:
            raise PermissionError(
                "sign-off is a human act here: pass --by <name>, or record a delegation in "
                "configs/delegation.yaml (ADR-0040)"
            )
        allowed, why = delegation.permits("promote_t2")
        if not allowed:
            raise PermissionError(f"autonomous sign-off refused: {why}")
        # Only `approve` is gated. Declining to promote something cannot promote it, and gating `reject`
        # would take away the loop's ability to prune -- which is the opposite of what the delegation is for.
        if decision == "approve":
            unmet = unmet_conditions(delegation, act="promote_t2", badge=row.get("badge"))
            if unmet:
                raise PermissionError("autonomous approval refused: " + "; ".join(unmet))
        who, autonomous = delegation.identity, True
        note = note or delegation.citation

    ledger = root / "research" / "ledger.jsonl"
    w = LedgerWriter.open(ledger, "0.1.0")
    ev = w.append(
        "signoff",
        # The actor prefix records WHICH kind of signature this is. `human:` for an autonomous close would
        # make the two indistinguishable in the ledger, which is the one thing ADR-0040 says must stay
        # distinguishable.
        f"{'agent' if autonomous else 'human'}:{who}",
        {
            "pack": str(pack),
            "decision": decision,
            "scope": "promote_T2",
            "note": note,
            "pack_hash": hashlib.sha256((Path(pack) / "README.md").read_bytes()).hexdigest(),
        },
        epoch=0,
        night=replay(ledger).night,
    )
    return {
        "seq": ev.seq,
        "this_hash": ev.this_hash,
        "decision": decision,
        "by": who,
        "autonomous": autonomous,
        "badge": row.get("badge"),
    }


# 2026-09-14: ADR-0040's delegation and the conditions above have existed since 2026-09-10, and `/inbox/sign`
# already applies them correctly -- but nothing calls it on a schedule. Eight promotion packs sat unsigned on
# the hosted Studio engine (some for days) for no reason other than that the operator had to open the inbox
# and notice them, which is exactly what the delegation was granted to stop. This is the missing driver: the
# same decision a person or `inbox-sign` would make for one pack, applied to every unsigned one, on a timer.
_EQUIVOCAL_NOTE = (
    "badge is {badge!r}, not green: the evidence is equivocal, so the autonomous action is to run the "
    "experiment that resolves it, not to approve it or leave it silently parked (CHARTER §6)."
)


def sweep(root: Path) -> list[dict[str, Any]]:
    """Decide every unsigned promotion pack once, under the recorded delegation: approve a green badge,
    defer anything else with the standing equivocal-evidence reason. One bad pack (no delegation recorded, an
    unreadable README, a badge the delegation's conditions still refuse) is skipped and reported rather than
    raised, so it does not stop the rest of the sweep -- the same fail-soft discipline `_beat_obligations`
    already applies to a batch of independent dispatches.
    """
    root = Path(root)
    results: list[dict[str, Any]] = []
    for row in inbox_listing(root):
        if row["signed"]:
            continue
        pack = Path(row["pack"])
        badge = row.get("badge")
        decision = "approve" if badge == "green" else "defer"
        note = "" if decision == "approve" else _EQUIVOCAL_NOTE.format(badge=badge)
        try:
            out = record_decision(root, pack=pack, decision=decision, note=note)
            results.append({"pack": str(pack), **out})
        except (PermissionError, ValueError, FileNotFoundError, OSError) as e:
            results.append({"pack": str(pack), "skipped": str(e)})
    return results
