"""prabhasa-samskrutam's benchmark panel enters the ledger by hash, like any external scorer.

The panel is the product measuring itself: Nyaya validity and hetvabhasa rejection (its verifier, track A),
plus the karaka and morphology probes, BPB and round-trip fidelity (its model, track B). Before this, not one
of those figures was visible to Pravrudhi -- `record_external` admitted lm-eval and EvalPlus and nothing else
-- so the product could improve or regress and Studio had no signal either way.

Spec: docs/superpowers/specs/2026-09-09-prabhasa-nyaya-measurement-design.md, card N1.
"""

from __future__ import annotations

import json
from pathlib import Path

from pravrudhi.application.external import parse_prabhasa_panel

# Field-for-field the shape of ~/projects/prabhasa-samskrutam/research/closure/m5_benchmarks.json, read
# 2026-09-09, trimmed to the groups that carry both a value and an n.
PANEL = {
    "pretraining_tokens": 5250000000,
    "sanskrit_competence": {
        "karaka_probe": {"f1_score": 0.994, "n_test_sentences": 8000, "roles_covered": 3},
        "morphology_probe": {"probes": {
            "case": {"accuracy": 0.8209, "n_classes": 8, "n_test": 5076},
            "upos": {"accuracy": 0.8522, "n_classes": 11, "n_test": 9585},
        }},
        "bpb_by_domain": {"dcs": {"mean_bpb": 1.6337, "std_bpb": 0.3335, "n_docs": 500}},
    },
    "structured_reasoning": {
        "nyaya_validity": {"valid_pass_at_5": 1.0, "n_syllogisms_tested": 2},
        "hetvabhasa_rejection": {"rejection_rate": 1.0, "n_invalid_tested": 3},
    },
    "round_trip_fidelity": {
        "semantic_fidelity": {"mean_similarity": 0.6352741733193398, "threshold": 0.7,
                              "pass_rate": 0.13, "n_pairs": 100},
    },
}


def test_every_leaf_group_becomes_an_addressable_task(tmp_path: Path) -> None:
    p = tmp_path / "m5_benchmarks.json"
    p.write_text(json.dumps(PANEL))
    parsed = parse_prabhasa_panel(p)

    assert parsed["tool"] == "prabhasa"
    tasks = parsed["metrics"]
    # Dotted path, panel root stripped: unique, and nameable in an objective's `metric:` line the same way
    # `mmlu_professional_law acc,none` already is.
    assert "structured_reasoning.nyaya_validity" in tasks
    assert tasks["structured_reasoning.nyaya_validity"]["valid_pass_at_5"] == 1.0
    assert "sanskrit_competence.morphology_probe.probes.case" in tasks
    assert tasks["sanskrit_competence.morphology_probe.probes.case"]["accuracy"] == 0.8209


def test_the_sample_size_travels_with_the_metric(tmp_path: Path) -> None:
    """A rate without its n is not a measurement. nyaya_validity reads 1.0 at n=2, which excludes nothing."""
    p = tmp_path / "m5_benchmarks.json"
    p.write_text(json.dumps(PANEL))
    n = parse_prabhasa_panel(p)["n_samples"]

    assert n["structured_reasoning.nyaya_validity"] == 2
    assert n["structured_reasoning.hetvabhasa_rejection"] == 3
    assert n["sanskrit_competence.karaka_probe"] == 8000
    assert n["sanskrit_competence.morphology_probe.probes.case"] == 5076
    assert n["round_trip_fidelity.semantic_fidelity"] == 100
    assert n["sanskrit_competence.bpb_by_domain.dcs"] == 500


def test_an_n_key_is_not_itself_reported_as_a_metric(tmp_path: Path) -> None:
    """`n_pairs` is the denominator, not a result. Reporting it would put 100 in a column of rates."""
    p = tmp_path / "m5_benchmarks.json"
    p.write_text(json.dumps(PANEL))
    metrics = parse_prabhasa_panel(p)["metrics"]["round_trip_fidelity.semantic_fidelity"]

    assert "pass_rate" in metrics and "mean_similarity" in metrics
    assert "n_pairs" not in metrics


def test_an_instrument_descriptor_is_not_a_result(tmp_path: Path) -> None:
    """`threshold` is where the rate was cut and `n_classes` is how many labels the probe had. Neither is a
    score, and both would read as one in a rendered table."""
    p = tmp_path / "m5_benchmarks.json"
    p.write_text(json.dumps(PANEL))
    metrics = parse_prabhasa_panel(p)["metrics"]

    assert "threshold" not in metrics["round_trip_fidelity.semantic_fidelity"]
    assert "n_classes" not in metrics["sanskrit_competence.morphology_probe.probes.case"]
    assert "roles_covered" not in metrics["sanskrit_competence.karaka_probe"]


def test_a_group_with_no_sample_size_reports_zero_rather_than_guessing(tmp_path: Path) -> None:
    p = tmp_path / "m5_benchmarks.json"
    p.write_text(json.dumps({"g": {"sub": {"value": 0.5}}}))
    parsed = parse_prabhasa_panel(p)

    assert parsed["metrics"]["g.sub"] == {"value": 0.5}
    assert parsed["n_samples"]["g.sub"] == 0


def test_a_scalar_at_the_panel_root_is_provenance_not_a_metric(tmp_path: Path) -> None:
    p = tmp_path / "m5_benchmarks.json"
    p.write_text(json.dumps(PANEL))
    parsed = parse_prabhasa_panel(p)

    assert parsed["panel"]["pretraining_tokens"] == 5250000000
    assert not any(t == "pretraining_tokens" for t in parsed["metrics"])


def test_a_panel_that_is_not_an_object_is_refused(tmp_path: Path) -> None:
    """A refusal names the file. Returning no metrics would admit an empty row by hash."""
    p = tmp_path / "m5_benchmarks.json"
    p.write_text(json.dumps([1, 2, 3]))
    try:
        parse_prabhasa_panel(p)
    except ValueError as error:
        assert "m5_benchmarks.json" in str(error)
    else:
        raise AssertionError("a non-object panel must be refused")


def test_the_panel_is_admitted_to_the_ledger_by_hash(tmp_path: Path) -> None:
    from pravrudhi.application.external import external_rows, record_external
    from pravrudhi_kernel.ledger import LedgerWriter

    (tmp_path / "research").mkdir()
    LedgerWriter.open(tmp_path / "research" / "ledger.jsonl", "0.1.0")
    p = tmp_path / "m5_benchmarks.json"
    p.write_text(json.dumps(PANEL))

    row = record_external(
        tmp_path, p, tool="prabhasa", track="P", condition="base", model="prabhasa-1.13b", night=0,
    )

    assert row["tool"] == "prabhasa"
    assert row["tier"] == "external"
    assert row["track"] == "P"
    assert len(row["sha256"]) == 64
    (back,) = external_rows(tmp_path / "research" / "ledger.jsonl")
    assert back["metrics"]["structured_reasoning.nyaya_validity"]["valid_pass_at_5"] == 1.0
    assert back["n_samples"]["structured_reasoning.nyaya_validity"] == 2


def test_an_unknown_tool_is_refused_rather_than_parsed_as_evalplus(tmp_path: Path) -> None:
    """`record_external` dispatched lm-eval on an exact match and EVERYTHING ELSE to evalplus, so a typo in
    --tool handed the file to the wrong parser and admitted whatever fell out, by hash, as evidence."""
    from pravrudhi.application.external import record_external
    from pravrudhi_kernel.ledger import LedgerWriter

    (tmp_path / "research").mkdir()
    LedgerWriter.open(tmp_path / "research" / "ledger.jsonl", "0.1.0")
    p = tmp_path / "m5_benchmarks.json"
    p.write_text(json.dumps(PANEL))
    try:
        record_external(tmp_path, p, tool="prabahsa", track="P", condition="base", model="m", night=0)
    except ValueError as error:
        assert "prabahsa" in str(error)
    else:
        raise AssertionError("an unknown --tool must be refused, not routed to a parser by fallthrough")


def test_a_panel_row_renders_one_line_per_metric() -> None:
    """`headlines` sent every non-lm-eval row to `_headline`, whose branch reads row["dataset"] -- a key only
    EvalPlus rows carry. A panel row raised on render."""
    from pravrudhi.application.external import headlines

    row = {
        "tool": "prabhasa",
        "metrics": {
            "structured_reasoning.nyaya_validity": {"valid_pass_at_5": 1.0},
            "sanskrit_competence.karaka_probe": {"f1_score": 0.994},
        },
        "n_samples": {"structured_reasoning.nyaya_validity": 2, "sanskrit_competence.karaka_probe": 8000},
    }
    got = {name: (value, n) for name, value, _stderr, n in headlines(row)}

    assert got["structured_reasoning.nyaya_validity valid_pass_at_5"] == (1.0, 2)
    assert got["sanskrit_competence.karaka_probe f1_score"] == (0.994, 8000)


def test_every_group_renders_not_only_the_first() -> None:
    """The same fault the lm-eval branch was fixed for: a panel carries many groups, and reading only the
    first would leave a named metric showing as unmeasured."""
    from pravrudhi.application.external import headlines

    row = {
        "tool": "prabhasa",
        "metrics": {f"g{i}": {"v": float(i)} for i in range(5)},
        "n_samples": {f"g{i}": 10 for i in range(5)},
    }
    assert len(headlines(row)) == 5


def test_the_panel_row_renders_into_the_evidence_document(tmp_path: Path) -> None:
    from pravrudhi.application.external import record_external, render_external
    from pravrudhi_kernel.ledger import LedgerWriter

    (tmp_path / "research").mkdir()
    ledger = tmp_path / "research" / "ledger.jsonl"
    LedgerWriter.open(ledger, "0.1.0")
    p = tmp_path / "m5_benchmarks.json"
    p.write_text(json.dumps(PANEL))
    record_external(tmp_path, p, tool="prabhasa", track="P", condition="base", model="prabhasa-1.13b", night=0)

    text = render_external(ledger)
    assert "prabhasa" in text
    assert "nyaya_validity" in text
