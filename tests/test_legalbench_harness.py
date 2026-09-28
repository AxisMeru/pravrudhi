"""Track A P3 (`research/prereg/B1-tier1-amendment-2.md`): closed-book unaided-accuracy scoring for
LegalBench's rule-application tasks (`hearsay`, `personal_jurisdiction`). Binary Yes/No classification, not
the conduct-with-missing-element shape `nyaya_lean_elements.py` checks -- see the amendment for why this
module deliberately makes no Lean-checked claim yet, only a labelled "model-read" one.

Done-when: loads a task's `.tsv` (index/answer/text/slice, and the `diversity_*` shape with no `slice`
column, which gets one task-named slice flagged as a fallback); scores a vendor's answers against gold as
accuracy, per-slice broken out and never pooled; every result carries `"labelled": "model-read"` so no
caller can present it as Lean-checked by omission; a vendor answer missing for an item counts as wrong
rather than being silently excluded (an unanswered item is not evidence of nothing).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pravrudhi.application import legalbench_harness as lbh

_FIXTURE_TSV = (
    "index\tanswer\ttext\tslice\n"
    '0\tYes\tRebecca told Ronald she was unwell.\tStandard hearsay\n'
    '1\tNo\tDavid set a track record.\tNon-assertive conduct\n'
    '2\tNo\tTim told Jimmy his opinion.\tNot introduced to prove truth\n'
)


# `diversity_*`'s own shape: no `slice` column at all (columns per the staged files).
_NO_SLICE_TSV = (
    "index\ttext\tanswer\tparties_are_diverse\taic_is_met\n"
    "0\tAllen is from Texas. Bob is from Texas. Allen sues Bob for $100,000.\tNo\tFalse\tTrue\n"
    "1\tCara is from Ohio. Dan is from Iowa. Cara sues Dan for $80,000.\tYes\tTrue\tTrue\n"
)


@pytest.fixture
def fixture_tsv(tmp_path: Path) -> Path:
    p = tmp_path / "hearsay.test.tsv"
    p.write_text(_FIXTURE_TSV)
    return p


@pytest.fixture
def no_slice_tsv(tmp_path: Path) -> Path:
    task_dir = tmp_path / "diversity_1"
    task_dir.mkdir()
    p = task_dir / "test.tsv"
    p.write_text(_NO_SLICE_TSV)
    return p


class TestLoadTask:
    def test_reads_every_row_with_its_slice(self, fixture_tsv: Path) -> None:
        items = lbh.load_task(fixture_tsv)
        assert len(items) == 3
        assert items[0].index == 0
        assert items[0].answer == "Yes"
        assert items[0].slice == "Standard hearsay"
        assert items[1].answer == "No"

    def test_a_file_with_a_slice_column_is_read_exactly_as_before(self, fixture_tsv: Path) -> None:
        # The `hearsay`/`personal_jurisdiction` shape: every field comes from the file, and nothing is
        # flagged as a fallback.
        assert lbh.load_task(fixture_tsv) == [
            lbh.LegalBenchItem(0, "Yes", "Rebecca told Ronald she was unwell.", "Standard hearsay"),
            lbh.LegalBenchItem(1, "No", "David set a track record.", "Non-assertive conduct"),
            lbh.LegalBenchItem(2, "No", "Tim told Jimmy his opinion.", "Not introduced to prove truth"),
        ]
        assert all(not item.slice_is_task_fallback for item in lbh.load_task(fixture_tsv))

    def test_a_file_with_no_slice_column_gets_one_task_named_slice_flagged_as_such(
        self, no_slice_tsv: Path
    ) -> None:
        items = lbh.load_task(no_slice_tsv)
        assert len(items) == 2
        assert [item.slice for item in items] == ["diversity_1", "diversity_1"]
        assert all(item.slice_is_task_fallback for item in items)
        # The rest of the row is still read straight from the file.
        assert items[1].index == 1
        assert items[1].answer == "Yes"
        assert items[1].text.startswith("Cara is from Ohio.")

    def test_a_missing_answer_column_still_raises(self, tmp_path: Path) -> None:
        p = tmp_path / "no_answer.tsv"
        p.write_text("index\ttext\tslice\n0\tSome facts.\tStandard hearsay\n")
        with pytest.raises(KeyError):
            lbh.load_task(p)

    def test_a_missing_text_column_still_raises(self, tmp_path: Path) -> None:
        p = tmp_path / "no_text.tsv"
        p.write_text("index\tanswer\tslice\n0\tYes\tStandard hearsay\n")
        with pytest.raises(KeyError):
            lbh.load_task(p)


class TestScoreUnaided:
    def test_all_correct_scores_1_0_and_labels_model_read(self, fixture_tsv: Path) -> None:
        items = lbh.load_task(fixture_tsv)
        result = lbh.score_unaided({0: "Yes", 1: "No", 2: "No"}, items)
        assert result["accuracy"] == 1.0
        assert result["n"] == 3
        assert result["labelled"] == "model-read"

    def test_comparison_is_case_insensitive(self, fixture_tsv: Path) -> None:
        items = lbh.load_task(fixture_tsv)
        result = lbh.score_unaided({0: "yes", 1: "no", 2: "NO"}, items)
        assert result["accuracy"] == 1.0

    def test_a_missing_answer_counts_as_wrong_not_excluded(self, fixture_tsv: Path) -> None:
        items = lbh.load_task(fixture_tsv)
        # Item 2 has no answer at all -- must still count in n and count as wrong, not be dropped.
        result = lbh.score_unaided({0: "Yes", 1: "No"}, items)
        assert result["n"] == 3
        assert result["accuracy"] == pytest.approx(2 / 3)

    def test_per_slice_accuracy_is_broken_out_and_never_pooled_away(self, fixture_tsv: Path) -> None:
        items = lbh.load_task(fixture_tsv)
        # Wrong on the "Non-assertive conduct" slice only.
        result = lbh.score_unaided({0: "Yes", 1: "Yes", 2: "No"}, items)
        assert result["accuracy"] == pytest.approx(2 / 3)
        by_slice = result["per_slice"]
        assert by_slice["Standard hearsay"] == {"correct": 1, "n": 1}
        assert by_slice["Non-assertive conduct"] == {"correct": 0, "n": 1}
        assert by_slice["Not introduced to prove truth"] == {"correct": 1, "n": 1}

    def test_refuses_an_empty_item_list(self) -> None:
        with pytest.raises(lbh.NoItemsError):
            lbh.score_unaided({}, [])

    def test_an_empty_task_file_still_refuses_to_score(self, tmp_path: Path) -> None:
        # Header only, and no `slice` column -- the fallback must not manufacture an item to score.
        task_dir = tmp_path / "diversity_2"
        task_dir.mkdir()
        p = task_dir / "test.tsv"
        p.write_text("index\ttext\tanswer\tparties_are_diverse\taic_is_met\n")
        items = lbh.load_task(p)
        assert items == []
        with pytest.raises(lbh.NoItemsError):
            lbh.score_unaided({}, items)

    def test_a_task_named_slice_is_reported_as_a_fallback_not_as_a_real_slice(
        self, no_slice_tsv: Path
    ) -> None:
        result = lbh.score_unaided({0: "No", 1: "Yes"}, lbh.load_task(no_slice_tsv))
        assert result["accuracy"] == 1.0
        assert result["per_slice"] == {"diversity_1": {"correct": 2, "n": 2}}
        assert result["slice_is_task_fallback"] is True

    def test_a_real_slice_breakdown_is_not_flagged_as_a_fallback(self, fixture_tsv: Path) -> None:
        result = lbh.score_unaided({0: "Yes", 1: "No", 2: "No"}, lbh.load_task(fixture_tsv))
        assert result["slice_is_task_fallback"] is False
