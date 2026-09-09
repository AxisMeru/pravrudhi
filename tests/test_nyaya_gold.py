"""A Nyaya gold set whose labels are derived, not asserted.

The product's differentiator is a Z3-verified validity trace, and that claim rests on 2 valid syllogisms and 3
invalid ones, both scoring 1.0. The 95% interval on 2/2 runs from about 0.16 to 1.0, so the measurement
excludes almost nothing. Card N4 replaces it with hundreds per hetvabhasa class.

The labels are DERIVED from the world by set relations, not written down by hand -- which is the whole argument
for admitting a constructed set as evidence (spec section 6): validity is decidable, so the label is a
derivation rather than an opinion.

The derivation is cross-checked here against the five syllogisms prabhasa-samskrutam already labels by hand in
`scripts/m4/m4_gate_frt_nyaya.py`. If an independent implementation of the classical rules disagrees with the
project's own worked examples, one of them is wrong and this test says so.

Spec: docs/superpowers/specs/2026-09-09-prabhasa-nyaya-measurement-design.md, card N4.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from nyaya_gold import build_gold_set, derive_verdict  # noqa: E402

# The world prabhasa-samskrutam's own gate uses, verbatim.
FIXTURE_WORLD = {
    "mountain": frozenset({"smoke", "fire"}),
    "kitchen": frozenset({"smoke", "fire"}),
    "iron-ball": frozenset({"fire"}),
    "lake": frozenset({"water"}),
}


class TestDerivationAgreesWithTheProjectsOwnLabels:
    """Five syllogisms that prabhasa-samskrutam labels by hand. An independent derivation must agree."""

    def test_pervasion_holding_with_a_corroborating_instance_is_valid(self) -> None:
        # paksa=mountain, sadhya=fire, hetu=smoke; kitchen corroborates.
        assert derive_verdict(FIXTURE_WORLD, "mountain", "fire", "smoke") == "valid"

    def test_a_self_referential_pervasion_is_still_valid(self) -> None:
        assert derive_verdict(FIXTURE_WORLD, "mountain", "smoke", "smoke") == "valid"

    def test_a_hetu_absent_from_the_paksa_is_asiddha(self) -> None:
        # The lake has no smoke, so a smoke-reason is not established there.
        assert derive_verdict(FIXTURE_WORLD, "lake", "fire", "smoke") == "asiddha"

    def test_a_hetu_that_strays_at_one_locus_is_savyabhicara(self) -> None:
        # sadhya=smoke, hetu=fire; the iron ball has fire and no smoke.
        assert derive_verdict(FIXTURE_WORLD, "mountain", "smoke", "fire") == "savyabhicara"

    def test_a_hetu_that_never_accompanies_the_sadhya_is_viruddha(self) -> None:
        # sadhya=water, hetu=fire; no fiery locus is watery, so the reason proves the contrary.
        assert derive_verdict(FIXTURE_WORLD, "mountain", "water", "fire") == "viruddha"


class TestTheRemainingClass:
    def test_pervasion_with_no_corroborating_instance_is_aprasiddha(self) -> None:
        """Pervasion holds vacuously because the paksa is the only locus with the hetu. Classical Nyaya wants a
        positive example (udaharana); without one the inference is unestablished rather than sound."""
        world = {"only": frozenset({"h", "s"}), "other": frozenset({"x"})}
        assert derive_verdict(world, "only", "s", "h") == "aprasiddha"

    def test_asiddha_outranks_a_deviation_elsewhere(self) -> None:
        """Order matters: if the reason is not even present in the subject, nothing about other loci saves it."""
        world = {"p": frozenset({"s"}), "q": frozenset({"h"})}
        assert derive_verdict(world, "p", "s", "h") == "asiddha"

    def test_a_total_deviation_is_viruddha_not_savyabhicara(self) -> None:
        """Some deviation is straying; universal deviation is contrariety, and they are different faults."""
        world = {"p": frozenset({"h"}), "q": frozenset({"h"}), "r": frozenset({"s"})}
        assert derive_verdict(world, "p", "s", "h") == "viruddha"


class TestTheGoldSet:
    def test_it_reaches_the_target_size_per_class(self) -> None:
        gold = build_gold_set(per_class=40, seed=0)
        counts = {c: sum(1 for it in gold if it["expected"] == c) for it in gold for c in [it["expected"]]}
        for cls in ("valid", "asiddha", "savyabhicara", "viruddha", "aprasiddha"):
            assert counts.get(cls, 0) >= 40, f"{cls} has only {counts.get(cls, 0)}"

    def test_every_item_carries_the_rule_its_label_derives_from(self) -> None:
        """Spec section 6's condition for admitting a constructed set: each item traceable to a rule."""
        gold = build_gold_set(per_class=5, seed=0)
        for item in gold:
            assert item["rule"], item
            assert item["expected"] in item["rule"] or item["expected"] == "valid"

    def test_every_items_label_is_reproduced_by_the_derivation(self) -> None:
        """The set is self-consistent: re-deriving each label from the stored world reproduces it. A generator
        that emitted a label its own rules disagree with would poison every score computed from it."""
        gold = build_gold_set(per_class=20, seed=1)
        for item in gold:
            world = {k: frozenset(v) for k, v in item["world"].items()}
            assert derive_verdict(world, item["paksa"], item["sadhya"], item["hetu"]) == item["expected"]

    def test_it_is_deterministic_for_a_seed(self) -> None:
        """A benchmark that changes between runs cannot support a paired comparison."""
        assert build_gold_set(per_class=10, seed=7) == build_gold_set(per_class=10, seed=7)

    def test_a_different_seed_gives_a_different_set(self) -> None:
        assert build_gold_set(per_class=10, seed=1) != build_gold_set(per_class=10, seed=2)
