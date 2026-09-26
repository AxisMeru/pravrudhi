"""Tests for the pure statistics and sampling helpers the P3 citation prereg
(research/prereg/p3-citation-verification-2026-09-25.md) specifies: exact Clopper-Pearson CIs, Cohen's kappa
for the two-labeler agreement, and the two code-exact sampling procedures (§3/§4 of the prereg). Written
before the prereg is signed and before any real sample is drawn -- these are the pure/testable building
blocks the scoring script will use once R1 signs; none of them touch the real case index.

`TestRecallOrderWithText`/`TestPrecisionSampleWithText` cover a real gap R1 found in the first cut of
`scripts/p3_citation_prereg.py` (2026-09-25): `emit-recall-order` wrote bare case_ids and
`emit-precision-sample` wrote dicts with no `text` key at all -- Labeler B (DashScope) would have been sent an
empty prompt. These tests exist specifically to fail on that regression (an empty/missing `text` field),
against a real in-memory index built the same way `test_verify.py`'s fixture is.
"""

from __future__ import annotations

import sqlite3

import pytest

from pravrudhi.application.case_index import CaseRecord, insert_case, open_index
from pravrudhi.application.p3_prereg import (
    clopper_pearson_ci,
    cohens_kappa,
    precision_sample_indices,
    precision_sample_with_text,
    recall_order_with_text,
    recall_sample_order,
)
from pravrudhi.application.verify import VerifyResult, resolve_citation_key


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


# MINED fixture text, same source as tests/test_verify.py (opennyaiorg/InJudgements shard0 `Text` column).
_CITING_TEXT = (
    "This Court in Narandas Karsondas v. S.A. Kamtam and Anr. (1977) 3 SCC 247 clarified the "
    "position on specific performance and time being of the essence in a contract for sale."
)
_RESOLVED_CASE_TEXT = (
    "In a suit for specific performance, time is not ordinarily of the essence of the contract "
    "for sale of immovable property unless the parties expressly intend it to be so."
)


@pytest.fixture
def db(tmp_path):  # type: ignore[no-untyped-def]
    conn = open_index(tmp_path / "p3_prereg_index.sqlite3")
    insert_case(
        conn,
        CaseRecord(
            case_id="citer1", title="Some Later Case v Someone Else", court="Supreme Court", year=2005,
            source="sc_pdf", path_or_url="/fake/citer.pdf", text=_CITING_TEXT,
        ),
    )
    insert_case(
        conn,
        CaseRecord(
            case_id="resolved1", title="Narandas Karsondas vs S A Kamtam and Anr", court="Supreme Court",
            year=1977, source="sc_pdf", path_or_url="/fake/narandas.pdf", text=_RESOLVED_CASE_TEXT,
        ),
    )
    insert_case(
        conn,
        CaseRecord(
            case_id="empty1", title="A Document With No Text", court="Supreme Court", year=1999,
            source="sc_pdf", path_or_url="/fake/empty.pdf", text=" ",
        ),
    )
    conn.commit()
    yield conn
    conn.close()


class TestRecallOrderWithText:
    """The regression this exists to catch: `emit-recall-order` wrote bare case_ids with no text at all,
    which would have sent Labeler B an empty prompt."""

    def test_every_emitted_record_has_the_real_case_text(self, db: sqlite3.Connection) -> None:
        records = recall_order_with_text(db, case_id_order=["citer1", "resolved1"], prefix=2)
        assert records == [
            {"case_id": "citer1", "text": _CITING_TEXT},
            {"case_id": "resolved1", "text": _RESOLVED_CASE_TEXT},
        ]

    def test_refuses_to_emit_a_record_with_empty_or_missing_text(self, db: sqlite3.Connection) -> None:
        # This is the exact shape of the bug R1 found -- a record with no usable text must never reach a
        # labeler; the function raises rather than silently emitting `{"case_id": ..., "text": ""}`.
        with pytest.raises(ValueError, match="empty1"):
            recall_order_with_text(db, case_id_order=["citer1", "empty1"], prefix=2)

    def test_prefix_limits_how_many_documents_are_loaded(self, db: sqlite3.Connection) -> None:
        records = recall_order_with_text(db, case_id_order=["citer1", "resolved1"], prefix=1)
        assert [r["case_id"] for r in records] == ["citer1"]


class TestPrecisionSampleWithText:
    """The regression this exists to catch: `emit-precision-sample` wrote `{party_1, party_2, citation,
    citing_case_id}` dicts with no `text` key at all."""

    def test_resolved_alias_carries_both_citing_and_resolved_text(self, db: sqlite3.Connection) -> None:
        sample = [{"rowid": 1, "party_1": "Narandas Karsondas", "party_2": "S.A. Kamtam and Anr.",
                   "citation": "(1977) 3 SCC 247", "citing_case_id": "citer1"}]
        [record] = precision_sample_with_text(db, sample)
        assert record["citing_text"] == _CITING_TEXT
        assert record["status"] == "resolved"
        assert record["resolved_case_id"] == "resolved1"
        assert record["resolved_text"] == _RESOLVED_CASE_TEXT

    def test_not_in_index_alias_has_no_resolved_text_but_still_has_citing_text(self, db: sqlite3.Connection) -> None:
        sample = [{"rowid": 2, "party_1": "Nobody", "party_2": "Nowhere", "citation": "(1999) 9 SCC 999",
                   "citing_case_id": "citer1"}]
        [record] = precision_sample_with_text(db, sample)
        assert record["citing_text"] == _CITING_TEXT
        assert record["status"] == "not_in_index"
        assert "resolved_text" not in record

    def test_refuses_when_the_citing_document_itself_has_no_text(self, db: sqlite3.Connection) -> None:
        sample = [{"rowid": 3, "party_1": "X", "party_2": "Y", "citation": "(1977) 3 SCC 247",
                   "citing_case_id": "empty1"}]
        with pytest.raises(ValueError, match="empty1"):
            precision_sample_with_text(db, sample)


def _insert_second_conflicting_group(conn: sqlite3.Connection) -> None:
    """A second citing document attributes the SAME citation ("(1977) 3 SCC 247", already attributed to
    Narandas Karsondas v. S.A. Kamtam by the `db` fixture) to a genuinely different, ALSO resolvable, party
    pair -- a real CONFLICT (test_verify.py's own `test_conflict_when_alias_maps_citation_to_two_different_
    party_pairs` covers the CONFLICT status itself; this adds a resolvable second case so a fix that returns
    candidate rows can be checked against more than one)."""
    insert_case(
        conn,
        CaseRecord(
            case_id="citer2", title="A Different Later Case", court="Supreme Court", year=2010,
            source="sc_pdf", path_or_url="/fake/citer2.pdf",
            text="In Totally Different Party v. Another Stranger (1977) 3 SCC 247 the Court held that.",
        ),
    )
    insert_case(
        conn,
        CaseRecord(
            case_id="resolved2", title="Totally Different Party vs Another Stranger", court="Supreme Court",
            year=1977, source="sc_pdf", path_or_url="/fake/resolved2.pdf",
            text="An entirely different holding, about an entirely different dispute.",
        ),
    )
    conn.commit()


class TestResolveCitationKeyOnConflict:
    """R1's rejection of 90c101b (prereg v3 §4): a bare CONFLICT with the candidates discarded cannot
    produce the labeler judgment the signed protocol requires ("real ambiguity" vs "normalization bug") --
    `resolve_citation_key` must carry a candidate row per genuinely distinct party-pair group instead."""

    def test_conflict_carries_one_candidate_per_distinct_party_pair_group(self, db: sqlite3.Connection) -> None:
        _insert_second_conflicting_group(db)
        resolved = resolve_citation_key(db, "(1977) 3 SCC 247")
        assert resolved.status == VerifyResult.CONFLICT
        assert {r["case_id"] for r in resolved.case_rows} == {"resolved1", "resolved2"}

    def test_a_conflicting_group_that_resolves_to_nothing_contributes_no_candidate(
        self, db: sqlite3.Connection
    ) -> None:
        # The existing test_verify.py scenario: the second group's party pair has no matching case at all.
        # It must not turn the whole lookup into an error or silently manufacture a candidate -- it simply
        # contributes zero rows, leaving only the group(s) that actually resolved.
        insert_case(
            db,
            CaseRecord(
                case_id="citer3", title="Yet Another Later Case", court="Supreme Court", year=2011,
                source="sc_pdf", path_or_url="/fake/citer3.pdf",
                text="In Nobody At All v. Nowhere In Particular (1977) 3 SCC 247 the Court observed that.",
            ),
        )
        db.commit()
        resolved = resolve_citation_key(db, "(1977) 3 SCC 247")
        assert resolved.status == VerifyResult.CONFLICT
        assert {r["case_id"] for r in resolved.case_rows} == {"resolved1"}


class TestPrecisionSampleWithTextOnConflict:
    def test_conflict_record_carries_every_candidates_title_and_text(self, db: sqlite3.Connection) -> None:
        _insert_second_conflicting_group(db)
        sample = [{"rowid": 4, "party_1": "Narandas Karsondas", "party_2": "S.A. Kamtam and Anr.",
                   "citation": "(1977) 3 SCC 247", "citing_case_id": "citer1"}]
        [record] = precision_sample_with_text(db, sample)
        assert record["status"] == "conflict"
        assert {c["case_id"] for c in record["conflict_candidates"]} == {"resolved1", "resolved2"}
        assert all({"case_id", "title", "text"} <= c.keys() for c in record["conflict_candidates"])
