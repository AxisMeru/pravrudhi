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
