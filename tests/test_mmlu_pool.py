"""The product's first internal choice pool: a law slice of MMLU sealed as option-letter items (ADR-0035).

Every pool this engine had was numeric, and `metrics` had exactly one scorer, so `prabhasa-nyaya` — the
operator's own product objective — could not run a night however much GPU was free. These tests pin the two
properties that make the new pool usable rather than merely present: the manifest declares the scorer that
must read it, and the items are drawn from splits the external proof tier does not measure, so the loop
cannot select on the items its own proof is scored against.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from pravrudhi.application.pool_admin import MMLU_INTERNAL_SPLITS, MMLU_LAW_SUBJECTS, seal_mmlu
from pravrudhi_kernel.metrics import scorer_for_pool
from pravrudhi_kernel.metrics.mmlu import gold_answer
from pravrudhi_kernel.metrics.pool import read_item


def _rows(n: int, first_answer: int = 2) -> dict[str, list[Any]]:
    return {
        "question": [f"Question {i}: which rule applies?" for i in range(n)],
        "subject": ["professional_law"] * n,
        "choices": [[f"opt{i}a", f"opt{i}b", f"opt{i}c", f"opt{i}d"] for i in range(n)],
        "answer": [(first_answer + i) % 4 for i in range(n)],
    }


@pytest.fixture
def cache(tmp_path: Path) -> Path:
    """A cache laid out the way the datasets hub lays out `cais/mmlu`."""
    snap = tmp_path / "hub" / "datasets--cais--mmlu" / "snapshots" / "deadbeef"
    counts = {"test": 40, "validation": 12, "dev": 4}
    for subject in MMLU_LAW_SUBJECTS:
        d = snap / subject
        d.mkdir(parents=True)
        for split, n in counts.items():
            pq.write_table(pa.table(_rows(n)), d / f"{split}-00000-of-00001.parquet")
    return tmp_path


def test_sealed_pool_declares_the_choice_scorer(tmp_path: Path, cache: Path) -> None:
    m = seal_mmlu(tmp_path, cache)
    pool = tmp_path / ".pravrudhi" / "kernel" / "pools" / m["bench"]
    assert m["answer_kind"] == "choice"
    assert scorer_for_pool(pool).__name__.endswith("mmlu")


def test_items_carry_the_options_in_the_question_and_a_letter_as_the_answer(tmp_path: Path, cache: Path) -> None:
    m = seal_mmlu(tmp_path, cache)
    pool = tmp_path / ".pravrudhi" / "kernel" / "pools" / m["bench"]
    item = read_item(pool, sorted(m["item_hashes"])[0])
    assert "A. " in item["question"] and "D. " in item["question"]
    assert gold_answer(item["answer"]) == item["answer"]
    # Every gold must be readable by the scorer the manifest names, or the pool is unscoreable at n items.
    for item_id in m["item_hashes"]:
        gold_answer(read_item(pool, item_id)["answer"])


def test_only_the_splits_the_external_tier_does_not_measure_are_sealed(tmp_path: Path, cache: Path) -> None:
    # The external proof runs lm-eval over the TEST splits. If the loop's selection pool contained those
    # items, an improvement on the external number would partly be selection showing up in its own proof.
    m = seal_mmlu(tmp_path, cache)
    assert m["n_items"] == 2 * (12 + 4)
    assert m["source"]["splits"] == list(MMLU_INTERNAL_SPLITS)
    assert "test" not in m["source"]["splits"]
    assert m["source"]["held_out_for_external_proof"]


def test_source_records_a_hash_per_file_so_the_pool_is_traceable(tmp_path: Path, cache: Path) -> None:
    m = seal_mmlu(tmp_path, cache)
    files = m["source"]["files"]
    assert len(files) == len(MMLU_LAW_SUBJECTS) * len(MMLU_INTERNAL_SPLITS)
    assert all(len(f["sha256"]) == 64 for f in files)


def test_sealing_is_deterministic(tmp_path: Path, cache: Path) -> None:
    a = seal_mmlu(tmp_path / "a", cache)
    b = seal_mmlu(tmp_path / "b", cache)
    assert a["pool_version"] == b["pool_version"]


def test_a_missing_cache_says_what_to_fetch(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="cais/mmlu"):
        seal_mmlu(tmp_path, tmp_path / "empty")
