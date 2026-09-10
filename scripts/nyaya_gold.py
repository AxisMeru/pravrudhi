#!/usr/bin/env python3
"""Build a Nyaya validity gold set whose labels are DERIVED, not asserted.

Why this exists. The product's differentiator is an answer that proves its own inference -- a Z3-verified
vyapti / hetvabhasa validity trace. That claim currently rests on 2 valid syllogisms and 3 invalid ones, each
scoring 1.0. The 95% interval on 2/2 runs from roughly 0.16 to 1.0, so the measurement excludes almost nothing,
while the metric beside it that nobody doubts (a karaka probe) has n=8000.

Why a constructed set is admissible here, when the charter forbids synthetic stand-ins as evidence: validity is
DECIDABLE. Given a world -- a mapping from locus to the properties that hold there -- whether the hetu pervades
the sadhya is a set-theoretic fact, so each label is a derivation rather than an opinion. Every item records
the rule its label comes from, so any item can be checked back to the classical distinction it encodes. This
follows the precedent of the project's own Paninian dose corpus: 945 verb forms derived from Panini's sutras,
"no fabrication, fully reproducible".

What this deliberately does NOT do: it does not import the verifier, and it does not score it. This emits the
gold set only. Feeding it to `z3_check_pervasion` belongs where the verifier lives, so that a gold set which
grades its own grader is impossible by construction -- the labels here are derived from the world independently
of any verifier's opinion about it.

Usage:
  python scripts/nyaya_gold.py --per-class 120 --seed 0 --out research/ext/nyaya-gold/gold.json
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path
from typing import Any

World = dict[str, frozenset[str]]

CLASSES = ("valid", "asiddha", "savyabhicara", "viruddha", "aprasiddha")

# The rule each verdict encodes, in the classical vocabulary, recorded on every item so a label can be traced
# rather than trusted.
RULES = {
    "valid": "vyapti holds: wherever the hetu, there the sadhya; the hetu is established in the paksa; and a "
             "locus other than the paksa corroborates it (udaharana)",
    "asiddha": "asiddha: the hetu is not established in the paksa",
    "viruddha": "viruddha: the hetu proves the absence of the sadhya -- no locus bearing the hetu bears the "
                "sadhya",
    "savyabhicara": "savyabhicara (anaikantika): the hetu strays -- some but not all loci bearing the hetu "
                    "lack the sadhya",
    "aprasiddha": "aprasiddha: pervasion is unviolated but no locus other than the paksa corroborates it, so "
                  "there is no positive example",
}


def derive_verdict(world: World, paksa: str, sadhya: str, hetu: str) -> str:
    """The classical verdict for one inference in one world, derived from set relations.

    Order is load-bearing and follows the classical priority: a reason not present in the subject is faulty
    whatever else is true of other loci, so asiddha is tested first. Universal deviation is contrariety
    (viruddha) rather than straying (savyabhicara), and they are different faults with different remedies.
    """
    if paksa not in world or hetu not in world[paksa]:
        return "asiddha"
    hetu_loci = [locus for locus, props in world.items() if hetu in props]
    deviating = [locus for locus in hetu_loci if sadhya not in world[locus]]
    if deviating:
        return "viruddha" if len(deviating) == len(hetu_loci) else "savyabhicara"
    corroborating = [locus for locus in hetu_loci if locus != paksa]
    if not corroborating:
        return "aprasiddha"
    return "valid"


def _worlds(rng: random.Random, count: int) -> list[World]:
    """Small random worlds over a fixed property vocabulary.

    Deliberately small and many rather than few and large: the distinctions being tested are about how a
    handful of loci relate, and a large world makes every class collapse towards savyabhicara because some
    locus almost always strays.
    """
    props = ["smoke", "fire", "water", "earth", "motion", "sound", "heat", "vapour"]
    loci = ["mountain", "kitchen", "iron-ball", "lake", "forge", "cloud", "river", "lamp", "pot", "field"]
    out: list[World] = []
    for _ in range(count):
        chosen = rng.sample(loci, rng.randint(3, 5))
        world: World = {}
        for locus in chosen:
            k = rng.randint(1, 3)
            world[locus] = frozenset(rng.sample(props, k))
        out.append(world)
    return out


def build_gold_set(per_class: int, seed: int = 0) -> list[dict[str, Any]]:
    """Up to `per_class` items for each of the five verdicts, balanced and deterministic for a seed.

    Balanced on purpose. An aggregate rejection rate of 0.95 is consistent with catching asiddha perfectly and
    savyabhicara never, and telling those apart is the entire reason the taxonomy exists -- so the set must be
    able to report per class, which means having enough of each.
    """
    rng = random.Random(seed)
    banks: dict[str, list[dict[str, Any]]] = {c: [] for c in CLASSES}
    props = ["smoke", "fire", "water", "earth", "motion", "sound", "heat", "vapour"]

    # Enumerate rather than search: for each world, every (paksa, sadhya, hetu) triple is classified by
    # derivation and filed. Generation stops when every bank is full or the world supply is exhausted.
    #
    # At most one item per class per world, so every class draws from a comparable spread of worlds. Filling
    # each bank greedily instead let the easy classes fill from the first few worlds while the rare ones
    # ranged over hundreds, which would make a per-class rate partly a statement about world size.
    for world in _worlds(rng, 4000):
        if all(len(b) >= per_class for b in banks.values()):
            break
        filed: set[str] = set()
        for paksa in world:
            for sadhya in props:
                for hetu in props:
                    # An inference whose probans IS its probandum ("it has water because it has water") is
                    # not an inference: Nyaya requires hetu and sadhya to be distinct terms, and such an item
                    # is passed by anything that merely checks the hetu is present in the paksa. Keeping a
                    # fraction of them made 70 of 120 valid items tautologies, because being trivially
                    # pervaded is the cheapest way into the valid bank; the class the panel reports as
                    # `nyaya_validity` was then majority-degenerate. They are excluded outright.
                    if sadhya == hetu:
                        continue
                    verdict = derive_verdict(world, paksa, sadhya, hetu)
                    bank = banks[verdict]
                    if len(bank) >= per_class or verdict in filed:
                        continue
                    filed.add(verdict)
                    bank.append({
                        "id": f"{verdict}-{len(bank):04d}",
                        "world": {locus: sorted(p) for locus, p in sorted(world.items())},
                        "paksa": paksa,
                        "sadhya": sadhya,
                        "hetu": hetu,
                        "expected": verdict,
                        "rule": RULES[verdict],
                    })
    return [item for cls in CLASSES for item in banks[cls]]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--per-class", type=int, default=120)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    gold = build_gold_set(args.per_class, args.seed)
    counts = {c: sum(1 for it in gold if it["expected"] == c) for c in CLASSES}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(
        {"seed": args.seed, "per_class_target": args.per_class, "counts": counts, "items": gold},
        indent=1, sort_keys=True,
    ) + "\n")
    print(f"wrote {args.out} -- {len(gold)} items")
    for cls, n in counts.items():
        short = "" if n >= args.per_class else f"  (SHORT of {args.per_class})"
        print(f"  {cls:14} {n}{short}")


if __name__ == "__main__":
    main()
