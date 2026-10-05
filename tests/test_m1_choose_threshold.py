"""The M1 threshold sweep is exact and its rule is deterministic (synthetic rows)."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "m1ct", Path(__file__).resolve().parent.parent / "scripts" / "m1_choose_threshold.py"
)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def row(cell, expected, got, p, mass):
    return {"cell": cell, "expected": expected, "got": got, "actor_p": p, "mass_ratio": mass}


ROWS = [
    row("P1_a", "pass", "pass", 0.95, 0.99),
    row("P1_a", "pass", "pass", 0.65, 0.99),
    row("P1_a", "pass", "pass", 0.55, 0.4),
    row("P1_a", "pass", "refuse", None, None),
    row("N1_co", "refuse", "pass", 0.55, 0.99),  # a false pass at low confidence: only theta >= 0.6 removes it
    row("N1_co", "refuse", "refuse", None, None),
]


def test_sweep_counts_are_exact_and_monotone() -> None:
    t = {(x["theta"], x["mass_floor"]): x for x in m.sweep(ROWS)}
    assert t[(0.5, 0.3)]["neg_misses"] == 1 and t[(0.6, 0.3)]["neg_misses"] == 0
    assert t[(0.5, 0.3)]["pos_refused"] == 1 and t[(0.6, 0.3)]["pos_refused"] == 2 and t[(0.9, 0.7)]["pos_refused"] == 3 + 1 - 1
    assert all(
        t[(a, f)]["pos_refused"] <= t[(b, f)]["pos_refused"] for a, b in ((0.5, 0.6), (0.6, 0.7), (0.8, 0.9)) for f in m.FLOORS
    )


def test_the_rule_picks_zero_misses_then_lowest_refusal_then_higher_theta() -> None:
    pick = m.choose(m.sweep(ROWS))
    assert pick["neg_misses"] == 0 and pick["pos_refused"] == 2 and (pick["theta"], pick["mass_floor"]) == (0.6, 0.7)
    assert m.choose([{"theta": 0.5, "mass_floor": 0.3, "neg_misses": 2, "pos_refusal": 0.0}]) is None


def test_main_writes_frozen_values_and_refuses_when_nothing_is_safe(tmp_path: Path) -> None:
    res = tmp_path / "dev.json"
    res.write_text(json.dumps({"summary": {"set_sha256": "abc"}, "rows": ROWS}))
    out = tmp_path / "frozen.json"
    assert m.main(["--dev-result", str(res), "--out", str(out)]) == 0
    frozen = json.loads(out.read_text())
    assert frozen["chosen"] and frozen["dev_set_sha256"] == "abc" and len(frozen["dev_result_sha256"]) == 64
    bad = [row("P1_a", "pass", "pass", 0.95, 0.99), row("N1_co", "refuse", "pass", 0.99, 0.99)]
    res.write_text(json.dumps({"summary": {"set_sha256": "abc"}, "rows": bad}))
    assert m.main(["--dev-result", str(res), "--out", str(out)]) == 1
    assert json.loads(out.read_text())["chosen"] is None
