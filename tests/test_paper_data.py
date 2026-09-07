"""The paper's tables are generated from the ledger alone; no number appears that the ledger does not contain."""
from __future__ import annotations

import json
import re
from pathlib import Path

from pravrudhi.application.discordance import discordance
from pravrudhi.application.external import record_external
from pravrudhi.application.paper_data import DO_NOT_EDIT, results_tables, write_tables
from pravrudhi_kernel.ledger import LedgerWriter


def _lm_eval_with_items(path: Path, acc: float, stderr: float, items: dict[str, int]) -> Path:
    path.write_text(
        json.dumps(
            {
                "results": {"gsm8k": {"exact_match,strict-match": acc, "exact_match_stderr,strict-match": stderr}},
                "n-samples": {"gsm8k": {"effective": len(items)}},
                "n-shot": {"gsm8k": 5},
                "lm_eval_version": "0.4.9",
                "samples": {"gsm8k": [{"doc_id": d, "exact_match": v} for d, v in items.items()]},
            }
        )
    )
    return path


def _ledger(tmp_path: Path) -> Path:
    (tmp_path / "research").mkdir()
    LedgerWriter.open(tmp_path / "research" / "ledger.jsonl", "0.1.0")
    return tmp_path / "research" / "ledger.jsonl"


class TestExternalTable:
    def test_carries_every_row_with_no_invented_numbers(self, tmp_path: Path) -> None:
        _ledger(tmp_path)
        base_items = {"0": 1, "1": 0, "2": 1, "3": 0}
        cand_items = {"0": 1, "1": 1, "2": 0, "3": 0}
        record_external(
            tmp_path, _lm_eval_with_items(tmp_path / "base.json", 0.4321, 0.0111, base_items),
            tool="lm-eval", track="M", condition="base", model="qwen", night=1,
        )
        record_external(
            tmp_path, _lm_eval_with_items(tmp_path / "cand.json", 0.5678, 0.0122, cand_items),
            tool="lm-eval", track="M", condition="adapter:c-1", model="qwen", night=1,
        )
        tables = results_tables(tmp_path)
        text = tables["external_benchmarks"]
        assert DO_NOT_EDIT.splitlines()[0] in text
        assert "0.4321" in text and "0.0111" in text
        assert "0.5678" in text and "0.0122" in text
        assert "qwen" in text and "lm-eval" in text
        # Every 4-decimal number printed must be one of the ledger's own values; no other value was ever recorded.
        printed = {float(x) for x in re.findall(r"\b0\.\d{4}\b", text)}
        assert printed == {0.4321, 0.0111, 0.5678, 0.0122}

    def test_unmeasured_workspace_renders_a_footnoted_dash_never_a_blank_or_number(self, tmp_path: Path) -> None:
        tables = results_tables(tmp_path)  # no research/ledger.jsonl at all
        text = tables["external_benchmarks"]
        assert "---\\textsuperscript{a}" in text
        assert "no ledger yet" in text
        assert not re.search(r"\b\d+\.\d+\b", text), "no measurement exists yet, so no number may be printed"


class TestPairedComparisons:
    def test_mcnemar_counts_and_p_value_match_discordance_on_the_shared_items(self, tmp_path: Path) -> None:
        _ledger(tmp_path)
        base_items = {"0": 1, "1": 0, "2": 1, "3": 0}
        cand_items = {"0": 1, "1": 1, "2": 0, "3": 0}
        record_external(
            tmp_path, _lm_eval_with_items(tmp_path / "base.json", 0.40, 0.01, base_items),
            tool="lm-eval", track="M", condition="base", model="qwen", night=1,
        )
        record_external(
            tmp_path, _lm_eval_with_items(tmp_path / "cand.json", 0.50, 0.01, cand_items),
            tool="lm-eval", track="M", condition="adapter:c-1", model="qwen", night=1,
        )
        expect = discordance(base_items, cand_items)
        text = results_tables(tmp_path)["paired_comparisons"]
        assert f"{expect.wins} & {expect.losses} & {expect.p_mcnemar:.4f}" in text
        assert "adapter:c-1" in text

    def test_a_comparison_with_no_shared_items_is_a_footnoted_dash(self, tmp_path: Path) -> None:
        _ledger(tmp_path)
        record_external(
            tmp_path, _lm_eval_with_items(tmp_path / "base.json", 0.40, 0.01, {"0": 1}),
            tool="lm-eval", track="M", condition="base", model="qwen", night=1,
        )
        record_external(
            tmp_path, _lm_eval_with_items(tmp_path / "cand.json", 0.50, 0.01, {"other-0": 1}),
            tool="lm-eval", track="M", condition="adapter:c-1", model="qwen", night=1,
        )
        text = results_tables(tmp_path)["paired_comparisons"]
        assert "---\\textsuperscript{a}" in text
        assert "no per-item vectors" in text

    def test_no_ledger_at_all_is_a_footnoted_dash(self, tmp_path: Path) -> None:
        text = results_tables(tmp_path)["paired_comparisons"]
        assert "---\\textsuperscript{a}" in text
        assert "no ledger yet" in text


class TestNightsTable:
    def test_reports_policy_spend_and_outcomes_from_the_ledger_alone(self, tmp_path: Path) -> None:
        ledger = _ledger(tmp_path)
        w = LedgerWriter.open(ledger, "0.1.0")
        w.append(
            "audit", "kernel",
            {"kind": "night_start", "severity": "info", "track": "lora", "selection_policy": "efe"},
            epoch=0, night=1,
        )
        w.append(
            "audit", "kernel",
            {
                "kind": "night_end", "severity": "info", "track": "lora", "spent_gpu_h": 1.5,
                "outcomes": {"c-1": "promoted", "c-2": "pruned", "c-3": "pruned"},
            },
            epoch=0, night=1,
        )
        text = results_tables(tmp_path)["nights"]
        assert "efe" in text
        assert "1.500" in text
        assert "1 promoted / 2 pruned / 3 candidates" in text

    def test_a_night_that_never_closed_leaves_no_row_and_no_number(self, tmp_path: Path) -> None:
        ledger = _ledger(tmp_path)
        w = LedgerWriter.open(ledger, "0.1.0")
        w.append(
            "audit", "kernel",
            {"kind": "night_start", "severity": "info", "track": "lora", "selection_policy": "efe"},
            epoch=0, night=1,
        )
        text = results_tables(tmp_path)["nights"]
        assert "---\\textsuperscript{a}" in text
        assert "no night has closed" in text
        assert "efe" not in text, "a policy from a night that never closed must not be reported as an outcome"


class TestWriteTables:
    def test_writes_all_three_generated_files_with_the_do_not_edit_marker(self, tmp_path: Path) -> None:
        _ledger(tmp_path)
        written = write_tables(tmp_path)
        names = {p.name for p in written}
        assert names == {"external_benchmarks.tex", "paired_comparisons.tex", "nights.tex"}
        for path in written:
            assert (tmp_path / "paper" / "generated" / path.name).exists()
            text = path.read_text()
            assert "GENERATED, DO NOT EDIT" in text
