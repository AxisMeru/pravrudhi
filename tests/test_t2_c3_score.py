"""#91: `t2_c3_score` derives each split's `n` from the planned (pre-scoring) row count and raises when a row
does not score, never reporting the smaller surviving count. Synthetic rows only; no model calls."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

SCRIPTS_DIR = str(Path(__file__).resolve().parent.parent / "scripts")
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

import t2_c3_score as mod  # type: ignore[import-not-found]  # noqa: E402


def _row(i: int) -> dict[str, Any]:
    arm = {"text": " not", "top_logprobs": [{" not": -0.05, " established": -4.0}]}
    return {"id": f"r{i}", "gold": "not_established", "half": "a", "arm_run_first": "free_text",
            "free_text": arm, "typed": arm}


def test_n_is_the_planned_count_and_all_rows_scored() -> None:
    rows = [_row(i) for i in range(5)]
    n, scored = mod.score_split("calib_v1", rows)
    assert n == 5 and len(scored) == 5


def test_a_row_that_does_not_score_raises_instead_of_shrinking_n(monkeypatch: pytest.MonkeyPatch) -> None:
    real = mod.score_row
    monkeypatch.setattr(mod, "score_row", lambda r: None if r["id"] == "r2" else real(r))
    with pytest.raises(ValueError, match=r"calib_v1: planned 5 rows but only 4 scored"):
        mod.score_split("calib_v1", [_row(i) for i in range(5)])


def test_a_row_that_raises_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(r: dict[str, Any]) -> dict[str, Any]:
        raise KeyError("free_text")

    monkeypatch.setattr(mod, "score_row", boom)
    with pytest.raises(KeyError):
        mod.score_split("calib_v1", [_row(0)])
