# ruff: noqa: E501
"""The S2 flag: fail closed (only the boolean true is on), a malformed cap with the flag on turns it OFF, the shipped config has it off."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from pravrudhi.application.tabular_config import FLAG, TabularConfig, load_tabular_config, parse_tabular_config

REPO = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize("body", [None, {}, [], FLAG, {FLAG: False}])
def test_anything_but_the_key_with_true_is_off(body: Any) -> None:
    assert parse_tabular_config(body) is None


@pytest.mark.parametrize("value", ["true", "yes", 1, "on", None, 0, "false"])
def test_a_value_that_is_not_the_boolean_true_is_off(value: Any) -> None:
    assert parse_tabular_config({FLAG: value}) is None


def test_true_is_on_with_the_default_caps() -> None:
    cfg = parse_tabular_config({FLAG: True})
    assert cfg == TabularConfig()
    assert (cfg.max_papers, cfg.max_passages, cfg.max_chars, cfg.max_cells_per_paper, cfg.max_cells_per_matter, cfg.max_response_bytes, cfg.max_seconds) == (
        200, 50_000, 5_000_000, 2_000, 20_000, 4_000_000, 30.0,
    )  # fmt: skip


def test_good_caps_are_read_and_each_malformed_one_turns_the_table_off() -> None:
    cfg = parse_tabular_config(
        {FLAG: True, "tab_max_concurrent": 3, "tab_max_papers": 10, "tab_max_cells_per_paper": 7, "tab_max_seconds": 12}
    )
    assert cfg is not None and (cfg.max_concurrent, cfg.max_papers, cfg.max_cells_per_paper, cfg.max_seconds) == (3, 10, 7, 12.0)
    for key in (
        "tab_max_concurrent",
        "tab_max_papers",
        "tab_max_passages",
        "tab_max_chars",
        "tab_max_cells_per_paper",
        "tab_max_cells_per_matter",
        "tab_max_response_bytes",
    ):
        for bad in (0, -1, "2", 2.5, True, None, 10**12):
            assert parse_tabular_config({FLAG: True, key: bad}) is None, (key, bad)
    for bad in (0, -1, "5", True, None, float("inf"), float("nan"), 601):
        assert parse_tabular_config({FLAG: True, "tab_max_seconds": bad}) is None, bad


def test_a_missing_or_broken_file_is_off(tmp_path: Path) -> None:
    assert load_tabular_config(tmp_path) is None
    (tmp_path / "configs").mkdir()
    (tmp_path / "configs" / "partner_api.yaml").write_text(f"{FLAG}: [unclosed", encoding="utf-8")
    assert load_tabular_config(tmp_path) is None


def test_the_shipped_config_has_the_table_off() -> None:
    assert load_tabular_config(REPO) is None
