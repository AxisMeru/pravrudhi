"""The engine writes a parent for every candidate and has never once read one back.

`lineage` is set to `[incumbent_id]` when a candidate is built and no part of the loop consults it again. Two
selection arms were written against it — `gear` ranks by an ancestor's standing in the archive, `hgm` shrinks a
candidate's posterior toward its lineage's — and both take the parent map as an optional argument that nothing
supplies. Their docstrings say so outright: "Pass `lineage` to get the parent-standing rule the reduction is
written for."

So these tests are about supplying it. The fold has to reproduce the real ledger's ancestry exactly, including the
fact that the whole recorded history descends from only two candidates, and the arms have to actually behave
differently once they are given it. An arm that scores the same with the map as without it has not received it.
"""

from __future__ import annotations

import json
from pathlib import Path

from pravrudhi.application.archive import (
    ancestry_report,
    parent_map,
    selection_pressure,
)
from pravrudhi.application.policies import gear_scores, hgm_scores


class _Belief:
    def __init__(self, mu: float, sigma2: float, n_obs: int) -> None:
        self.mu, self.sigma2, self.n_obs = mu, sigma2, n_obs


class _Citta:
    """The smallest stand-in for the kernel's Citta that the selection arms actually read."""

    def __init__(self, beliefs: dict[str, tuple[float, float, int]]) -> None:
        self.candidates = {c: _Belief(*v) for c, v in beliefs.items()}


def _ledger(tmp_path: Path, rows: list[dict[str, object]]) -> Path:
    path = tmp_path / "ledger.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return path


def _propose(cid: str, lineage: list[str] | None) -> dict[str, object]:
    payload: dict[str, object] = {"op": "adapter"}
    if lineage is not None:
        payload["lineage"] = lineage
    return {"kind": "propose", "candidate_id": cid, "payload": payload}


class TestTheFold:
    def test_a_candidate_maps_to_the_nearest_parent_its_lineage_names(self, tmp_path: Path) -> None:
        path = _ledger(tmp_path, [_propose("c-0001", ["c-0000"])])
        assert parent_map(path) == {"c-0001": "c-0000"}

    def test_a_baseline_with_no_lineage_is_a_root(self, tmp_path: Path) -> None:
        """`c-0000` is the unmodified trainee. It has no parent and must not be invented one."""
        path = _ledger(tmp_path, [_propose("c-0000", None), _propose("c-0001", ["c-0000"])])
        assert parent_map(path)["c-0000"] is None

    def test_the_last_entry_is_the_nearest_parent(self, tmp_path: Path) -> None:
        """Lineage is written root-first, so the nearest ancestor is the final element."""
        path = _ledger(tmp_path, [_propose("c-0002", ["c-0000", "c-0001"])])
        assert parent_map(path) == {"c-0002": "c-0001"}

    def test_a_candidate_naming_itself_as_parent_is_dropped(self, tmp_path: Path) -> None:
        """A ledger repair once made this possible; a self-edge would be a cycle the arms must never walk."""
        path = _ledger(tmp_path, [_propose("c-0007", ["c-0007"])])
        assert parent_map(path)["c-0007"] is None

    def test_rows_that_are_not_proposals_are_ignored(self, tmp_path: Path) -> None:
        path = _ledger(tmp_path, [
            _propose("c-0001", ["c-0000"]),
            {"kind": "observe", "candidate_id": "c-0001", "payload": {"lineage": ["c-9999"]}},
        ])
        assert parent_map(path) == {"c-0001": "c-0000"}

    def test_a_corrupt_line_is_skipped_rather_than_fatal(self, tmp_path: Path) -> None:
        path = tmp_path / "ledger.jsonl"
        path.write_text(json.dumps(_propose("c-0001", ["c-0000"])) + "\n{not json\n")
        assert parent_map(path) == {"c-0001": "c-0000"}

    def test_a_missing_ledger_is_an_empty_map_not_a_crash(self, tmp_path: Path) -> None:
        assert parent_map(tmp_path / "nowhere.jsonl") == {}


class TestTheReportDescribesTheSearchHonestly:
    def test_a_star_is_reported_as_one_generation_deep(self, tmp_path: Path) -> None:
        """Every child hanging off one root is depth 1, however many children there are."""
        rows = [_propose("c-0000", None)] + [_propose(f"c-{i:04d}", ["c-0000"]) for i in range(1, 6)]
        report = ancestry_report(parent_map(_ledger(tmp_path, rows)))
        assert report.roots == 1
        assert report.max_depth == 1
        assert report.distinct_parents == 1

    def test_a_chain_is_reported_at_its_true_depth(self, tmp_path: Path) -> None:
        rows = [_propose("c-0000", None)] + [
            _propose(f"c-{i:04d}", [f"c-{i - 1:04d}"]) for i in range(1, 5)
        ]
        report = ancestry_report(parent_map(_ledger(tmp_path, rows)))
        assert report.max_depth == 4
        assert report.distinct_parents == 4

    def test_the_widest_parent_is_named_with_its_child_count(self, tmp_path: Path) -> None:
        rows = [_propose("c-0000", None), _propose("c-0001", ["c-0000"])] + [
            _propose(f"c-{i:04d}", ["c-0001"]) for i in range(2, 9)
        ]
        report = ancestry_report(parent_map(_ledger(tmp_path, rows)))
        assert report.widest == ("c-0001", 7)


class TestTheArmsActuallyReceiveIt:
    """If an arm scores identically with the map and without it, the map is not reaching it."""

    def test_gear_ranks_by_ancestor_standing_only_once_it_has_the_map(self) -> None:
        # c-0100 descends from the archive's best, c-0200 from its worst. Neither has evidence of its own, so
        # without a map gear cannot tell them apart and gives both the novelty score.
        citta = _Citta({"c-0000": (0.9, 0.1, 5), "c-0001": (0.1, 0.1, 5)})
        pool = ["c-0100", "c-0200"]

        blind = gear_scores(citta, pool, None)
        assert blind["c-0100"] == blind["c-0200"], "with no map both are simply novel"

        informed = gear_scores(citta, pool, {"c-0100": "c-0000", "c-0200": "c-0001"})
        assert informed["c-0100"] > informed["c-0200"], "the better lineage must outrank the worse one"

    def test_gear_still_awards_novelty_to_a_lineage_the_archive_cannot_reach(self) -> None:
        citta = _Citta({"c-0000": (0.9, 0.1, 5), "c-0001": (0.1, 0.1, 5)})
        scores = gear_scores(citta, ["c-0100", "c-0300"], {"c-0100": "c-0000", "c-0300": "c-8888"})
        assert scores["c-0300"] == 0.5, "an unreachable ancestor is the unexplored lineage, not the worst one"
        assert scores["c-0100"] > scores["c-0300"]

    def test_hgm_shrinks_toward_the_named_parent_rather_than_the_whole_archive(self) -> None:
        citta = _Citta({"c-0000": (1.0, 0.1, 10), "c-0001": (0.0, 0.1, 10), "c-0100": (0.5, 0.1, 0)})

        blind = hgm_scores(citta, ["c-0100"], None)["c-0100"]
        informed = hgm_scores(citta, ["c-0100"], {"c-0100": "c-0000"})["c-0100"]

        assert informed != blind, "the map must change the shrinkage target"
        assert informed > blind, "shrinking toward the strong parent must beat shrinking toward the whole archive"

    def test_a_candidate_whose_lineage_carries_no_evidence_keeps_its_own_estimate(self) -> None:
        citta = _Citta({"c-0100": (0.42, 0.1, 3)})
        assert hgm_scores(citta, ["c-0100"], {"c-0100": "c-7777"})["c-0100"] == 0.42


class TestAgainstTheRealLedger:
    """The recorded history is a two-node star: 189 proposals descended from exactly two ancestors."""

    def test_the_committed_ledger_folds_to_two_parents(self) -> None:
        path = Path("research/ledger.jsonl")
        if not path.exists():
            return  # the ledger is local-only; the fold is covered by the fixtures above
        report = ancestry_report(parent_map(path))
        assert report.distinct_parents == 2, (
            f"the real search has {report.distinct_parents} distinct parents, not 2; "
            "the fold disagrees with the ledger"
        )
        assert report.max_depth <= 2


class TestTheLoopSuppliesIt:
    """The arms accepting a map proves nothing if the loop never passes one. This is that wiring."""

    def test_deliberate_hands_a_lineage_arm_the_folded_map(self, tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        from pravrudhi.application import deliberate as deliberate_mod
        from pravrudhi.application.propose import propose
        from tests.test_propose_deliberate import BUCKET, ScriptedClient, _root

        root, w = _root(tmp_path)
        propose(
            root, w, ScriptedClient(), night=1, k=4, model="Qwen/Qwen3-4B", bucket=BUCKET,
            prompts_dir=root / "harness" / "prompts",
            sealed_dir=root / ".pravrudhi" / "kernel" / "sealed" / "predictions",
            incumbent_id="c-0000", sigma_seed=0.03, temperature=0.7, max_tokens=512,
            rethink_m=6, log=lambda s: None,
        )

        seen: dict[str, object] = {}
        real = deliberate_mod.rank_scores

        def spy(policy, citta, pool, rng, lineage=None):  # type: ignore[no-untyped-def]
            seen["lineage"] = lineage
            return real(policy, citta, pool, rng, lineage)

        monkeypatch.setattr(deliberate_mod, "rank_scores", spy)
        deliberate_mod.deliberate(
            root, w, night=1, budget_gpu_h=3.0, sigma_seed=0.03, incumbent_id="c-0000",
            harness_hash="h" * 64, model_hash="m" * 64, rng_seed=1, log=lambda s: None,
            selection_policy="gear",
        )

        lineage = seen["lineage"]
        assert isinstance(lineage, dict) and lineage, "the arm was handed no parent map at all"
        assert lineage["c-0000"] is None, "the baseline is a root"
        assert lineage["c-0001"] == "c-0000", "a proposed candidate must carry its real parent"


class TestSelectionPressure:
    """A selection rule earns its keep only when the pool exceeds the budget. Often here, it has not."""

    def _ledger(self, tmp_path: Path, rows: list[dict[str, object]]) -> Path:
        p = tmp_path / "ledger.jsonl"
        p.write_text("".join(json.dumps(r) + "\n" for r in rows))
        return p

    def _row(self, kind: str, cid: str, night: int, bench: str = "b") -> dict[str, object]:
        return {"kind": kind, "candidate_id": cid, "night": night, "bucket": {"task_family": bench}}

    def test_a_night_that_ran_everything_it_had_decided_nothing(self, tmp_path: Path) -> None:
        rows = [self._row("propose", "c-1", 1), self._row("select", "c-1", 1)]
        p = selection_pressure(self._ledger(tmp_path, rows))[0]
        assert p.live == 1 and p.selected == 1 and p.declined == 0 and not p.binding

    def test_a_night_that_left_candidates_on_the_table_made_a_choice(self, tmp_path: Path) -> None:
        rows = [
            self._row("propose", "c-1", 1), self._row("propose", "c-2", 1),
            self._row("select", "c-1", 1),
        ]
        p = selection_pressure(self._ledger(tmp_path, rows))[0]
        assert p.live == 2 and p.selected == 1 and p.declined == 1 and p.binding

    def test_a_candidate_pruned_during_a_night_was_still_a_choice_that_night(self, tmp_path: Path) -> None:
        """Pruning follows evaluation, so the candidate was live when the night chose. It dies from the next one."""
        rows = [
            self._row("propose", "c-1", 1), self._row("propose", "c-2", 1),
            self._row("select", "c-1", 1), self._row("prune", "c-2", 1),
            self._row("propose", "c-3", 2), self._row("select", "c-3", 2),
        ]
        first, second = selection_pressure(self._ledger(tmp_path, rows))
        assert first.live == 2 and first.binding, "c-2 was available when night 1 chose"
        assert second.live == 2 and second.declined == 1, "night 2 sees c-1 and c-3, not the pruned c-2"

    def test_a_candidate_on_another_bench_is_not_a_forgone_choice(self, tmp_path: Path) -> None:
        rows = [
            self._row("propose", "elsewhere", 1, "other"), self._row("propose", "c-1", 1),
            self._row("select", "c-1", 1),
        ]
        assert not selection_pressure(self._ledger(tmp_path, rows))[0].binding

    def test_the_real_ledger_shows_the_budget_rarely_bound(self) -> None:
        path = Path("research/ledger.jsonl")
        if not path.exists():
            return
        rows = selection_pressure(path)
        recent = [p for p in rows if p.night >= 7]
        assert recent, "the ledger should carry nights from 7 onward"
        assert sum(p.binding for p in recent) <= 2, (
            "if selection started binding regularly, this measurement and the plan built on it need revisiting"
        )
