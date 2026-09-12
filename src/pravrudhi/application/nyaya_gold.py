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

Taxonomy (2026-09-12, session-3's decision on measurement-design spec section 4.2): six banks -- valid, plus
the five classical hetvabhasa: asiddha, viruddha, savyabhicara, satpratipaksa, badhita. `aprasiddha` is a
sub-case of asiddha for this gold set's purposes, not a sixth bank: `derive_verdict` still returns it (it is a
real, distinct set-theoretic case, and removing it would be a substantive change to a decidability function
that two other implementations are cross-checked against), but no candidate labelled `aprasiddha` is filed
into any bank here.

`satpratipaksa` and `badhita` are not decidable the way the other four are: ADR-0001 and
`prabhasa-nyaya/lean/PrabhasaNyaya/Hetvabhasa.lean` both say so plainly -- they are "rule- and prompt-based for
now", because within a single closed world (the format every other class here uses) a paksa's properties are
already settled ground truth, and both fallacies are precisely about a paksa that is NOT yet settled: a
counterbalanced reason (satpratipaksa) or one overridden by a stronger pramana (badhita). So these two banks
are built differently, and honestly documented as a simplification rather than pretending false rigor:

* `satpratipaksa` pairs two INDEPENDENTLY valid inferences (each individually checked by `derive_verdict`,
  each in its own witnessing world) concluding different sadhyas. Two things independently support to
  different conclusions -- that is the fallacy -- but this generator does not require the pair to share a
  paksa across their two separate worlds; a real decider must bind that itself.
* `badhita` takes one valid inference and names a `defeating_source`: a stronger pramana (pratyaksa or sabda,
  chosen for variety) asserting the sadhya's negation. The claim is recorded, not verified -- there is no
  closed-world fact to check it against, which is exactly why `Verdict.of` does not decide this class either.

Both carry what a decider needs so the items are complete even before the decider exists (session-3, A2
decision 1): the counter-inference in full, or the defeating claim in full, travel with the item rather than
being asserted in a docstring nobody reads.

What this deliberately does NOT do: it does not import the verifier, and it does not score it. This emits the
gold set only. Scoring belongs where the verifier lives (`prabhasa-nyaya`'s Lean `Verdict.of`, wired in
`nyaya_gold_score.py`), so that a gold set which grades its own grader is impossible by construction -- the
labels here are derived from the world independently of any verifier's opinion about it, and
`satpratipaksa`/`badhita` are labelled as not decided by that verifier rather than scored against it.
"""

from __future__ import annotations

import random
from typing import Any

World = dict[str, frozenset[str]]

CLASSES = ("valid", "asiddha", "viruddha", "savyabhicara", "satpratipaksa", "badhita")

#: Classes `derive_verdict` can produce and this gold set files into a bank of their own. `valid` is harvested
#: separately (see `build_gold_set`) because it also supplies the raw material for `satpratipaksa`/`badhita`.
_DECIDED_FALLACY_CLASSES = ("asiddha", "viruddha", "savyabhicara")

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
    "satpratipaksa": "satpratipaksa (counterbalanced): the hetu's inference is individually valid, but an "
                     "equally supported counter-inference (see counter_inference) argues a different sadhya "
                     "for the same paksa. Verdict.of does not decide this class -- it names the competing "
                     "inference a future decider must weigh.",
    "badhita": "badhita (defeated): the hetu's inference is individually valid, but a named stronger pramana "
               "(see defeating_source) already asserts the sadhya's negation. Verdict.of does not decide this "
               "class -- it names the defeating claim a future decider must weigh.",
}

#: Stronger pramanas that can defeat an otherwise-valid inference, for badhita's defeating_source.
_STRONGER_PRAMANAS = ("pratyaksa", "sabda")


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


def _satpratipaksa_bank(
    pool: list[dict[str, Any]], per_class: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Pair independently-valid inferences (from `pool`, each already checked by `derive_verdict`) into
    satpratipaksa items concluding different sadhyas. Returns (bank, unused_remainder_of_pool)."""
    bank: list[dict[str, Any]] = []
    i = 0
    while len(bank) < per_class and i + 1 < len(pool):
        base, counter = pool[i], pool[i + 1]
        if base["sadhya"] == counter["sadhya"]:
            # Not a competing conclusion -- try the next base against this same counter rather than
            # discarding both; a pair that collides on sadhya is the rare case, not the common one.
            i += 1
            continue
        i += 2
        bank.append({
            "id": f"satpratipaksa-{len(bank):04d}",
            "world": base["world"], "paksa": base["paksa"], "sadhya": base["sadhya"], "hetu": base["hetu"],
            "counter_inference": {
                "world": counter["world"], "paksa": counter["paksa"],
                "sadhya": counter["sadhya"], "hetu": counter["hetu"],
            },
            "expected": "satpratipaksa",
            "rule": RULES["satpratipaksa"],
        })
    return bank, pool[i:]


def _badhita_bank(rng: random.Random, pool: list[dict[str, Any]], per_class: int) -> list[dict[str, Any]]:
    """One valid inference per item, each carrying a named-but-unverified defeating claim from a stronger
    pramana. Nothing in `pool` is consumed twice: each base is used by exactly one badhita item."""
    bank: list[dict[str, Any]] = []
    for base in pool[:per_class]:
        bank.append({
            "id": f"badhita-{len(bank):04d}",
            "world": base["world"], "paksa": base["paksa"], "sadhya": base["sadhya"], "hetu": base["hetu"],
            "defeating_source": {
                "pramana": rng.choice(_STRONGER_PRAMANAS),
                "claim": f"not-{base['sadhya']}",
            },
            "expected": "badhita",
            "rule": RULES["badhita"],
        })
    return bank


def build_gold_set(per_class: int, seed: int = 0) -> list[dict[str, Any]]:
    """Up to `per_class` items for each of the six banks, balanced and deterministic for a seed.

    Balanced on purpose. An aggregate rejection rate of 0.95 is consistent with catching asiddha perfectly and
    savyabhicara never, and telling those apart is the entire reason the taxonomy exists -- so the set must be
    able to report per class, which means having enough of each.
    """
    rng = random.Random(seed)
    banks: dict[str, list[dict[str, Any]]] = {c: [] for c in _DECIDED_FALLACY_CLASSES}
    valid_pool: list[dict[str, Any]] = []
    # 4x is the exact accounting (1 valid + 2 per satpratipaksa item + 1 per badhita item); padded to 6x
    # because a same-sadhya collision costs `_satpratipaksa_bank` one pool item without producing one.
    valid_target = per_class * 6
    props = ["smoke", "fire", "water", "earth", "motion", "sound", "heat", "vapour"]

    # Enumerate rather than search: for each world, every (paksa, sadhya, hetu) triple is classified by
    # derivation and filed. Generation stops when every bank is full or the world supply is exhausted.
    #
    # At most one item per class per world, so every class draws from a comparable spread of worlds. Filling
    # each bank greedily instead let the easy classes fill from the first few worlds while the rare ones
    # ranged over hundreds, which would make a per-class rate partly a statement about world size.
    for world in _worlds(rng, 16000):
        if len(valid_pool) >= valid_target and all(len(b) >= per_class for b in banks.values()):
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
                    if verdict in filed:
                        continue
                    if verdict == "valid":
                        if len(valid_pool) >= valid_target:
                            continue
                        filed.add(verdict)
                        valid_pool.append({
                            "world": {locus: sorted(p) for locus, p in sorted(world.items())},
                            "paksa": paksa, "sadhya": sadhya, "hetu": hetu,
                        })
                    elif verdict in banks:
                        bank = banks[verdict]
                        if len(bank) >= per_class:
                            continue
                        filed.add(verdict)
                        bank.append({
                            "id": f"{verdict}-{len(bank):04d}",
                            "world": {locus: sorted(p) for locus, p in sorted(world.items())},
                            "paksa": paksa, "sadhya": sadhya, "hetu": hetu,
                            "expected": verdict,
                            "rule": RULES[verdict],
                        })
                    # else: aprasiddha, a sub-case of asiddha for this gold set's purposes -- not filed.

    valid_bank = [
        {**v, "id": f"valid-{i:04d}", "expected": "valid", "rule": RULES["valid"]}
        for i, v in enumerate(valid_pool[:per_class])
    ]
    satpratipaksa_bank, remainder = _satpratipaksa_bank(valid_pool[per_class:], per_class)
    badhita_bank = _badhita_bank(rng, remainder, per_class)

    all_banks = {
        "valid": valid_bank,
        "asiddha": banks["asiddha"],
        "viruddha": banks["viruddha"],
        "savyabhicara": banks["savyabhicara"],
        "satpratipaksa": satpratipaksa_bank,
        "badhita": badhita_bank,
    }
    return [item for cls in CLASSES for item in all_banks[cls]]
