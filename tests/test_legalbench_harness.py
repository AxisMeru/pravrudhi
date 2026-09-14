"""Track A P3 (`research/prereg/B1-tier1-amendment-2.md`): closed-book unaided-accuracy scoring for
LegalBench's rule-application tasks (`hearsay`, `personal_jurisdiction`). Binary Yes/No classification, not
the conduct-with-missing-element shape `nyaya_lean_elements.py` checks -- see the amendment for why this
module deliberately makes no Lean-checked claim yet, only a labelled "model-read" one.

Done-when: loads a task's `.tsv` (index/answer/text/slice); scores a vendor's answers against gold as
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


@pytest.fixture
def fixture_tsv(tmp_path: Path) -> Path:
    p = tmp_path / "hearsay.test.tsv"
    p.write_text(_FIXTURE_TSV)
    return p


class TestLoadTask:
    def test_reads_every_row_with_its_slice(self, fixture_tsv: Path) -> None:
        items = lbh.load_task(fixture_tsv)
        assert len(items) == 3
        assert items[0].index == 0
        assert items[0].answer == "Yes"
        assert items[0].slice == "Standard hearsay"
        assert items[1].answer == "No"


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
