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
    _serialize_item_line,
    _serialize_observation,
    _serialize_world,
    lean_source_commit,
    score_bin_path,
    score_gold_set,
    wilson,
)

# prabhasa-nyaya main's own build (Score.lean, isSatpratipaksa and isBadhita are all merged there as of
# f47267f). See docs/decisions/plan-A2-hetvabhasa-gold-set.md (prabhasa-nyaya repo) for the wiring this is
# part of.
_LEAN_SCORE_BIN = Path("/home/ss/projects/prabhasa-nyaya/lean/.lake/build/bin/score")
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


def test_serialize_observation_is_the_lean_scorers_field_order() -> None:
    assert _serialize_observation({"polarity": "absent", "locus": "mountain", "prop": "cold"}) == \
        "absent:mountain:cold"


def test_serialize_item_line_is_five_fields_for_a_decidable_class() -> None:
    item = {
        "id": "valid-0000", "paksa": "mountain", "sadhya": "fire", "hetu": "smoke",
        "world": {"mountain": ["smoke", "fire"]}, "expected": "valid",
    }
    assert _serialize_item_line(item) == "valid-0000\tmountain\tfire\tsmoke\tmountain:smoke,fire"


def test_serialize_item_line_is_six_fields_for_badhita() -> None:
    item = {
        "id": "badhita-0000", "paksa": "mountain", "sadhya": "cold", "hetu": "substance",
        "world": {"mountain": ["smoke", "fire"]}, "expected": "badhita",
        "defeating_source": {
            "pramana": "constructed",
            "observation": {"polarity": "absent", "locus": "mountain", "prop": "cold"},
        },
    }
    line = _serialize_item_line(item)
    assert line.count("\t") == 5  # 6 fields
    assert line == "badhita-0000\tmountain\tcold\tsubstance\tmountain:smoke,fire\tabsent:mountain:cold"


def test_serialize_item_line_is_nine_fields_for_satpratipaksa() -> None:
    item = {
        "id": "satpratipaksa-0000", "paksa": "sound", "sadhya": "eternal", "hetu": "audible",
        "world": {"sound": ["audible", "eternal"], "space": ["audible", "eternal"]},
        "expected": "satpratipaksa",
        "counter_inference": {
            "paksa": "sound", "sadhya": "non-eternal", "hetu": "produced",
            "world": {"sound": ["produced", "non-eternal"], "pot": ["produced", "non-eternal"]},
        },
    }
    line = _serialize_item_line(item)
    assert line.count("\t") == 8  # 9 fields
    assert line == (
        "satpratipaksa-0000\tsound\teternal\taudible\tsound:audible,eternal;space:audible,eternal"
        "\tsound\tnon-eternal\tproduced\tsound:produced,non-eternal;pot:produced,non-eternal"
    )


def test_lean_source_commit_is_none_off_a_path_with_no_git_ancestor(tmp_path: Path) -> None:
    assert lean_source_commit(tmp_path / "bin" / "score") is None


@requires_lean_scorer
class TestAgainstTheRealLeanScorer:
    def test_every_class_scores_perfectly(self) -> None:
        """The gold set's labels and prabhasa-nyaya's Lean deciders are independent implementations of the
        same rules -- `Verdict.of` cross-checked in tests/test_nyaya_gold.py against the fixture world,
        `isSatpratipaksa`/`isBadhita` built to match exactly how this generator constructs those two banks --
        so perfect agreement on all six classes is the expected outcome, not a coincidence to relax the test
        over. If this ever regresses, session-3's standing instruction is to report it, not tune it away."""
        items = build_gold_set(per_class=15, seed=0)
        result = score_gold_set(items, _LEAN_SCORE_BIN)
        for cls in DECIDED_CLASSES:
            row = result["per_class"][cls]
            assert row["decided"] is True
            assert row["n"] >= 15
            assert row["pass_rate"] == 1.0, (cls, row)

    def test_no_class_is_reported_not_decided_any_more(self) -> None:
        assert NOT_DECIDED_CLASSES == ()
        items = build_gold_set(per_class=15, seed=0)
        result = score_gold_set(items, _LEAN_SCORE_BIN)
        assert result["not_decided_classes"] == []

    def test_the_headline_pass_rate_is_the_worst_of_all_six_not_a_pooled_average(self) -> None:
        items = build_gold_set(per_class=15, seed=0)
        result = score_gold_set(items, _LEAN_SCORE_BIN)
        decided_rates = [result["per_class"][c]["pass_rate"] for c in result["decided_classes"]]
        assert result["pass_rate"] == min(decided_rates)
        assert set(result["decided_classes"]) == set(DECIDED_CLASSES)
        assert len(result["decided_classes"]) == 6

    def test_the_lean_source_commit_is_recorded(self) -> None:
        items = build_gold_set(per_class=2, seed=0)
        result = score_gold_set(items, _LEAN_SCORE_BIN)
        assert result["lean_source_commit"]
        assert len(result["lean_source_commit"]) == 40  # a full sha, not an abbreviation
