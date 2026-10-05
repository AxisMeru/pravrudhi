"""The dev measurement script is deterministic and its cells behave as documented (constructed sentences only)."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parent.parent


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("aadm", ROOT / "scripts" / "accused_attribution_dev_measure.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_measurement_is_deterministic_and_negatives_are_all_refused() -> None:
    m = _load()
    a, b = m.run(10, 7), m.run(10, 7)
    assert a == b
    assert a["negatives_refused"]["rate"] == 1.0
    cells = a["cells"]
    for name in (
        "P01 'Accused No.n' + lexicon verb",
        "P09 object-position collective (should PASS)",
        "P11 verb outside the lexicon",
    ):
        assert cells[name]["refused"] == 0, name
    assert cells["P13 pronoun-only (refused by design, R4)"]["refused"] == 10


def test_the_true_proof_refusal_rate_is_within_decision_4_on_the_constructed_set() -> None:
    """Lead-2 decision 4: at most 20% of true proofs refused on dev (point estimate; CP95 reported beside it)."""
    r = _load().run(40, 20261005)["true_proof_refusal_all_cells"]
    assert r["point"] <= 0.20 and r["cp95_upper"] >= r["point"]


def test_the_exact_upper_bound_matches_known_values() -> None:
    cp = _load().cp_upper
    assert abs(cp(0, 99) - 0.0298) < 1e-3 and abs(cp(2, 109) - 0.057) < 1e-3
