"""Make two artifacts measured on different held-out rotations comparable.

Every score this project has is a pass rate on one rotation of a sealed pool, and rotations differ in difficulty.
The loop copes by pairing: a candidate is always run against the incumbent on the same rotation and seed, and the
difference is what the boundary reads. That works, and nothing here changes it.

It stops working the moment an archive keeps candidates across generations, because a delta is a comparison
against whichever incumbent was standing at the time. Two deltas measured against two different incumbents are on
two different scales, and pooling them is exactly the defect the kernel's dead rebase path was written to prevent.

The way out is to anchor on the items rather than on the incumbent. An item's difficulty is estimated from every
observation that has ever scored it, leaving out the observation being scored, and an artifact's anchored score is
its pass rate minus the mean difficulty of the items it happened to draw. That quantity is defined against the
pool, so it is comparable across generations, and it costs no GPU time because every number it needs is already
recorded.

Leave-one-out is not a refinement here, it is the whole correctness argument: an in-sample estimate lets an
artifact set the very baseline it is then scored against, which flatters an artifact that ran on items nothing
else has touched.
"""

from __future__ import annotations

from pravrudhi.application.anchor import anchored, difficulty


class TestItemDifficulty:
    def test_an_item_everyone_passes_is_recorded_as_easy(self) -> None:
        d = difficulty([{"x": 1}, {"x": 1}, {"x": 1}])
        assert d.trials["x"] == 3 and d.successes["x"] == 3

    def test_an_item_seen_once_is_counted_but_cannot_be_anchored_against(self) -> None:
        """Its only evidence is the observation being scored, so leaving that out leaves nothing."""
        d = difficulty([{"lonely": 1}])
        assert d.trials["lonely"] == 1
        assert d.leave_one_out("lonely", 1) is None


class TestLeaveOneOut:
    def test_the_observation_being_scored_is_excluded_from_its_own_baseline(self) -> None:
        # "x" seen three times: 1, 1, 0. Two successes in three trials.
        d = difficulty([{"x": 1}, {"x": 1}, {"x": 0}])
        # Scoring one of the passes: (2 - 1) / (3 - 1) = 0.5
        assert d.leave_one_out("x", 1) == 0.5
        # Scoring the failure: (2 - 0) / (3 - 1) = 1.0
        assert d.leave_one_out("x", 0) == 1.0

    def test_an_unknown_item_has_no_baseline(self) -> None:
        assert difficulty([{"x": 1}]).leave_one_out("nowhere", 1) is None


class TestTheAnchoredScore:
    def test_an_artifact_on_easy_items_loses_the_advantage_the_items_gave_it(self) -> None:
        others = [{"e1": 1, "e2": 1}] * 4
        result = anchored({"e1": 1, "e2": 1}, value=1.0, table=difficulty([*others, {"e1": 1, "e2": 1}]))
        assert result.anchored == 0.0, "scoring full marks on items everyone passes is worth nothing"
        assert result.n_anchored == 2

    def test_an_artifact_on_hard_items_is_credited_for_them(self) -> None:
        others = [{"h1": 0, "h2": 0}] * 4
        result = anchored({"h1": 1, "h2": 1}, value=1.0, table=difficulty([*others, {"h1": 1, "h2": 1}]))
        assert result.anchored == 1.0 - 0.0, "passing items everyone fails is worth the full margin"

    def test_two_artifacts_on_rotations_of_different_difficulty_become_comparable(self) -> None:
        """The property the archive needs: raw scores disagree, anchored scores agree."""
        history = [{"e1": 1, "e2": 1, "h1": 0, "h2": 0} for _ in range(9)]
        easy_run = {"e1": 1, "e2": 1}
        hard_run = {"h1": 1, "h2": 1}
        table = difficulty([*history, easy_run, hard_run])

        a = anchored(easy_run, value=1.0, table=table)
        b = anchored(hard_run, value=1.0, table=table)

        assert a.value > 0 and b.value > 0
        assert a.anchored < b.anchored, (
            "full marks on the easy rotation must not outrank full marks on the hard one"
        )

    def test_items_with_no_baseline_are_excluded_and_the_shortfall_is_reported(self) -> None:
        table = difficulty([{"known": 1}, {"known": 0}, {"known": 1, "fresh": 1}])
        result = anchored({"known": 1, "fresh": 1}, value=1.0, table=table)
        assert result.n_items == 2
        assert result.n_anchored == 1, "only `known` has evidence outside this observation"

    def test_an_observation_with_nothing_anchorable_reports_no_anchored_score(self) -> None:
        """Returning the raw value here would silently pass an un-anchored number as an anchored one."""
        result = anchored({"fresh": 1}, value=1.0, table=difficulty([{"fresh": 1}]))
        assert result.anchored is None and result.n_anchored == 0


class TestReproducibility:
    def test_the_table_carries_a_hash_so_a_cited_score_can_be_reproduced(self) -> None:
        """Difficulty estimates sharpen as nights run, so a bare anchored score is not reproducible."""
        a = difficulty([{"x": 1}, {"y": 0}])
        b = difficulty([{"y": 0}, {"x": 1}])
        assert a.epoch == b.epoch, "the same evidence in a different order is the same table"

    def test_the_hash_moves_when_the_evidence_moves(self) -> None:
        a = difficulty([{"x": 1}])
        b = difficulty([{"x": 1}, {"x": 0}])
        assert a.epoch != b.epoch
