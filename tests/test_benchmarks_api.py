"""GET /api/benchmarks: every task the external tier has ever scored, so choosing what an objective's success
means is picking from what this engine can actually measure rather than typing an external scorer's own task
syntax from memory. Before this route existed, the only way to discover a valid benchmark name was to read
lm-eval's task list or an existing objective file."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from pravrudhi.api.server import create_app
from pravrudhi_kernel.ledger import LedgerWriter

H = {"host": "127.0.0.1:8008"}
ENTRY_FIELDS = {"id", "tool", "metric", "track", "value", "n"}


def _client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(tmp_path), headers=H)


def _write_row(root: Path, night: int, payload: dict) -> None:
    ledger = root / "research" / "ledger.jsonl"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    w = LedgerWriter.open(ledger, "0.1.0")
    w.append("audit", "auditor", payload, epoch=0, night=night)


def test_benchmarks_answers_on_a_workspace_that_has_never_run_a_night(tmp_path: Path) -> None:
    """No `research/ledger.jsonl` exists at all here -- this is what a just-installed engine looks like."""
    c = _client(tmp_path)
    r = c.get("/api/benchmarks")
    assert r.status_code == 200
    assert r.json() == {"benchmarks": []}


def test_benchmarks_lists_every_scored_task_with_its_most_recent_value(tmp_path: Path) -> None:
    lm_eval_base = {
        "kind": "external_eval", "severity": "info", "tier": "external", "track": "M", "condition": "base",
        "model": "base-model", "tool": "lm-eval", "tool_version": "1.0",
        "metrics": {"gsm8k": {"exact_match,strict-match": 0.30}},
        "n_samples": {"gsm8k": 100}, "sha256": "a" * 64,
    }
    lm_eval_candidate = {
        **lm_eval_base, "condition": "candidate-1", "model": "candidate-model",
        "metrics": {"gsm8k": {"exact_match,strict-match": 0.42}},
    }
    evalplus_row = {
        "kind": "external_eval", "severity": "info", "tier": "external", "track": "H", "condition": "base",
        "model": "base-model", "tool": "evalplus", "tool_version": "0.3.1", "dataset": "humaneval",
        "n_samples": {"humaneval": 50},
        "metrics": {
            "humaneval": {"pass@1_base": 0.5, "pass@1_plus": 0.44},
            "humaneval_counts": {"n": 50, "base_pass": 25, "plus_pass": 22},
        },
        "sha256": "b" * 64,
    }
    _write_row(tmp_path, 1, lm_eval_base)
    _write_row(tmp_path, 2, lm_eval_candidate)
    _write_row(tmp_path, 1, evalplus_row)

    c = _client(tmp_path)
    body = c.get("/api/benchmarks").json()
    rows = {row["metric"]: row for row in body["benchmarks"]}

    assert set(rows) == {"gsm8k exact_match,strict-match", "humaneval+ pass@1"}
    for row in body["benchmarks"]:
        assert set(row) == ENTRY_FIELDS

    gsm8k = rows["gsm8k exact_match,strict-match"]
    assert gsm8k["id"] == "gsm8k"
    assert gsm8k["tool"] == "lm-eval"
    assert gsm8k["track"] == "M"
    assert gsm8k["value"] == 0.42, "the later-seq row is the one that counts, not the baseline"
    assert gsm8k["n"] == 100

    humaneval = rows["humaneval+ pass@1"]
    assert humaneval["id"] == "humaneval+"
    assert humaneval["tool"] == "evalplus"
    assert humaneval["track"] == "H"
    assert humaneval["value"] == 0.44
    assert humaneval["n"] == 50


def test_benchmarks_skips_a_row_with_no_usable_metrics_instead_of_failing_the_whole_list(tmp_path: Path) -> None:
    empty_row = {
        "kind": "external_eval", "severity": "info", "tier": "external", "track": "M", "condition": "base",
        "model": "base-model", "tool": "lm-eval", "tool_version": "1.0",
        "metrics": {"gsm8k": {}}, "n_samples": {"gsm8k": 0}, "sha256": "c" * 64,
    }
    _write_row(tmp_path, 1, empty_row)

    c = _client(tmp_path)
    r = c.get("/api/benchmarks")
    assert r.status_code == 200
    assert r.json() == {"benchmarks": []}
