"""Scoring the constructed hetvabhasa gold set against prabhasa-nyaya's real Lean `score` executable --
integration, not a fake: session-3's A2 decision 2 names the Lean verifier of record specifically so this
module is never tested against a stand-in for it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pravrudhi.application.nyaya_gold import build_gold_set
from pravrudhi.application.nyaya_gold_score import (
    DECIDED_CLASSES,
    NOT_DECIDED_CLASSES,
    _serialize_world,
    score_bin_path,
    score_gold_set,
    wilson,
)

# The assistant-trackA worktree's own build, since prabhasa-nyaya/main does not yet have Score.lean merged.
# See docs/decisions/plan-A2-hetvabhasa-gold-set.md (prabhasa-nyaya repo) for the wiring this is part of.
_LEAN_SCORE_BIN = Path(
    "/home/ss/projects/prabhasa-nyaya/.worktrees/assistant-trackA/lean/.lake/build/bin/score"
)
requires_lean_scorer = pytest.mark.skipif(
    not _LEAN_SCORE_BIN.exists(), reason="prabhasa-nyaya's Lean `score` executable is not built at this path"
)


def test_wilson_gives_the_zero_over_zero_interval_no_evidence_shape() -> None:
    assert wilson(0, 0) == (0.0, 0.0)


def test_wilson_narrows_towards_the_point_estimate_as_n_grows() -> None:
    lo_small, hi_small = wilson(8, 8)
    lo_big, hi_big = wilson(800, 800)
    assert lo_small < lo_big
    assert hi_small - lo_small > hi_big - lo_big


def test_serialize_world_is_the_lean_scorers_line_protocol() -> None:
    world = {"mountain": ["smoke", "fire"], "lake": ["water"]}
    assert _serialize_world(world) == "mountain:smoke,fire;lake:water"


def test_score_bin_path_honours_the_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PRABHASA_NYAYA_SCORE_BIN", "/tmp/somewhere/score")
    assert score_bin_path(Path("/does/not/matter")) == Path("/tmp/somewhere/score")


def test_score_bin_path_reads_the_configured_path_when_no_env_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("PRABHASA_NYAYA_SCORE_BIN", raising=False)
    (tmp_path / "configs").mkdir()
    (tmp_path / "configs" / "nyaya_gold.yaml").write_text("score_bin: elsewhere/score\n")
    assert score_bin_path(tmp_path) == (tmp_path / "elsewhere" / "score").resolve()


def test_score_bin_path_falls_back_to_the_sibling_default_with_no_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("PRABHASA_NYAYA_SCORE_BIN", raising=False)
    assert score_bin_path(tmp_path) == (tmp_path / "../prabhasa-nyaya/lean/.lake/build/bin/score").resolve()


def test_scoring_a_missing_binary_raises_a_clear_error(tmp_path: Path) -> None:
    items = build_gold_set(per_class=2, seed=0)
    with pytest.raises(FileNotFoundError, match="not built"):
        score_gold_set(items, tmp_path / "no-such-binary")


@requires_lean_scorer
class TestAgainstTheRealLeanScorer:
    def test_every_decided_class_scores_perfectly(self) -> None:
        """The gold set's labels and the Lean verifier are two independent implementations of the same
        classical rules (already cross-checked in tests/test_nyaya_gold.py against the fixture world), so
        perfect agreement on the four decidable classes is the expected outcome, not a coincidence to relax
        the test over."""
        items = build_gold_set(per_class=15, seed=0)
        result = score_gold_set(items, _LEAN_SCORE_BIN)
        for cls in DECIDED_CLASSES:
            row = result["per_class"][cls]
            assert row["decided"] is True
            assert row["n"] >= 15
            assert row["pass_rate"] == 1.0, (cls, row)

    def test_not_decided_classes_are_reported_not_scored(self) -> None:
        items = build_gold_set(per_class=15, seed=0)
        result = score_gold_set(items, _LEAN_SCORE_BIN)
        for cls in NOT_DECIDED_CLASSES:
            row = result["per_class"][cls]
            assert row["decided"] is False
            assert row["note"] == "not decided (heuristic path)"
            assert row["n"] >= 15
            assert "pass_rate" not in row

    def test_the_headline_pass_rate_is_the_worst_decided_class_not_a_pooled_average(self) -> None:
        items = build_gold_set(per_class=15, seed=0)
        result = score_gold_set(items, _LEAN_SCORE_BIN)
        decided_rates = [result["per_class"][c]["pass_rate"] for c in result["decided_classes"]]
        assert result["pass_rate"] == min(decided_rates)
        assert set(result["decided_classes"]) == set(DECIDED_CLASSES)
        assert set(result["not_decided_classes"]) == set(NOT_DECIDED_CLASSES)
