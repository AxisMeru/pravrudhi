#!/usr/bin/env python3
"""Dev measurement of the accused-attribution check D0 on CONSTRUCTED sentences (no model, no real or sealed text).

Positives are TRUE-PROOF sentences: a specific act performed by the accused under review alone. The refusal rate on them is
what Lead-2's decision 4 bounds at 20% (POINT estimate, exact one-sided Clopper-Pearson upper bound beside it). Negatives
(a co-accused's act, collective forms) are listed only to show the check refuses what it is for.

CAVEAT, repeated in the output: constructed by the check's author, so it measures actor-equality accuracy on written
templates, not real prevalence; real text is harder. The real dev bank (Track B) is the second measurement.

    python3 scripts/accused_attribution_dev_measure.py [--n-per-cell 40] [--seed 20261005] [--json OUT]
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from pravrudhi.application.nyaya_attribution import AccusedRef, check_attribution  # noqa: E402

NAMES = ["Nila", "Meena", "Sarita", "Kavya", "Radha", "Anita", "Divya", "Pooja"]
MEN = ["Ramesh", "Suresh", "Mahesh", "Dinesh", "Rakesh", "Naresh"]
ACTS_IN_LEXICON = [
    "slapped {v} on the face",
    "beat {v} with a stick",
    "abused {v} in filthy language",
    "harassed {v} for dowry",
    "threatened {v} with dire consequences",
    "tortured {v} mentally",
    "assaulted {v}",
    "taunted {v} about {v}'s family",
    "demanded Rs. 5 lakhs from {v}",
    "kicked {v} out of the room",
]
ACTS_OUTSIDE_LEXICON = [
    "locked {v} in a room",
    "pushed {v} down the stairs",
    "snatched {v}'s jewellery",
    "pulled {v}'s hair",
    "refused to give {v} food",
    "spat at {v}",
    "turned {v} out of the house",
    "stopped {v} from meeting {v}'s parents",
]
WHEN = ["On 12.03.2019", "On 04.07.2018, at about 9 pm", "In March 2017", "Thereafter", "After the marriage", ""]


def _ref(n: int, m: int, name: str | None = None) -> AccusedRef:
    aliases = (f"Accused No.{n}", f"A{n}", f"Petitioner No.{n}") + ((name,) if name else ())
    return AccusedRef(f"acc{n}", aliases, ((f"Accused No.{m}", f"A{m}"), ("her husband",)))


def _pick(rng: random.Random, acts: list[str], v: str) -> str:
    return rng.choice(acts).replace("{v}", v)


def _cell(make: Callable[[random.Random, int, int, str, str, str], str], *, name: bool = False):
    def build(rng: random.Random, n: int, m: int) -> tuple[str, AccusedRef]:
        victim = rng.choice(NAMES)
        man = rng.choice(MEN)
        when = rng.choice(WHEN)
        sentence = make(rng, n, m, victim, man, when)
        return sentence, _ref(n, m, man if name else None)

    return build


def _lead(when: str, rest: str) -> str:
    return f"{when}, {rest}" if when else rest[0].upper() + rest[1:]


def _sing(alias: str):
    def f(rng, n, m, v, man, when):
        a = alias.format(n=n, m=m, man=man)
        return _lead(when, f"{a} {_pick(rng, ACTS_IN_LEXICON, v)}.") if when else f"{a} {_pick(rng, ACTS_IN_LEXICON, v)}."

    return f


POSITIVE = {
    "P01 'Accused No.n' + lexicon verb": _cell(_sing("Accused No.{n}")),
    "P02 'A n' short form": _cell(_sing("A{n}")),
    "P03 'Petitioner No.n'": _cell(_sing("Petitioner No.{n}")),
    "P04 personal name (alias supplied)": _cell(
        lambda rng, n, m, v, man, w: _lead(w, f"{man} {_pick(rng, ACTS_IN_LEXICON, v)}."), name=True
    ),
    "P05 bridge/adverb forms": _cell(
        lambda rng, n, m, v, man, w: _lead(
            w,
            f"Accused No.{n} {rng.choice(['allegedly used to', 'started to', 'repeatedly', 'also'])} "
            f"{_pick(rng, ['harass {v}', 'beat {v}', 'abuse {v}', 'taunt {v}'], v)}.",
        )
    ),
    "P06 passive with by-agent": _cell(
        lambda rng, n, m, v, man, w: _lead(
            w, f"{v} was {rng.choice(['beaten', 'slapped', 'abused', 'harassed', 'threatened'])} by Accused No.{n}."
        )
    ),
    "P07 apposition ('her husband, Accused No.n,')": _cell(
        lambda rng, n, m, v, man, w: _lead(w, f"her husband, Accused No.{n}, {_pick(rng, ACTS_IN_LEXICON, v)}.")
    ),
    "P08 subordinate clause first": _cell(
        lambda rng, n, m, v, man, w: f"When {v} protested, Accused No.{n} {_pick(rng, ACTS_IN_LEXICON, v)}."
    ),
    "P09 object-position collective (should PASS)": _cell(
        lambda rng, n, m, v, man, w: _lead(
            w,
            f"Accused No.{n} {rng.choice(['abused', 'beat', 'insulted'])} {v} in front of "
            f"{rng.choice(['her in-laws', 'the family members', 'her relatives'])}.",
        )
    ),
    "P10 object kin/other (should PASS)": _cell(
        lambda rng, n, m, v, man, w: _lead(
            w, f"Accused No.{n} threatened {v}'s parents and demanded Rs. {rng.randint(1, 9)} lakhs."
        )
    ),
    "P11 verb outside the lexicon": _cell(
        lambda rng, n, m, v, man, w: _lead(w, f"Accused No.{n} {_pick(rng, ACTS_OUTSIDE_LEXICON, v)}.")
    ),
    "P12 pronoun subject after the name in one fact": _cell(
        lambda rng, n, m, v, man, w: (
            f"Accused No.{n} came home drunk and he {_pick(rng, ['beat {v}', 'abused {v}', 'slapped {v}'], v)}."
        )
    ),
    "P13 pronoun-only (refused by design, R4)": _cell(lambda rng, n, m, v, man, w: f"He {_pick(rng, ACTS_IN_LEXICON, v)}."),
}
BY_DESIGN = {"P13 pronoun-only (refused by design, R4)"}

NEGATIVE = {
    "N1 co-accused singular": _cell(lambda rng, n, m, v, man, w: _lead(w, f"Accused No.{m} {_pick(rng, ACTS_IN_LEXICON, v)}.")),
    "N2 the husband (other party)": _cell(
        lambda rng, n, m, v, man, w: _lead(w, f"Her husband {_pick(rng, ACTS_IN_LEXICON, v)}.")
    ),
    "N3 'the accused' generic": _cell(lambda rng, n, m, v, man, w: _lead(w, f"The accused {_pick(rng, ACTS_IN_LEXICON, v)}.")),
    "N4 'accused nos. x to y'": _cell(
        lambda rng, n, m, v, man, w: _lead(w, f"Accused Nos.{n} to {n + 3} {_pick(rng, ACTS_IN_LEXICON, v)}.")
    ),
    "N5 'X along with Y'": _cell(
        lambda rng, n, m, v, man, w: _lead(w, f"Accused No.{n} along with Accused No.{m} {_pick(rng, ACTS_IN_LEXICON, v)}.")
    ),
    "N6 in-laws": _cell(lambda rng, n, m, v, man, w: _lead(w, f"Her in-laws {_pick(rng, ACTS_IN_LEXICON, v)}.")),
    "N7 'they'": _cell(lambda rng, n, m, v, man, w: f"They {_pick(rng, ACTS_IN_LEXICON, v)}."),
}


def cp_upper(k: int, n: int, a: float = 0.05) -> float:
    """Exact one-sided Clopper-Pearson upper bound by bisection (stdlib only)."""
    if k >= n:
        return 1.0
    from math import comb

    def cdf(p: float) -> float:
        return sum(comb(n, i) * p**i * (1 - p) ** (n - i) for i in range(k + 1))

    lo, hi = 0.0, 1.0
    for _ in range(80):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if cdf(mid) > a else (lo, mid)
    return hi


def run(n_per_cell: int, seed: int, pronoun_rule: bool = False) -> dict:
    rng = random.Random(seed)
    out: dict = {
        "seed": seed,
        "n_per_cell": n_per_cell,
        "label": "CONSTRUCTED, author-written templates; not real prevalence",
        "cells": {},
    }
    for group, cells in (("positive", POSITIVE), ("negative", NEGATIVE)):
        for name, build in cells.items():
            refused, reasons, examples = 0, {}, []
            for _ in range(n_per_cell):
                n, m = rng.sample(range(1, 9), 2)
                sentence, ref = build(rng, n, m)
                r = check_attribution(sentence, ref, pronoun_rule=pronoun_rule)
                if not r.passed:
                    refused += 1
                    reasons[r.reason] = reasons.get(r.reason, 0) + 1
                    if len(examples) < 2:
                        examples.append(sentence)
            out["cells"][name] = {
                "group": group,
                "n": n_per_cell,
                "refused": refused,
                "reasons": reasons,
                "refused_examples": examples,
            }
    pos = [c for c in out["cells"].values() if c["group"] == "positive"]
    for key, cells in (
        ("true_proof_refusal_all_cells", pos),
        (
            "true_proof_refusal_excluding_by_design",
            [c for k, c in out["cells"].items() if c["group"] == "positive" and k not in BY_DESIGN],
        ),
    ):
        k, n = sum(c["refused"] for c in cells), sum(c["n"] for c in cells)
        out[key] = {"refused": k, "n": n, "point": round(k / n, 4), "cp95_upper": round(cp_upper(k, n), 4)}
    neg = [c for c in out["cells"].values() if c["group"] == "negative"]
    k, n = sum(c["refused"] for c in neg), sum(c["n"] for c in neg)
    out["negatives_refused"] = {"refused": k, "n": n, "rate": round(k / n, 4)}
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--n-per-cell", type=int, default=40)
    ap.add_argument("--seed", type=int, default=20261005)
    ap.add_argument("--pronoun-rule", action="store_true", help="measure with the R4 relaxation on (default off)")
    ap.add_argument("--json", default=None)
    a = ap.parse_args(argv)
    res = run(a.n_per_cell, a.seed, a.pronoun_rule)
    print(res["label"])
    for name, c in res["cells"].items():
        print(f"  [{c['group'][:3]}] {name:55s} refused {c['refused']:3d}/{c['n']}  {c['reasons']}")
    for key in ("true_proof_refusal_all_cells", "true_proof_refusal_excluding_by_design", "negatives_refused"):
        print(key, res[key])
    if a.json:
        Path(a.json).write_text(json.dumps(res, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
