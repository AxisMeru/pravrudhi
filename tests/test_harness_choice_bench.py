"""The harness track on a choice bench.

`harness_track.SCORERS` maps a pool's bench to a CONTAINER job that executes hidden tests. That is right for
code, where scoring means running the candidate's solution, and meaningless for a multiple-choice pool whose
answer is a letter: there is nothing to execute, and `score_agent` crashed before it got that far, on
`json.loads("B")` while reading a task id out of the pool's answer.

The fix is not a new in-container scorer. ADR-0035 put a choice scorer in the kernel precisely so the engine
would not compute the numbers; adding `score_choice.py` beside `score_code.py` would put it back. A bench with
no container scorer is scored in process by the scorer its own manifest declares, exactly as the model track's
`spine.score_job` does.

The dispatch rule has to survive a code pool that nobody remembered to register. `mbppplus` and `apps` were
sealed before `answer_kind` existed, so their manifests declare none and default to numeric -- routing on
`answer_kind` alone would hand a JSON answer blob to the numeric scorer and score every item zero, which looks
exactly like a harness that got worse. So: a bench in SCORERS keeps its container job; otherwise the pool MUST
declare an answer kind, and a pool that does neither is refused rather than guessed at.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pravrudhi.application.harness_track import SCORERS, _scorer, kernel_scored
from pravrudhi_kernel.metrics import seal_pool

CHOICE = [{"question": f"q{i}\nA. no\nB. yes", "answer": "B"} for i in range(6)]
NUMERIC = [{"question": f"q{i}", "answer": f"steps\n#### {i}"} for i in range(6)]


def test_a_code_bench_keeps_its_container_scorer(tmp_path: Path) -> None:
    pool = tmp_path / "mbppplus"
    seal_pool(pool, "mbppplus", NUMERIC, {})
    assert kernel_scored(pool) is False
    job, _ = _scorer(pool)
    assert job.name == "score_code.py"


def test_a_choice_bench_is_scored_in_process_by_the_kernel(tmp_path: Path) -> None:
    pool = tmp_path / "law"
    seal_pool(pool, "mmlu-law-val", CHOICE, {}, answer_kind="choice")
    assert "mmlu-law-val" not in SCORERS
    assert kernel_scored(pool) is True


def test_a_pool_that_is_neither_registered_nor_declared_is_refused(tmp_path: Path) -> None:
    # The dangerous case: a future code pool left out of SCORERS. Its manifest declares no kind, so routing on
    # answer_kind would hand a JSON answer to the numeric scorer and record a harness that got worse.
    pool = tmp_path / "mystery"
    seal_pool(pool, "mystery-bench", NUMERIC, {})
    manifest = json.loads((pool / "manifest.json").read_text())
    del manifest["answer_kind"]
    (pool / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    with pytest.raises(KeyError, match="mystery-bench"):
        kernel_scored(pool)


def test_the_choice_scorer_reads_the_pool_the_harness_track_would_use(tmp_path: Path) -> None:
    from pravrudhi_kernel.metrics import scorer_for_pool, scorer_source_for_pool
    from pravrudhi_kernel.metrics.pool import read_item

    pool = tmp_path / "law"
    seal_pool(pool, "mmlu-law-val", CHOICE, {}, answer_kind="choice")
    scorer = scorer_for_pool(pool)
    item_id = sorted(json.loads((pool / "manifest.json").read_text())["item_hashes"])[0]
    gold = scorer.gold_answer(read_item(pool, item_id)["answer"])
    assert scorer.score_completions({item_id: "Answer: B"}, {item_id: gold}) == {item_id: 1}
    # And the observation must name the file that scored it, not a container job that never ran.
    assert scorer_source_for_pool(pool).name == "mmlu.py"
