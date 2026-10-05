"""The held-out measurement harness: measure-once ledger, gate numbers, variants. A tiny constructed set stands in for any held-
out file."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("aam", ROOT / "scripts" / "accused_attribution_measure.py")
aam = importlib.util.module_from_spec(spec)
spec.loader.exec_module(aam)

REF = {"id": "a2", "aliases": ["Accused No.2", "A2"], "other_parties": [["Accused No.1", "A1"]]}


def _row(i: int, cell: str, sentence: str, expected: str) -> dict:
    return {
        "id": f"r{i}",
        "cell": cell,
        "sentence": sentence,
        "accused_ref": REF,
        "expected": expected,
        "kind": "positive" if cell.startswith("P") else "negative",
    }


ROWS = [
    _row(1, "P1_active", "Accused No.2 beat Nila.", "pass"),
    _row(2, "P1_active", "A2 slapped Nila.", "pass"),
    _row(3, "P4_pronoun", "Accused No.2 came home and he beat Nila.", "pass"),
    _row(4, "N1_co", "Accused No.1 beat Nila.", "refuse"),
    _row(5, "N5b_unlisted", "Accused No.2, together with Gopal Rao, beat Nila.", "refuse"),
]


def _write(tmp_path: Path) -> Path:
    p = tmp_path / "set.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in ROWS) + "\n")
    return p


def test_scores_the_gate_with_and_without_p4_and_counts_negative_misses(tmp_path: Path, capsys) -> None:
    out, ledger = tmp_path / "o.json", tmp_path / "ledger.jsonl"
    assert aam.main(["--set", str(_write(tmp_path)), "--variant", "d0", "--ledger", str(ledger), "--out", str(out)]) == 0
    s = json.loads(out.read_text())["summary"]
    assert s["gate_P1_P6_including_P4"]["refused"] == 1 and s["gate_P1_P6_including_P4"]["n"] == 3
    assert s["P1_P6_excluding_P4"]["refused"] == 0 and s["P1_P6_excluding_P4"]["n"] == 2
    assert s["negatives"] == {"passed_miss": 0, "n": 2, "by_cell": {"N1_co": 0, "N5b_unlisted": 0}}
    assert s["gate_20pct_met_point_estimate"] is False and s["set_sha256"] and s["variant"] == "d0"
    assert s["gate_P1_P6_including_P4"]["cp95_upper"] >= s["gate_P1_P6_including_P4"]["point"]


def test_measure_once_refuses_a_second_scoring_of_the_same_set_and_variant(tmp_path: Path) -> None:
    p, ledger = _write(tmp_path), tmp_path / "ledger.jsonl"
    base = ["--set", str(p), "--ledger", str(ledger)]
    aam.main([*base, "--variant", "d0", "--out", str(tmp_path / "a.json")])
    with pytest.raises(SystemExit, match="already scored"):
        aam.main([*base, "--variant", "d0", "--out", str(tmp_path / "b.json")])
    aam.main([*base, "--variant", "d0b", "--out", str(tmp_path / "c.json")])  # another variant is a separate measurement
    aam.main([*base, "--variant", "d0", "--allow-repeat", "--out", str(tmp_path / "d.json")])
    entries = [json.loads(x) for x in ledger.read_text().splitlines()]
    assert [e["repeat"] for e in entries] == [False, False, True]


def test_m1_needs_a_backend_and_the_cp95_helper_is_exact() -> None:
    with pytest.raises(SystemExit, match="needs --base-url and --model"):
        aam.make_checker("m1", None, None, 0.6)
    assert abs(aam.cp_upper(0, 99) - 0.0298) < 1e-3 and aam.cp_upper(3, 3) == 1.0


def test_d0b_resolves_a_verb_outside_the_lexicon_that_d0_cannot(tmp_path: Path) -> None:
    d0, d0b = aam.make_checker("d0", None, None, 0.6), aam.make_checker("d0b", None, None, 0.6)
    ref = aam.AccusedRef("a2", ("Accused No.2",), (("Accused No.1",),))
    s = "Accused No.2 whipped Nila."
    assert not d0(s, ref).passed and d0b(s, ref).passed
    assert not d0b("Accused No.1 whipped Nila.", ref).passed
