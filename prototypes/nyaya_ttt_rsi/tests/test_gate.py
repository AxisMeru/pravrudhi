"""Tests for gate.py: RegressionProbe sampling, decide(), and GateLedger.
No real model needed -- nll_fn is a plain injected callable."""

from __future__ import annotations

import json

from prototypes.nyaya_ttt_rsi.gate import (
    SETTLED_LAW_CANARIES,
    GateDecision,
    GateLedger,
    RegressionProbe,
    decide,
)


def _write_jsonl(path, records):
    with open(path, "w") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


def test_regression_probe_from_files_samples_deterministically(tmp_path):
    general_path = tmp_path / "general_val.jsonl"
    records = [{"prompt": f"p{i}", "target": f"t{i}"} for i in range(100)]
    _write_jsonl(general_path, records)

    probe_a = RegressionProbe.from_files(str(general_path), n_general=10, seed=42)
    probe_b = RegressionProbe.from_files(str(general_path), n_general=10, seed=42)
    probe_c = RegressionProbe.from_files(str(general_path), n_general=10, seed=1)

    assert probe_a.items == probe_b.items
    assert probe_a.items != probe_c.items
    # 10 general + the 10 built-in settled-law canaries
    assert len(probe_a.items) == 10 + len(SETTLED_LAW_CANARIES)
    for prompt, target in probe_a.items[:10]:
        assert prompt.startswith("p")
        assert target.startswith("t")


def test_regression_probe_uses_custom_law_canaries_file_when_present(tmp_path):
    general_path = tmp_path / "general_val.jsonl"
    _write_jsonl(general_path, [{"prompt": f"p{i}", "target": f"t{i}"} for i in range(20)])

    law_path = tmp_path / "law_canaries.jsonl"
    _write_jsonl(law_path, [{"prompt": "Custom Q", "target": "Custom A"}])

    probe = RegressionProbe.from_files(str(general_path), str(law_path), n_general=5, seed=0)
    assert len(probe.items) == 5 + 1
    assert probe.items[-1] == ("Custom Q", "Custom A")


def test_probe_nll_uses_injected_nll_fn():
    probe = RegressionProbe(items=[("q1", "a1"), ("q2", "a2"), ("q3", "a3")])
    calls = []

    def fake_nll_fn(model, tok, prompt, continuation):
        calls.append((prompt, continuation))
        return {"q1": 1.0, "q2": 2.0, "q3": 3.0}[prompt]

    result = probe.nll(model=None, tok=None, nll_fn=fake_nll_fn)
    assert result == 2.0
    assert calls == [("q1", "a1"), ("q2", "a2"), ("q3", "a3")]


def test_decide_accepts_small_delta_when_grounded():
    d = decide(probe_before=1.0, probe_after=1.05, grounded=True, threshold=0.15)
    assert isinstance(d, GateDecision)
    assert d.accepted is True
    assert d.reason == "ok"
    assert abs(d.probe_delta_rel - 0.05) < 1e-9
    json.loads(d.to_json())  # must be JSON-serializable


def test_decide_rejects_large_probe_regression():
    d = decide(probe_before=1.0, probe_after=1.5, grounded=True, threshold=0.15)
    assert d.accepted is False
    assert d.reason == "probe_regression"
    assert d.probe_delta_rel > 0.15


def test_decide_rejects_ungrounded_when_required():
    d = decide(probe_before=1.0, probe_after=1.0, grounded=False, threshold=0.15,
               require_grounded=True)
    assert d.accepted is False
    assert d.reason == "ungrounded"


def test_decide_ignores_groundedness_when_not_required():
    d = decide(probe_before=1.0, probe_after=1.02, grounded=False, threshold=0.15,
               require_grounded=False)
    assert d.accepted is True


def test_decide_carries_delta_norm_through():
    d = decide(probe_before=1.0, probe_after=1.02, grounded=True, delta_norm=0.42)
    assert d.delta_norm == 0.42


def test_gate_ledger_append_and_summary(tmp_path):
    run_dir = tmp_path / "runs" / "demo"
    ledger = GateLedger(str(run_dir))

    decisions = [
        decide(probe_before=1.0, probe_after=1.01, grounded=True),   # ok
        decide(probe_before=1.0, probe_after=1.5, grounded=True),    # probe_regression
        decide(probe_before=1.0, probe_after=1.0, grounded=False),   # ungrounded
        decide(probe_before=1.0, probe_after=1.02, grounded=True),   # ok
    ]
    for i, d in enumerate(decisions):
        ledger.append(qid=f"q{i}", decision=d)

    assert (run_dir / "gate_ledger.jsonl").exists()
    lines = (run_dir / "gate_ledger.jsonl").read_text().strip().splitlines()
    assert len(lines) == 4
    first = json.loads(lines[0])
    assert first["qid"] == "q0"
    assert first["accepted"] is True

    summary = ledger.summary()
    assert summary["n"] == 4
    assert summary["accepted"] == 2
    assert summary["rejected"] == 2
    assert summary["reasons"] == {"ok": 2, "probe_regression": 1, "ungrounded": 1}
