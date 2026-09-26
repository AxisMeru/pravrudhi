"""`scripts/t2_c3_ab_run.py`'s sealing logic: a latency-circuit-breaker abort, or a per-row error, must
never seal a TRUNCATED run under the canonical output filename with exit 0. All model calls are mocked --
no real vLLM server, no network.
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

import t2_c3_ab_run as mod  # type: ignore[import-not-found]  # noqa: E402


class _FakeResult:
    def __init__(self, text: str, top_logprobs: dict[str, float]) -> None:
        self.text = text
        self.top_logprobs = top_logprobs


class _FakeRunMetadata:
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
    def __init__(self, *, fail_at_call: int | None = None, latency_fail_at_call: int | None = None, **_kw: Any) -> None:
        self.model = "fake-house-model"
        self._n = 0
        self._fail_at_call = fail_at_call
        self._latency_fail_at_call = latency_fail_at_call

    def _complete(self, prompt: str) -> _FakeResult:
        idx = self._n
        self._n += 1
        if self._latency_fail_at_call is not None and idx == self._latency_fail_at_call:
            raise mod.LatencyDegraded(f"call took 99s, > 4.0x rolling median 1s (call {idx})")
        if self._fail_at_call is not None and idx == self._fail_at_call:
            raise ValueError(f"free-arm transport error on call {idx}")
        return _FakeResult("established F1:0:5", {" established": -0.1, " not": -3.0})


class _FakeDecoder:
    def __init__(self, **_kw: Any) -> None:
        self.model = "fake-typed-model"

    def complete(self, prompt: str, *, max_tokens: int, temperature: float, logprobs: int | None) -> _FakeResult:
        return _FakeResult("established F1:0:5", {"true": -0.1, "false": -3.0})


def _write_prompts(tmp_path: Path, n_calib: int, n_heldout: int) -> Path:
    rows = []
    for i in range(n_calib):
        rows.append({"split": "calib_v1", "id": f"calib-{i}", "gold": "established", "prompt": f"prompt calib {i}"})
    for i in range(n_heldout):
        rows.append(
            {"split": "heldout_v1", "id": f"heldout-{i}", "gold": "established", "prompt": f"prompt heldout {i}"}
        )
    prompts_path = tmp_path / "prompts.jsonl"
    prompts_path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return prompts_path


@pytest.fixture(autouse=True)
def _patch_common(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mod, "RunMetadata", _FakeRunMetadata)


def _configure_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, n_calib: int, n_heldout: int) -> Path:
    prompts_path = _write_prompts(tmp_path, n_calib, n_heldout)
    monkeypatch.setattr(mod, "EXPECTED_SHA", hashlib.sha256(prompts_path.read_bytes()).hexdigest())
    results_dir = tmp_path / "results"
    monkeypatch.setenv("PRAVRUDHI_T1_PARITY_PROMPTS", str(prompts_path))
    monkeypatch.setenv("PRAVRUDHI_T2_RESULTS_DIR", str(results_dir))
    return results_dir


def test_a_complete_run_seals_the_canonical_file_and_exits_zero(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    results_dir = _configure_env(monkeypatch, tmp_path, n_calib=4, n_heldout=2)
    monkeypatch.setattr(mod, "HouseJudge", lambda **kw: _FakeHouse())
    monkeypatch.setattr(mod, "VLLMDecoder", lambda **kw: _FakeDecoder())

    rc = mod.main()

    assert rc == 0
    out_path = results_dir / "t2_c3_raw_outputs.jsonl"
    assert out_path.exists()
    assert not (results_dir / "t2_c3_raw_outputs.jsonl.TRUNCATED").exists()
    rows = [json.loads(line) for line in out_path.read_text().splitlines() if line.strip()]
    assert len(rows) == 6

    meta = json.loads((results_dir / "t2_c3_ab_run_RUN-METADATA.json").read_text())
    assert meta["n_planned"] == 6
    assert meta["n_scored"] == 6
    assert meta["n_error"] == 0
    assert meta["complete"] is True
    assert meta["raw_output_sha256"] == hashlib.sha256(out_path.read_bytes()).hexdigest()


def test_a_latency_circuit_breaker_trip_seals_nothing_under_the_canonical_name(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    results_dir = _configure_env(monkeypatch, tmp_path, n_calib=5, n_heldout=5)
    # Each calib_v1/heldout_v1 row makes 2 calls (free + typed); trip on the 5th call (row index 2, second call).
    monkeypatch.setattr(mod, "HouseJudge", lambda **kw: _FakeHouse(latency_fail_at_call=2))
    monkeypatch.setattr(mod, "VLLMDecoder", lambda **kw: _FakeDecoder())

    rc = mod.main()

    assert rc != 0
    canonical = results_dir / "t2_c3_raw_outputs.jsonl"
    truncated = results_dir / "t2_c3_raw_outputs.jsonl.TRUNCATED"
    assert not canonical.exists()
    assert truncated.exists()
    rows = [json.loads(line) for line in truncated.read_text().splitlines() if line.strip()]
    assert len(rows) < 10

    meta = json.loads((results_dir / "t2_c3_ab_run_RUN-METADATA.json").read_text())
    assert meta["n_planned"] == 10
    assert meta["n_scored"] == len(rows)
    assert meta["complete"] is False
    assert meta["truncated_reason"]
    assert meta["raw_output_sha256"] == hashlib.sha256(truncated.read_bytes()).hexdigest()
    assert meta["output_path"] == str(truncated)


def test_a_per_row_error_is_counted_and_still_truncates_the_seal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    results_dir = _configure_env(monkeypatch, tmp_path, n_calib=3, n_heldout=3)
    monkeypatch.setattr(mod, "HouseJudge", lambda **kw: _FakeHouse(fail_at_call=1))
    monkeypatch.setattr(mod, "VLLMDecoder", lambda **kw: _FakeDecoder())

    rc = mod.main()

    assert rc != 0
    truncated = results_dir / "t2_c3_raw_outputs.jsonl.TRUNCATED"
    assert truncated.exists()
    rows = [json.loads(line) for line in truncated.read_text().splitlines() if line.strip()]
    assert len(rows) == 5

    meta = json.loads((results_dir / "t2_c3_ab_run_RUN-METADATA.json").read_text())
    assert meta["n_planned"] == 6
    assert meta["n_scored"] == 5
    assert meta["n_error"] == 1
    assert meta["complete"] is False


def test_refuses_without_required_env_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PRAVRUDHI_T1_PARITY_PROMPTS", raising=False)
    monkeypatch.delenv("PRAVRUDHI_T2_RESULTS_DIR", raising=False)
    assert mod.main() == 2
