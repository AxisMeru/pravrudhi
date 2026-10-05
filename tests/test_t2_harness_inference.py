"""`scripts/t2_harness_inference.py`'s sealing logic: a latency-circuit-breaker abort, or a per-item error,
must never seal a TRUNCATED run under the canonical output filename with exit 0. All model calls are mocked
-- no real vLLM server, no network.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import pytest

SCRIPTS_DIR = str(Path(__file__).resolve().parent.parent / "scripts")
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

import t2_harness_inference as mod  # type: ignore[import-not-found]  # noqa: E402


class _FakeResult:
    def __init__(self, text: str, top_logprobs: dict[str, float]) -> None:
        self.text = text
        self.top_logprobs = top_logprobs


class _FakeRunMetadata:
    """Records `finish(extra=...)`'s fields without touching subprocess/network at all."""

    def __init__(self, **_kw: Any) -> None:
        self.data: dict[str, Any] = {}

    def start(self) -> None:
        pass

    def finish(self, *, extra: dict[str, Any] | None = None) -> None:
        if extra:
            self.data.update(extra)

    def write(self, out_path: Path) -> None:
        out_path.write_text(json.dumps(self.data, default=str))


class _FakeHouse:
    def __init__(self, *, fail_at: int | None = None, latency_fail_at: int | None = None, **_kw: Any) -> None:
        self.model = "fake-house-model"
        self._n = 0
        self._fail_at = fail_at
        self._latency_fail_at = latency_fail_at

    def _complete(self, prompt: str) -> _FakeResult:
        idx = self._n
        self._n += 1
        if self._latency_fail_at is not None and idx == self._latency_fail_at:
            raise mod.LatencyDegraded(f"call took 99s, > 4.0x rolling median 1s (item {idx})")
        if self._fail_at is not None and idx == self._fail_at:
            raise ValueError(f"free-arm transport error on item {idx}")
        return _FakeResult("established F1:0:5", {" established": -0.1, " not": -3.0})


class _FakeDecoder:
    def __init__(self, **_kw: Any) -> None:
        self.model = "fake-typed-model"

    def complete(self, prompt: str, *, max_tokens: int, temperature: float, logprobs: int | None) -> _FakeResult:
        return _FakeResult("established F1:0:5", {"true": -0.1, "false": -3.0})


def _write_items_and_scores(tmp_path: Path, n: int) -> tuple[Path, Path]:
    items = []
    scores = []
    for i in range(n):
        item_id = f"item-{i}"
        items.append(
            {
                "item_id": item_id,
                "element_id": f"el-{i}",
                "contract_id": f"contract-{i}",
                "partition": "test",
                "gold_status": "established",
                "element_desc": "some element",
                "statute": "some statute text",
                "narrative": "some narrative",
                "facts": [{"id": "F1", "text": "a fact"}],
            }
        )
        scores.append({"source_row_id": item_id, "p_established": 0.9})

    items_path = tmp_path / "eval_items.jsonl"
    items_path.write_text("\n".join(json.dumps(r) for r in items) + "\n")
    scores_path = tmp_path / "eval_logit_scores.jsonl"
    scores_path.write_text("\n".join(json.dumps(r) for r in scores) + "\n")
    return items_path, scores_path


@pytest.fixture(autouse=True)
def _patch_common(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mod, "RunMetadata", _FakeRunMetadata)
    monkeypatch.setattr(mod.time, "sleep", lambda *_a, **_kw: None)


def _configure_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, n_items: int) -> Path:
    items_path, scores_path = _write_items_and_scores(tmp_path, n_items)
    monkeypatch.setattr(mod, "EVAL_ITEMS_SHA", hashlib.sha256(items_path.read_bytes()).hexdigest())
    results_dir = tmp_path / "results"
    monkeypatch.setenv("PRAVRUDHI_T2_EVAL_ITEMS", str(items_path))
    monkeypatch.setenv("PRAVRUDHI_T2_EVAL_LOGIT_SCORES", str(scores_path))
    monkeypatch.setenv("PRAVRUDHI_T2_RESULTS_DIR", str(results_dir))
    return results_dir


def test_a_complete_run_seals_the_canonical_file_and_exits_zero(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    results_dir = _configure_env(monkeypatch, tmp_path, n_items=5)
    monkeypatch.setattr(mod, "HouseJudge", lambda **kw: _FakeHouse())
    monkeypatch.setattr(mod, "VLLMDecoder", lambda **kw: _FakeDecoder())

    rc = mod.main()

    assert rc == 0
    out_path = results_dir / "t2_harness_raw_outputs.jsonl"
    assert out_path.exists()
    assert not (results_dir / "t2_harness_raw_outputs.jsonl.TRUNCATED").exists()
    rows = [json.loads(line) for line in out_path.read_text().splitlines() if line.strip()]
    assert len(rows) == 5

    meta = json.loads((results_dir / "t2_harness_inference_RUN-METADATA.json").read_text())
    assert meta["n_planned"] == 5
    assert meta["n_scored"] == 5
    assert meta["n_error"] == 0
    assert meta["complete"] is True
    # Expected bytes are built here from the known fake outputs, not by re-reading what the script wrote.
    expected = "".join(
        json.dumps(
            {
                "item_id": f"item-{i}", "element_id": f"el-{i}", "contract_id": f"contract-{i}", "partition": "test",
                "gold_status": "established", "frozen_p_established": 0.9,
                "free_text": {"text": "established F1:0:5", "top_logprobs": {" established": -0.1, " not": -3.0}},
                "typed": {"text": "established F1:0:5", "top_logprobs": {"true": -0.1, "false": -3.0}},
            }
        )
        + "\n"
        for i in range(5)
    )
    assert out_path.read_bytes() == expected.encode()
    assert meta["raw_output_sha256"] == hashlib.sha256(expected.encode()).hexdigest()
    # (a) the input artefacts are recorded with their own digests
    assert meta["input_eval_items_path"] == str(tmp_path / "eval_items.jsonl")
    assert meta["input_eval_items_sha256"] == hashlib.sha256((tmp_path / "eval_items.jsonl").read_bytes()).hexdigest()
    assert meta["input_eval_logit_scores_sha256"] == hashlib.sha256(
        (tmp_path / "eval_logit_scores.jsonl").read_bytes()
    ).hexdigest()


def test_a_latency_circuit_breaker_trip_seals_nothing_under_the_canonical_name(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    results_dir = _configure_env(monkeypatch, tmp_path, n_items=10)
    monkeypatch.setattr(mod, "HouseJudge", lambda **kw: _FakeHouse(latency_fail_at=3))
    monkeypatch.setattr(mod, "VLLMDecoder", lambda **kw: _FakeDecoder())

    rc = mod.main()

    assert rc != 0
    canonical = results_dir / "t2_harness_raw_outputs.jsonl"
    truncated = results_dir / "t2_harness_raw_outputs.jsonl.TRUNCATED"
    assert not canonical.exists()
    assert truncated.exists()
    rows = [json.loads(line) for line in truncated.read_text().splitlines() if line.strip()]
    assert len(rows) == 3
    assert len(rows) < 10

    meta = json.loads((results_dir / "t2_harness_inference_RUN-METADATA.TRUNCATED.json").read_text())
    assert not (results_dir / "t2_harness_inference_RUN-METADATA.json").exists()
    assert meta["n_planned"] == 10
    assert meta["n_scored"] == 3
    assert meta["complete"] is False
    assert meta["truncated_reason"]
    assert meta["raw_output_sha256"] == hashlib.sha256(truncated.read_bytes()).hexdigest()
    assert meta["output_path"] == str(truncated)


def test_a_per_item_error_is_counted_and_still_truncates_the_seal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    results_dir = _configure_env(monkeypatch, tmp_path, n_items=6)
    monkeypatch.setattr(mod, "HouseJudge", lambda **kw: _FakeHouse(fail_at=2))
    monkeypatch.setattr(mod, "VLLMDecoder", lambda **kw: _FakeDecoder())

    rc = mod.main()

    assert rc != 0
    truncated = results_dir / "t2_harness_raw_outputs.jsonl.TRUNCATED"
    assert truncated.exists()
    rows = [json.loads(line) for line in truncated.read_text().splitlines() if line.strip()]
    assert len(rows) == 5

    meta = json.loads((results_dir / "t2_harness_inference_RUN-METADATA.TRUNCATED.json").read_text())
    assert meta["n_planned"] == 6
    assert meta["n_scored"] == 5
    assert meta["n_error"] == 1
    assert meta["complete"] is False


def test_refuses_without_required_env_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PRAVRUDHI_T2_EVAL_ITEMS", raising=False)
    monkeypatch.delenv("PRAVRUDHI_T2_EVAL_LOGIT_SCORES", raising=False)
    monkeypatch.delenv("PRAVRUDHI_T2_RESULTS_DIR", raising=False)
    assert mod.main() == 2


def test_an_empty_plan_refuses_instead_of_sealing_an_empty_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # The real pin is left in place: the empty plan must be refused on its own, not because the pin mismatched.
    items_path = tmp_path / "eval_items.jsonl"
    items_path.write_text("")
    scores_path = tmp_path / "eval_logit_scores.jsonl"
    scores_path.write_text("")
    results_dir = tmp_path / "results"
    monkeypatch.setenv("PRAVRUDHI_T2_EVAL_ITEMS", str(items_path))
    monkeypatch.setenv("PRAVRUDHI_T2_EVAL_LOGIT_SCORES", str(scores_path))
    monkeypatch.setenv("PRAVRUDHI_T2_RESULTS_DIR", str(results_dir))

    assert mod.main() == 2
    assert "n_planned == 0" in capsys.readouterr().err
    assert list(results_dir.iterdir()) == []


def test_a_truncated_run_never_overwrites_a_complete_runs_sidecar(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    results_dir = _configure_env(monkeypatch, tmp_path, n_items=4)
    monkeypatch.setattr(mod, "HouseJudge", lambda **kw: _FakeHouse())
    monkeypatch.setattr(mod, "VLLMDecoder", lambda **kw: _FakeDecoder())
    assert mod.main() == 0
    complete_sidecar = results_dir / "t2_harness_inference_RUN-METADATA.json"
    before = complete_sidecar.read_bytes()

    monkeypatch.setattr(mod, "HouseJudge", lambda **kw: _FakeHouse(latency_fail_at=2))
    assert mod.main() != 0

    assert complete_sidecar.read_bytes() == before
    assert json.loads((results_dir / "t2_harness_inference_RUN-METADATA.TRUNCATED.json").read_text())["complete"] is False


def test_row_assembly_failure_is_counted_not_fatal(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    results_dir = _configure_env(monkeypatch, tmp_path, n_items=3)
    items_path = Path(tmp_path / "eval_items.jsonl")
    rows = [json.loads(line) for line in items_path.read_text().splitlines()]
    del rows[1]["partition"]  # a malformed item surfaces at row assembly, after both model calls
    items_path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    monkeypatch.setattr(mod, "EVAL_ITEMS_SHA", hashlib.sha256(items_path.read_bytes()).hexdigest())
    monkeypatch.setattr(mod, "HouseJudge", lambda **kw: _FakeHouse())
    monkeypatch.setattr(mod, "VLLMDecoder", lambda **kw: _FakeDecoder())

    assert mod.main() == 2
    meta = json.loads((results_dir / "t2_harness_inference_RUN-METADATA.TRUNCATED.json").read_text())
    assert meta["n_error"] == 1 and meta["n_scored"] == 2 and meta["complete"] is False
