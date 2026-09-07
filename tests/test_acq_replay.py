"""Score a selection rule on nights that have already been run, without spending a rotation.

The engine has 189 proposals and 526 observations on disk, and eleven of its nights measured every candidate they
proposed. On those nights a counterfactual selection rule can be graded exactly: rebuild what the loop believed
before it chose, let the rule choose instead, and look up what the candidate it picked actually scored. Nothing is
simulated and no model runs.

That makes the meta-level affordable, which matters because the live version is not. The sealed pools are nearly
spent, and a policy comparison powered to detect the effect sizes this project actually sees needs several times
the rotations that remain.

The bench has a bias it cannot remove and must therefore declare. The measured set was curated by the arm that
actually ran, so a challenger is being asked to re-order a shortlist its rival drew up. A challenger that wins has
won on unfriendly ground; a challenger that loses may only have lost the curation. The statistic is the area under
the best-so-far curve at matched evaluation count, which is what makes two rules comparable when they spend the
same budget and disagree about where to spend it.
"""

from __future__ import annotations

from pravrudhi.application.acq_replay import auc, best_so_far


class TestBestSoFar:
    def test_the_curve_never_falls(self) -> None:
        assert best_so_far([0.1, 0.3, 0.2, 0.5, 0.4]) == [0.1, 0.3, 0.3, 0.5, 0.5]

    def test_a_rule_that_finds_the_best_first_dominates_one_that_finds_it_last(self) -> None:
        early = best_so_far([0.9, 0.1, 0.1])
        late = best_so_far([0.1, 0.1, 0.9])
        assert early == [0.9, 0.9, 0.9]
        assert late == [0.1, 0.1, 0.9]
        assert auc(early) > auc(late), "finding the winner sooner is the whole point of a selection rule"

    def test_two_rules_that_find_the_same_thing_at_the_same_time_tie(self) -> None:
        assert auc(best_so_far([0.4, 0.7])) == auc(best_so_far([0.4, 0.7]))

    def test_an_empty_run_scores_nothing_rather_than_crashing(self) -> None:
        assert best_so_far([]) == []
        assert auc([]) == 0.0


class TestTheStatisticIsBudgetMatched:
    def test_a_longer_run_is_not_rewarded_for_being_longer(self) -> None:
        """AUC is a mean, not a sum: a rule does not win by being given more evaluations."""
        short = auc(best_so_far([0.5, 0.5]))
        long = auc(best_so_far([0.5, 0.5, 0.5, 0.5]))
        assert short == long

    def test_a_negative_score_is_carried_rather_than_clipped(self) -> None:
        """Most candidates score below the incumbent; clipping at zero would hide the arm's real cost."""
        assert best_so_far([-0.3, -0.1]) == [-0.3, -0.1]
        assert auc([-0.3, -0.1]) < 0


class TestThePoolIsTheLivePoolOnTheNightsOwnBench:
    """Two bugs lived here, and each one produced a confident, wrong answer."""

    def _ledger(self, tmp_path, rows):  # type: ignore[no-untyped-def]
        import json
        p = tmp_path / "ledger.jsonl"
        p.write_text("".join(json.dumps(r) + "\n" for r in rows))
        return p

    def _scored(self, spec):  # type: ignore[no-untyped-def]
        from pravrudhi.application.acq_replay import Scored
        return {c: Scored(c, n, b, 0.5, a, "e") for c, (n, b, a) in spec.items()}

    def test_a_candidate_from_an_earlier_night_is_still_selectable(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """Scoring only the night's own proposals made the budget look non-binding and every arm identical."""
        from pravrudhi.application.acq_replay import decisions

        rows = [
            {"kind": "propose", "candidate_id": "c-1", "night": 1, "bucket": {"task_family": "b"}},
            {"kind": "propose", "candidate_id": "c-2", "night": 2, "bucket": {"task_family": "b"}},
            {"kind": "select", "candidate_id": "c-2", "night": 2, "bucket": {"task_family": "b"}},
        ]
        scored = self._scored({"c-1": (1, "b", 0.1), "c-2": (2, "b", 0.2)})
        night2 = [d for d in decisions(self._ledger(tmp_path, rows), scored) if d.night == 2][0]
        assert set(night2.pool) == {"c-1", "c-2"}, "a live candidate from an earlier night is still a choice"
        assert night2.budget == 1 and night2.binding

    def test_a_pruned_candidate_leaves_the_live_pool(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        from pravrudhi.application.acq_replay import decisions

        rows = [
            {"kind": "propose", "candidate_id": "c-1", "night": 1, "bucket": {"task_family": "b"}},
            {"kind": "prune", "candidate_id": "c-1", "night": 1, "bucket": {"task_family": "b"}},
            {"kind": "propose", "candidate_id": "c-2", "night": 2, "bucket": {"task_family": "b"}},
            {"kind": "select", "candidate_id": "c-2", "night": 2, "bucket": {"task_family": "b"}},
        ]
        scored = self._scored({"c-1": (1, "b", 0.9), "c-2": (2, "b", 0.2)})
        night2 = [d for d in decisions(self._ledger(tmp_path, rows), scored) if d.night == 2][0]
        assert set(night2.pool) == {"c-2"}

    def test_a_candidate_from_another_bench_is_not_an_alternative(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """Anchored scores are comparable within a pool and not across pools.

        Without this restriction every counterfactual arm reached back to a handful of high-scoring candidates
        on a wider-spread bench and appeared to beat the arm that actually ran. That was pooling, not a result.
        """
        from pravrudhi.application.acq_replay import decisions

        rows = [
            {"kind": "propose", "candidate_id": "easy", "night": 1, "bucket": {"task_family": "other"}},
            {"kind": "propose", "candidate_id": "here", "night": 2, "bucket": {"task_family": "b"}},
            {"kind": "select", "candidate_id": "here", "night": 2, "bucket": {"task_family": "b"}},
        ]
        scored = self._scored({"easy": (1, "other", 0.9), "here": (2, "b", 0.01)})
        night2 = [d for d in decisions(self._ledger(tmp_path, rows), scored) if d.night == 2][0]
        assert set(night2.pool) == {"here"}, "a candidate scored on another bench is not a choice on this one"

    def test_a_night_that_could_afford_its_whole_pool_decided_nothing(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        from pravrudhi.application.acq_replay import decisions

        rows = [
            {"kind": "propose", "candidate_id": "c-1", "night": 1, "bucket": {"task_family": "b"}},
            {"kind": "select", "candidate_id": "c-1", "night": 1, "bucket": {"task_family": "b"}},
        ]
        scored = self._scored({"c-1": (1, "b", 0.1)})
        assert not decisions(self._ledger(tmp_path, rows), scored)[0].binding
