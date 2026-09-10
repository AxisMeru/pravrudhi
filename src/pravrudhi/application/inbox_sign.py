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
