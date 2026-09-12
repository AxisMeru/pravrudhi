"""Read-only check for r-workspace-scoped-writes (2026-09-12): before that fix, `POST /objectives` and the
`/plan`, `/loom`, `/subagents` routes closed over the engine's own root directly, so a signed-in non-admin
caller's objective landed in the shared root instead of their own workspace.

This lists every objective under `<root>/.pravrudhi/objectives/` created on or after a cutover date, so an
operator can see whether any of that pollution actually happened. It does NOT and cannot name who created one:
`Objective` has no author field, and the routes this bug lived in never recorded the caller's identity anywhere
the objective itself carries forward - reporting an author would mean inventing one, which is exactly the kind
of fabricated evidence this project's Charter refuses. What it reports is honest: id, intent, track and created
timestamp, for a human to correlate against whatever access logs or Supabase records the deployment keeps.

Writes nothing, deletes nothing, and does not attempt to move or quarantine what it finds - a real fix for a
polluted objective (moving it into its actual owner's workspace, or removing it) is a decision for whoever holds
that authority, not something this script would presume to do to a live install's data on its own.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pravrudhi.application.objectives import load_all  # noqa: E402

DEFAULT_SINCE = "2026-09-11"


def objectives_since(root: Path, since: str) -> list[dict[str, str]]:
    """Every objective at `root` whose `created` timestamp sorts on or after `since` (both ISO-ish strings, so a
    plain string comparison is exact for the `YYYY-MM-DD...` shape this codebase always writes). An objective
    with no `created` at all predates the field and is skipped - it cannot be newer than a cutover it has no
    timestamp to compare against."""
    out = []
    for o in load_all(root):
        if o.created and o.created >= since:
            out.append({"id": o.id, "intent": o.intent, "track": o.track, "created": o.created})
    return sorted(out, key=lambda r: r["created"])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", type=Path, required=True, help="the engine root to check, e.g. the product install")
    ap.add_argument("--since", default=DEFAULT_SINCE, help=f"ISO date/time cutover (default {DEFAULT_SINCE})")
    args = ap.parse_args()

    root = args.root.resolve()
    if not (root / ".pravrudhi").is_dir():
        print(f"{root}: not an initialised engine root (no .pravrudhi/)")
        return 1

    found = objectives_since(root, args.since)
    if not found:
        print(f"{root}: no objectives created on or after {args.since} - no evidence of pollution")
        return 0

    print(f"{root}: {len(found)} objective(s) created on or after {args.since} (author not recorded; not shown):")
    for row in found:
        print(f"  {row['created']}  {row['id']}  track={row['track']}  intent={row['intent'][:80]!r}")
    print(
        "\nThese may be the operator's own, entered locally at the root before or after this fix - this script "
        "cannot tell. Cross-check against Supabase or access logs, if the deployment keeps them, before treating "
        "any of these as another caller's work."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
