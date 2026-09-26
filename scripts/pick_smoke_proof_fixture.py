"""Mechanical selection rule for the config-C smoke test's `proof` fixture (Track-C, 2026-09-26).

Replaces an earlier hand-written fixture that turned out to be a marginal, near-tau case under the real 32B
judge (found 2026-09-25/26 when L2A's smoke run against the deployed endpoint failed check 1). This fixture
must never be hand-picked again -- it is chosen by a stated rule, from sealed data, exactly like
`negative_not_proved` already is.

**Rule**:
1. Candidate pool: every `full_ir_gold` item (no denial elements) in the constructed eval set
   (`eval_items.jsonl`, sha `600f6dc2...`) whose SXM-run 32B `p_established` (sha `4c84dc8d...`) is >= 0.999
   on EVERY required element -- a wide margin above tau=0.97, chosen because a smoke fixture should not be
   near the boundary the REFER band exists to catch.
2. Order candidates by `item_id`, ascending.
3. **Live re-verification, not trust-from-sealed-margin alone**: the >=0.999 margin above is measured
   against the item's OWN real narrative (the convention the SXM measurement run itself used). The smoke
   script instead sends a content-free placeholder narrative ("Smoke-test facts, not a real case.") --
   2026-09-25/26 finding: narrative content materially changes 32B p (a real, large, reproducible effect,
   not noise -- see the P0 narrative-sensitivity investigation). So candidates are tried IN ORDER, live,
   against the actual deployed 32B endpoint WITH the placeholder narrative, and the first one whose EVERY
   required element clears tau under that exact prompt is accepted. Do not accept a candidate on the sealed
   margin alone.
4. The accepted item's facts (excluding `F_narrative`) become the fixture's `facts`; production
   `ingest_facts()` never sees `F_narrative` either.

This script implements steps 1-2 (candidate ordering) as pure, offline logic; step 3 (live check) is a
separate, deliberate action against a real endpoint -- not run automatically here, so this script itself
never makes a network call or costs anything by being imported/run for candidate listing.
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

EVAL_ITEMS_ENV = "PRAVRUDHI_CONFIGC_FIXTURE_EVAL_ITEMS"
SXM_SCORES_ENV = "PRAVRUDHI_CONFIGC_FIXTURE_SXM_SCORES"
MARGIN = 0.999


def candidate_pool(eval_items_path: Path, sxm_scores_path: Path) -> list[tuple[str, list[tuple[str, str, float]]]]:
    """Returns [(item_id, [(contract_id, element_id, p), ...]), ...], ordered by item_id ascending, for
    every full_ir_gold item whose every required element clears MARGIN on the SXM run's own recorded p."""
    items = [json.loads(line) for line in eval_items_path.read_text().splitlines() if line.strip()]
    sxm: dict[str, float] = {}
    for line in sxm_scores_path.read_text().splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        sxm[r["id"]] = r["p_established"]

    by_item: dict[str, list[tuple[str, str, float | None]]] = defaultdict(list)
    for it in items:
        if it["partition"] != "full_ir_gold" or it.get("is_denial"):
            continue
        key = f"{it['item_id']}__{it['element_id']}"
        by_item[it["item_id"]].append((it["contract_id"], it["element_id"], sxm.get(key)))

    qualifying: list[tuple[str, list[tuple[str, str, float]]]] = []
    for item_id in sorted(by_item):
        elems = by_item[item_id]
        if all(p is not None and p >= MARGIN for _, _, p in elems):
            qualifying.append((item_id, [(c, e, p) for c, e, p in elems if p is not None]))
    return qualifying


def main() -> int:
    import os

    eval_items = os.environ.get(EVAL_ITEMS_ENV)
    sxm_scores = os.environ.get(SXM_SCORES_ENV)
    if not eval_items or not sxm_scores:
        print(f"REFUSING: set {EVAL_ITEMS_ENV} and {SXM_SCORES_ENV} (no defaults)", file=sys.stderr)
        return 2
    pool = candidate_pool(Path(eval_items), Path(sxm_scores))
    print(f"{len(pool)} candidates (margin >= {MARGIN}), lowest item_id first:")
    for item_id, elems in pool[:10]:
        print(f"  {item_id}: {elems}")
    print(
        "\nThese are candidates only -- step 3 (live re-verification against the deployed endpoint with the "
        "smoke script's actual placeholder narrative) is a separate, deliberate action, not run here."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
