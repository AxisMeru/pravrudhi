"""The S1 flag: fail closed (only the boolean true is on), a malformed cap with the flag on turns it OFF, the shipped config has it off."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from pravrudhi.application.matter_sweep_config import FLAG, SweepConfig, load_sweep_config, parse_sweep_config

REPO = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize("body", [None, {}, [], FLAG, {FLAG: False}])
def test_anything_but_the_key_with_true_is_off(body: Any) -> None:
    assert parse_sweep_config(body) is None


@pytest.mark.parametrize("value", ["true", "yes", 1, "on", None, 0, "false"])
def test_a_value_that_is_not_the_boolean_true_is_off(value: Any) -> None:
    assert parse_sweep_config({FLAG: value}) is None


def test_true_is_on_with_the_default_caps() -> None:
    cfg = parse_sweep_config({FLAG: True})
    assert cfg == SweepConfig()
    assert (cfg.max_papers, cfg.max_passages, cfg.max_chars, cfg.max_distinct_citations, cfg.max_seconds, cfg.max_rows, cfg.max_bytes) == (
        200, 50_000, 5_000_000, 500, 60.0, 5_000, 4_000_000,
    )  # fmt: skip


def test_good_caps_are_read_and_each_malformed_one_turns_the_sweep_off() -> None:
    good = {
        FLAG: True,
        "sweep_max_concurrent": 3,
        "sweep_max_papers": 10,
        "sweep_max_distinct_citations": 7,
        "sweep_max_seconds": 12,
    }
    cfg = parse_sweep_config(good)
    assert cfg is not None and (cfg.max_concurrent, cfg.max_papers, cfg.max_distinct_citations, cfg.max_seconds) == (
        3,
        10,
        7,
        12.0,
    )
    for key in (
        "sweep_max_concurrent",
        "sweep_max_papers",
        "sweep_max_passages",
        "sweep_max_chars",
        "sweep_max_distinct_citations",
        "sweep_max_rows",
        "sweep_max_bytes",
    ):
        for bad in (0, -1, "2", 2.5, True, None, 10**12):
            assert parse_sweep_config({FLAG: True, key: bad}) is None, (key, bad)
    for bad in (0, -1, "5", True, None, float("inf"), float("nan"), 601):
        assert parse_sweep_config({FLAG: True, "sweep_max_seconds": bad}) is None, bad


def test_a_missing_or_broken_file_is_off(tmp_path: Path) -> None:
    assert load_sweep_config(tmp_path) is None
    (tmp_path / "configs").mkdir()
    (tmp_path / "configs" / "partner_api.yaml").write_text(f"{FLAG}: [unclosed", encoding="utf-8")
    assert load_sweep_config(tmp_path) is None


def test_the_shipped_config_has_the_sweep_off() -> None:
    assert load_sweep_config(REPO) is None
