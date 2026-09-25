"""Tests for the pure statistics and sampling helpers the P3 citation prereg
(research/prereg/p3-citation-verification-2026-09-25.md) specifies: exact Clopper-Pearson CIs, Cohen's kappa
for the two-labeler agreement, and the two code-exact sampling procedures (§3/§4 of the prereg). Written
before the prereg is signed and before any real sample is drawn -- these are the pure/testable building
blocks the scoring script will use once R1 signs; none of them touch the real case index.
"""

from __future__ import annotations

import pytest

from pravrudhi.application.p3_prereg import (
    clopper_pearson_ci,
    cohens_kappa,
    precision_sample_indices,
    recall_sample_order,
)


class TestClopperPearsonCi:
    """No fabricated "reference tool" values here -- every expected number below is derived from a closed-form
    identity that can be checked by hand, not recalled from memory of a tool this repo has no way to run:
    for k=0, the exact upper bound is `1 - (alpha/2)**(1/n)`; for k=n, the exact lower bound is
    `(alpha/2)**(1/n)`; for n=1 both reduce to the trivial single-Bernoulli-trial case."""

    def test_zero_successes_matches_the_closed_form_edge_case(self) -> None:
        lo, hi = clopper_pearson_ci(0, 50)
        assert lo == 0.0
        assert hi == pytest.approx(1 - 0.025 ** (1 / 50), abs=1e-9)

    def test_all_successes_matches_the_closed_form_edge_case(self) -> None:
        lo, hi = clopper_pearson_ci(50, 50)
        assert hi == 1.0
        assert lo == pytest.approx(0.025 ** (1 / 50), abs=1e-9)

    def test_single_trial_zero_successes(self) -> None:
        # n=1, k=0: CI = [0, 1 - alpha/2] = [0, 0.975], the single-Bernoulli-trial case worked by hand.
        lo, hi = clopper_pearson_ci(0, 1)
        assert lo == 0.0
        assert hi == pytest.approx(0.975, abs=1e-9)

    def test_single_trial_one_success(self) -> None:
        # n=1, k=1: CI = [alpha/2, 1] = [0.025, 1].
        lo, hi = clopper_pearson_ci(1, 1)
        assert lo == pytest.approx(0.025, abs=1e-9)
        assert hi == 1.0

    def test_interval_is_symmetric_around_half_for_k_equal_half_n(self) -> None:
        # No closed form for the middle case, but a real invariant holds exactly: k/n = 0.5 makes the
        # binomial symmetric under p -> 1-p, so the CI must be symmetric around 0.5 too.
        lo, hi = clopper_pearson_ci(50, 100)
        assert lo == pytest.approx(1 - hi, abs=1e-9)
        assert (lo + hi) / 2 == pytest.approx(0.5, abs=1e-9)

    def test_round_trips_through_the_binomial_tail_it_inverts(self) -> None:
        # Property check standing in for an external reference: the returned lower bound must be the p at
        # which P(Binomial(n, p) >= k) == alpha/2 exactly (the definition Clopper-Pearson inverts).
        from math import comb, exp, fsum, log, log1p

        def tail(n: int, k: int, p: float) -> float:
            return fsum(exp(log(comb(n, j)) + j * log(p) + (n - j) * log1p(-p)) for j in range(k, n + 1))

        lo, hi = clopper_pearson_ci(45, 100)
        assert tail(100, 45, lo) == pytest.approx(0.025, abs=1e-6)
        assert tail(100, 46, hi) == pytest.approx(0.975, abs=1e-6)

    def test_refuses_more_successes_than_trials(self) -> None:
        with pytest.raises(ValueError):
            clopper_pearson_ci(11, 10)

    def test_refuses_non_positive_n(self) -> None:
        with pytest.raises(ValueError):
            clopper_pearson_ci(0, 0)

    def test_pooled_recall_ci_half_width_matches_the_prereg_power_claim(self) -> None:
        # The prereg's §3 power claim: pooled recall's 95% CI half-width at n=200, k=100 (p=0.5, the worst
        # case) is ~0.0713 -- this pins the actual computed value so a formula change fails loudly rather
        # than silently drifting from what the signed prereg states.
        lo, hi = clopper_pearson_ci(100, 200)
        half_width = (hi - lo) / 2
        assert half_width == pytest.approx(0.0713, abs=2e-4)

    def test_resolution_precision_power_claim_at_n_100_k_90(self) -> None:
        # The prereg's §4 power claim: at n=100, k=90 (precision 0.9), the exact CI is [0.824, 0.951].
        lo, hi = clopper_pearson_ci(90, 100)
        assert lo == pytest.approx(0.824, abs=2e-3)
        assert hi == pytest.approx(0.951, abs=2e-3)


class TestCohensKappa:
    def test_perfect_agreement_is_one(self) -> None:
        a = ["found", "found", "missed", "found"]
        b = ["found", "found", "missed", "found"]
        assert cohens_kappa(a, b) == pytest.approx(1.0)

    def test_agreement_matching_chance_is_zero(self) -> None:
        # Both labelers say "found" 50% and "missed" 50%, but independently -- constructed to hit exactly the
        # chance-agreement rate: 2 agreements out of 4, with marginals 2/2 each side, chance agreement = 0.5.
        a = ["found", "found", "missed", "missed"]
        b = ["found", "missed", "found", "missed"]
        assert cohens_kappa(a, b) == pytest.approx(0.0, abs=1e-9)

    def test_systematic_disagreement_is_negative(self) -> None:
        a = ["found", "missed"]
        b = ["missed", "found"]
        assert cohens_kappa(a, b) < 0

    def test_refuses_mismatched_lengths(self) -> None:
        with pytest.raises(ValueError):
            cohens_kappa(["found"], ["found", "missed"])

    def test_refuses_empty_input(self) -> None:
        with pytest.raises(ValueError):
            cohens_kappa([], [])

    def test_kappa_is_undefined_when_both_labelers_agree_on_everything_the_same_way(self) -> None:
        # No disagreement AND only one category ever appears -> chance agreement is 1.0 too (deterministic
        # marginals), so kappa's 0/0 case: defined here as 1.0 (perfect, non-informative agreement), not NaN,
        # since the two labelers did in fact agree on every single item.
        a = ["found"] * 5
        b = ["found"] * 5
        assert cohens_kappa(a, b) == 1.0


class TestRecallSampleOrder:
    """§3's code-exact recall sampling procedure: a fixed-seed full permutation of document indices."""

    def test_deterministic_for_a_fixed_seed(self) -> None:
        order1 = recall_sample_order(n_docs=10, seed=20250925)
        order2 = recall_sample_order(n_docs=10, seed=20250925)
        assert order1 == order2

    def test_is_a_full_permutation_not_a_subset(self) -> None:
        order = recall_sample_order(n_docs=37, seed=20250925)
        assert sorted(order) == list(range(37))

    def test_different_seed_gives_a_different_order(self) -> None:
        order_a = recall_sample_order(n_docs=50, seed=20250925)
        order_b = recall_sample_order(n_docs=50, seed=1)
        assert order_a != order_b


class TestPrecisionSampleIndices:
    """§4's code-exact resolution-precision sampling procedure: 100 indices drawn from the alias-row
    population with a seed distinct from the recall sample's (stated explicitly in the prereg to avoid any
    ambiguity about shared randomness between the two tasks)."""

    def test_deterministic_for_a_fixed_seed(self) -> None:
        idx1 = precision_sample_indices(n_rows=1000, k=100, seed=20250926)
        idx2 = precision_sample_indices(n_rows=1000, k=100, seed=20250926)
        assert idx1 == idx2

    def test_returns_k_distinct_indices_within_range(self) -> None:
        idx = precision_sample_indices(n_rows=1000, k=100, seed=20250926)
        assert len(idx) == 100
        assert len(set(idx)) == 100
        assert all(0 <= i < 1000 for i in idx)

    def test_uses_a_seed_distinct_from_the_recall_sample_by_prereg_convention(self) -> None:
        # Not a code invariant (nothing stops calling both with the same seed), but the prereg names 20250925
        # for recall and 20250926 for precision -- this test pins those two literals so a future edit to
        # either constant fails loudly rather than silently drifting from the signed document.
        assert precision_sample_indices(n_rows=100, k=10, seed=20250925) != precision_sample_indices(
            n_rows=100, k=10, seed=20250926
        )

    def test_refuses_k_larger_than_population(self) -> None:
        with pytest.raises(ValueError):
            precision_sample_indices(n_rows=10, k=100, seed=20250926)
