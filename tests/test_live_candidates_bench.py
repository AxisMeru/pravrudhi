"""A candidate is live on the bench it was proposed for, not on every bench (2026-09-10).

`live_candidates` filtered by `target_model` -- "a candidate proposed for another trainee is not live here
(its adapter cannot load)" -- and said nothing about the bench, because until this engine ran more than one
bench per track there was nothing to say. Harness night 20 then paired candidates carried over from
code-bench nights against a law pool.

Checked against the ledger before changing anything: of 170 observed candidates exactly two had observations
on more than one bench -- `c-0000`, the baseline, which is re-measured on every bench by design and is
excluded by the guard above, and `c-0045` across two GSM8K train slices after ADR-0033 moved the pool. So this
closes a LATENT gap. What it prevents is a sequential boundary accumulating n across benches whose pass rates
are not the same quantity, which is the same error as pairing a config with another pool's noise floor.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from pravrudhi.application.deliberate import live_candidates


def _state(*cids: str) -> dict[str, Any]:
    return {
        cid: SimpleNamespace(
            pruned=False, promoted=False, audit_high=False, skipped=False, last_boundary="continue"
        )
        for cid in cids
    }


def _meta(**buckets: dict[str, str]) -> dict[str, dict[str, Any]]:
    return {cid: {"bucket": bucket, "surface": "H3.prompt"} for cid, bucket in buckets.items()}


LAW = {"target_model": "Qwen/Qwen3-1.7B", "task_family": "mmlu-law-val"}
CODE = {"target_model": "Qwen/Qwen3-1.7B", "task_family": "mbppplus"}


def test_a_candidate_from_another_bench_is_not_live() -> None:
    pool = live_candidates(
        _state("c-0201", "c-0107"),
        _meta(**{"c-0201": LAW, "c-0107": CODE}),
        "c-0000",
        target_model="Qwen/Qwen3-1.7B",
        bench="mmlu-law-val",
    )
    assert pool == ["c-0201"]


def test_a_candidate_with_no_bucket_bench_stays_live() -> None:
    # `None` means the record does not say, which is not the same as saying another bench. Excluding those
    # would silently drop every candidate written before the bucket carried a task family.
    pool = live_candidates(
        _state("c-0300"),
        _meta(**{"c-0300": {"target_model": "Qwen/Qwen3-1.7B"}}),
        "c-0000",
        target_model="Qwen/Qwen3-1.7B",
        bench="mmlu-law-val",
    )
    assert pool == ["c-0300"]


def test_no_bench_given_is_the_old_behaviour_exactly() -> None:
    # Every existing caller that does not name a bench must keep the pool it had.
    pool = live_candidates(
        _state("c-0201", "c-0107"),
        _meta(**{"c-0201": LAW, "c-0107": CODE}),
        "c-0000",
        target_model="Qwen/Qwen3-1.7B",
    )
    assert sorted(pool) == ["c-0107", "c-0201"]


def test_the_model_filter_still_applies() -> None:
    other = {"target_model": "Qwen/Qwen3-0.6B", "task_family": "mmlu-law-val"}
    pool = live_candidates(
        _state("c-0201", "c-0400"),
        _meta(**{"c-0201": LAW, "c-0400": other}),
        "c-0000",
        target_model="Qwen/Qwen3-1.7B",
        bench="mmlu-law-val",
    )
    assert pool == ["c-0201"]
