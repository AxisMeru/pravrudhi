"""Tests for prototypes.nyaya_ttt_rsi.report: markdown/HTML rendering of a
run's report.json, robustness against missing/malformed keys, and the
`python3 -m prototypes.nyaya_ttt_rsi.report <run_dir>` CLI end-to-end."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from prototypes.nyaya_ttt_rsi.report import render_html, render_markdown

REPO_ROOT = Path(__file__).resolve().parents[3]


def _sample_report() -> dict:
    return {
        "run": "ttt-rsi-demo",
        "checkpoint": "ckpt-0042",
        "created": "2026-09-13T12:00:00Z",
        "n_heldout": 690,
        "conditions": {
            "A": {
                "name": "frozen closed-book",
                "metrics": {
                    "citation_precision": {"k": 1, "n": 227, "rate": 0.0044, "wilson": [0.0002, 0.024]},
                    "citation_recall": {"k": 30, "n": 227, "rate": 0.1322, "wilson": [0.094, 0.181]},
                },
                "timing": {"wall_s": 123.4, "peak_vram_gib": 3.2},
            },
            "B": {
                "name": "frozen open-book",
                "metrics": {
                    "citation_precision": {"k": 40, "n": 227, "rate": 0.1762, "wilson": [0.131, 0.233]},
                    "citation_recall": {"k": 102, "n": 227, "rate": 0.4493, "wilson": [0.385, 0.515]},
                    "abstention_correct": {"k": 5, "n": 60, "rate": 0.0833, "wilson": [0.036, 0.182]},
                },
                "timing": {"wall_s": 200.0, "peak_vram_gib": 4.1},
            },
            "C": {
                "name": "ttt ephemeral",
                "metrics": {
                    "citation_recall": {"k": 120, "n": 227, "rate": 0.5286, "wilson": [0.463, 0.593]},
                    "grounded_rate": {"k": 180, "n": 227, "rate": 0.793, "wilson": [0.734, 0.842]},
                },
            },
            "D": {
                "name": "consolidated lora",
                "metrics": {
                    "citation_recall": {"k": 131, "n": 227, "rate": 0.577, "wilson": [0.511, 0.64]},
                    "unparsed": {"k": 2, "n": 690, "rate": 0.0029, "wilson": [0.0008, 0.0105]},
                },
            },
        },
        "paired": [
            {
                "a": "A",
                "b": "B",
                "metric": "citation_recall",
                "b_only": 12,
                "c_only": 1,
                "mcnemar_p": 0.003,
                "bootstrap_ci": [0.02, 0.09],
            },
            {
                "a": "C",
                "b": "D",
                "metric": "grounded_rate",
                "b_only": 7,
                "c_only": 3,
                "mcnemar_p": 0.34,
                "bootstrap_ci": [-0.01, 0.05],
            },
        ],
        "gate": {
            "accepted": 410,
            "rejected": 53,
            "reasons": {"probe_regression": 40, "ungrounded": 13},
        },
        "rounds": [
            {
                "round": 1,
                "metrics": {
                    "citation_recall": {"k": 80, "n": 227, "rate": 0.352, "wilson": [0.292, 0.417]},
                    "grounded_rate": {"k": 150, "n": 227, "rate": 0.661, "wilson": [0.596, 0.72]},
                },
                "gate": {"accepted": 120, "rejected": 30},
            },
            {
                "round": 2,
                "metrics": {
                    "citation_recall": {"k": 91, "n": 227, "rate": 0.401, "wilson": [0.338, 0.467]},
                    "grounded_rate": {"k": 163, "n": 227, "rate": 0.718, "wilson": [0.656, 0.774]},
                },
                "gate": {"accepted": 140, "rejected": 12},
            },
            {
                "round": 3,
                "metrics": {
                    "citation_recall": {"k": 102, "n": 227, "rate": 0.449, "wilson": [0.385, 0.515]},
                    "grounded_rate": {"k": 171, "n": 227, "rate": 0.753, "wilson": [0.693, 0.806]},
                },
                "gate": {"accepted": 150, "rejected": 11},
            },
        ],
        "notes": ["probe NLL threshold 0.15", "held-out slice frozen at 690 queries"],
    }


def test_render_markdown_full_report() -> None:
    md = render_markdown(_sample_report())
    assert "# Run report: ttt-rsi-demo" in md
    assert "ckpt-0042" in md
    assert "2026-09-13T12:00:00Z" in md
    assert "690" in md
    assert "frozen closed-book" in md
    assert "frozen open-book" in md
    assert "consolidated lora" in md
    assert "citation_precision" in md
    assert "0.004 (1/227) [0.000\u20130.024]" in md
    assert "## Paired comparisons" in md
    assert "A \u2192 B" in md
    assert "McNemar" in md
    assert "0.003" in md
    assert "[0.020, 0.090]" in md
    assert "## Gate" in md
    assert "410" in md
    assert "probe_regression: 40" in md
    assert "ungrounded: 13" in md
    assert "## Rounds" in md
    assert "0.352 [0.292\u20130.417]" in md
    assert "## Notes" in md
    assert "- probe NLL threshold 0.15" in md


def test_render_html_full_report() -> None:
    doc = render_html(_sample_report())
    assert doc.startswith("<!doctype html>")
    assert "ttt-rsi-demo" in doc
    assert "prefers-color-scheme" in doc
    assert "<style>" in doc
    assert "<table>" in doc
    assert "<thead>" in doc
    assert "frozen closed-book" in doc
    assert "citation_recall" in doc
    assert "0.004" in doc
    assert "McNemar" in doc
    assert "probe_regression" in doc
    assert "<svg" in doc
    assert 'width="720"' in doc
    assert 'height="240"' in doc
    assert "<polyline" in doc
    assert "<polygon" in doc
    assert ">Round<" in doc
    assert ">Rate<" in doc
    assert "probe NLL threshold 0.15" in doc
    assert "http://" not in doc.replace("http://www.w3.org/2000/svg", "")
    assert "<script" not in doc


def test_minimal_report_renders() -> None:
    md = render_markdown({"run": "x"})
    doc = render_html({"run": "x"})
    assert "x" in md
    assert "x" in doc
    assert "<svg" not in doc
    for empty in ({}, {"conditions": {}, "paired": [], "rounds": [], "notes": []}):
        render_markdown(empty)
        render_html(empty)


def test_malformed_report_never_crashes() -> None:
    junk = {
        "run": 7,
        "checkpoint": {"not": "a string"},
        "conditions": "nope",
        "paired": [3, None, {"a": "A"}, {"mcnemar_p": "bad", "bootstrap_ci": [1]}],
        "gate": [],
        "rounds": {"x": 1},
        "notes": 5,
    }
    md = render_markdown(junk)
    doc = render_html(junk)
    assert isinstance(md, str) and isinstance(doc, str)
    weird = {
        "conditions": {"A": {"metrics": {"m": {"rate": "NaN", "wilson": [0.1]}, "m2": None}}},
        "rounds": [{"round": "one", "metrics": {"m": {"rate": 0.5}}}, "junk", {}],
    }
    render_markdown(weird)
    render_html(weird)


def test_html_escapes_free_text() -> None:
    doc = render_html({"run": "<script>alert(1)</script>", "notes": ["a & b < c"]})
    assert "<script>" not in doc
    assert "&lt;script&gt;" in doc
    assert "a &amp; b &lt; c" in doc


def test_cli_end_to_end(tmp_path: Path) -> None:
    run_dir = tmp_path / "demo-run"
    run_dir.mkdir()
    (run_dir / "report.json").write_text(json.dumps(_sample_report()), encoding="utf-8")

    proc = subprocess.run(
        [sys.executable, "-m", "prototypes.nyaya_ttt_rsi.report", str(run_dir)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr

    md_path = run_dir / "report.md"
    html_path = run_dir / "report.html"
    assert md_path.is_file()
    assert html_path.is_file()
    md = md_path.read_text(encoding="utf-8")
    doc = html_path.read_text(encoding="utf-8")
    assert "# Run report: ttt-rsi-demo" in md
    assert "0.004" in md
    assert "<svg" in doc
    assert "McNemar" in doc


def test_cli_missing_report_json_fails(tmp_path: Path) -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "prototypes.nyaya_ttt_rsi.report", str(tmp_path)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert proc.returncode != 0
    assert not (tmp_path / "report.md").exists()
