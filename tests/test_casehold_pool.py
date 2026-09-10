"""The law tracks' second internal choice pool (ADR-0041).

`mmlu-law-val` is spent -- 191 items, 115 at exposure cap 16, 76 eligible against a k=96 draw -- so the nyaya
harness night died in `draw_rotation` before evaluating anything, exactly as MBPP+ did before ADR-0029. These
tests pin the properties that make the replacement a replacement rather than a second thing to get wrong.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from pravrudhi.application.harness_track import kernel_scored
from pravrudhi.application.pool_admin import (
    CASEHOLD_EXTERNAL_SPLITS,
    CASEHOLD_INTERNAL_SPLIT,
    seal_casehold,
)
from pravrudhi_kernel.metrics import scorer_for_pool
from pravrudhi_kernel.metrics.pool import answer_kind, load_manifest

#: CaseHOLD's CSV is read POSITIONALLY -- `corpus._ID, _PROMPT, _FIRST_HOLDING, _LABEL = 0, 1, 2, 12` -- and
#: its header is the literal column numbers. A fixture with the wrong width does not fail loudly: every row
#: is shorter than `_LABEL` and is skipped, so the pool seals empty. Getting this wrong is how this test
#: first failed, which is worth a comment rather than a silently corrected number.
WIDTH = 13
HEADER = ["Unnamed: 0", *[str(i) for i in range(WIDTH - 1)]]


def _csv(path: Path, n: int = 6) -> Path:
    with path.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(HEADER)
        for i in range(n):
            row = [""] * WIDTH
            row[0] = str(i)
            row[1] = f"passage {i} cites (<HOLDING>)"
            for j in range(5):
                row[2 + j] = f"holding {i}.{j}"
            row[12] = str(i % 5)
            w.writerow(row)
    return path


def test_the_internal_split_is_validation_and_test_is_reserved() -> None:
    """The model track does rejection sampling on `casehold-train`, so selecting on those rows would have the
    loop score itself on what it learned from; `test` is what the external proof tier scores."""
    assert CASEHOLD_INTERNAL_SPLIT == "val"
    assert "test" in CASEHOLD_EXTERNAL_SPLITS
    assert "train" not in CASEHOLD_EXTERNAL_SPLITS  # train is excluded by being a different split, not by this


def test_sealing_declares_choice_and_records_its_disjointness(tmp_path: Path) -> None:
    src = _csv(tmp_path / "casehold-val.csv")
    m = seal_casehold(tmp_path, src, "casehold-test-pool")
    assert m["answer_kind"] == "choice"
    assert m["n_items"] == 6
    pool = Path(tmp_path) / ".pravrudhi" / "kernel" / "pools" / "casehold-test-pool"
    manifest = load_manifest(pool)
    disjoint = " ".join(manifest["source"]["disjoint_from"]).lower()
    assert "casehold-train" in disjoint, "the manifest must say what it is disjoint FROM, or nothing can check"
    assert "external proof" in disjoint
    assert manifest["source"]["held_out_for_external_proof"] == ["test"]


def test_the_pool_is_scored_in_process_by_the_kernels_choice_scorer(tmp_path: Path) -> None:
    """No `score_choice.py` beside `score_code.py`. Scoring a code pool means EXECUTING a candidate and needs
    a sandbox; scoring a letter is a comparison the kernel already does, and ADR-0035 put it in T0 so the
    engine would not compute the numbers."""
    src = _csv(tmp_path / "casehold-val.csv")
    seal_casehold(tmp_path, src, "casehold-test-pool")
    pool = Path(tmp_path) / ".pravrudhi" / "kernel" / "pools" / "casehold-test-pool"
    assert answer_kind(pool) == "choice"
    assert kernel_scored(pool) is True
    assert scorer_for_pool(pool).gold_answer("C") == "C"


def test_an_empty_source_is_refused_rather_than_sealed(tmp_path: Path) -> None:
    """A pool of nothing refuses every draw, and would do it at `draw_rotation` on the night rather than here."""
    src = _csv(tmp_path / "casehold-val.csv", n=0)
    with pytest.raises(ValueError, match="no rows"):
        seal_casehold(tmp_path, src, "casehold-empty")


def test_the_live_pool_has_the_headroom_the_spent_one_lacked() -> None:
    """The forcing reason for ADR-0041, asserted against the sealed pool rather than the plan."""
    pool = Path(".pravrudhi/kernel/pools/casehold-val")
    if not pool.exists():
        pytest.skip("casehold-val is not sealed in this workspace")
    n = len(json.loads((pool / "manifest.json").read_text())["item_hashes"])
    assert n > 5000
    # k=96 at exposure cap 16: rotations available before the pool is spent.
    assert (n * 16) // 96 > 500, "a pool that ends nights on exposure budget is the thing being replaced"


def test_outlier_length_questions_are_excluded_and_counted(tmp_path: Path) -> None:
    """Found by running the floor, not by a test: every run of rotation 0 died with CUDA OOM while rotations
    1 and 2 passed. A consistent per-rotation failure is content, not contention. `agent_choice.py` batches 16
    prompts with padding=True, so one 115,303-character question -- ~29k tokens, against a p99 of 2,596 --
    pads the whole batch to its own length and the job dies. An item the model cannot be ASKED is not an
    evaluation item; it scores 0 for a context reason and reads as a legal-reasoning failure."""
    from pravrudhi.application.pool_admin import CASEHOLD_MAX_QUESTION_CHARS

    src = tmp_path / "casehold-val.csv"
    with src.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(HEADER)
        # 1 in 40 is 2.5%, inside the 5% the sealer allows. 1 in 10 would be 10% and is refused -- correctly,
        # and by this file's own next test. The real split drops 23 of 5,314, which is 0.43%.
        for i in range(40):
            row = [""] * WIDTH
            row[0] = str(i)
            row[1] = ("x" * 60000 if i == 3 else f"passage {i}") + " cites (<HOLDING>)"
            for j in range(5):
                row[2 + j] = f"holding {i}.{j}"
            row[12] = str(i % 5)
            w.writerow(row)

    m = seal_casehold(tmp_path, src, "casehold-bounded")
    assert m["n_items"] == 39, "the outlier must be gone"
    manifest = load_manifest(Path(tmp_path) / ".pravrudhi" / "kernel" / "pools" / "casehold-bounded")
    s = manifest["source"]
    assert s["n_rows_in_split"] == 40
    assert s["n_dropped_too_long"] == 1, "the exclusion has to be counted, or it becomes folklore"
    assert s["max_question_chars"] == CASEHOLD_MAX_QUESTION_CHARS
    longest = max(
        len(json.loads(p.read_text())["question"])
        for p in (Path(tmp_path) / ".pravrudhi" / "kernel" / "pools" / "casehold-bounded" / "items").iterdir()
    )
    assert longest <= CASEHOLD_MAX_QUESTION_CHARS


def test_a_bound_that_cuts_into_the_distribution_is_refused(tmp_path: Path) -> None:
    """Clipping outliers and trimming the hard end are different decisions, and only the first is this one.

    The lengths have to VARY for this to be meaningful: a uniform fixture either keeps everything or nothing,
    and nothing trips the empty-pool check first, which is a different refusal."""
    src = tmp_path / "casehold-val.csv"
    with src.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(HEADER)
        for i in range(20):
            row = [""] * WIDTH
            row[0] = str(i)
            row[1] = "y" * (100 * (i + 1)) + " cites (<HOLDING>)"   # 100 .. 2000 chars, evenly spread
            for j in range(5):
                row[2 + j] = f"holding {i}.{j}"
            row[12] = str(i % 5)
            w.writerow(row)
    # A bound at 1000 chars keeps ~9 of 20 and drops the rest: a slice, not a clip.
    with pytest.raises(ValueError, match="slice of the distribution"):
        seal_casehold(tmp_path, src, "casehold-overcut", max_chars=1000)


def test_the_live_pool_carries_no_item_the_model_cannot_be_asked() -> None:
    pool = Path(".pravrudhi/kernel/pools/casehold-val")
    if not pool.exists():
        pytest.skip("casehold-val is not sealed in this workspace")
    from pravrudhi.application.pool_admin import CASEHOLD_MAX_QUESTION_CHARS

    lens = [len(json.loads(p.read_text())["question"]) for p in (pool / "items").iterdir()]
    assert max(lens) <= CASEHOLD_MAX_QUESTION_CHARS
    src = load_manifest(pool)["source"]
    assert src["n_dropped_too_long"] == 23, "the 23 outliers in CaseHOLD val, recorded rather than assumed"
