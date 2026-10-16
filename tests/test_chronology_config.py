"""A3 chronology flag (wf_a3_chronology): fail closed, caps validated, the shipped config is off."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from pravrudhi.application import chronology_config as cc
from pravrudhi.application.chronology_config import ChronologyConfig, load_chronology_config, parse_chronology_config

REPO = Path(__file__).resolve().parent.parent


def test_only_the_boolean_true_turns_the_chronology_on() -> None:
    assert parse_chronology_config({"wf_a3_chronology": True}) == ChronologyConfig()
    for flag in (False, None, "true", "yes", "on", 1, 0, [], {}):
        assert parse_chronology_config({"wf_a3_chronology": flag}) is None
    assert parse_chronology_config({}) is None and parse_chronology_config(None) is None and parse_chronology_config("x") is None


@pytest.mark.parametrize(
    "key", ["chronology_max_concurrent", "chronology_max_passages", "chronology_max_chars", "chronology_max_rows"]
)
@pytest.mark.parametrize("bad", [0, -1, "3", 2.5, True, None, 10**12])
def test_a_malformed_cap_with_the_flag_on_turns_it_off(key: str, bad: Any) -> None:
    assert parse_chronology_config({"wf_a3_chronology": True, key: bad}) is None


@pytest.mark.parametrize("bad", [0, 0.5, 601, True, "9", None, float("nan"), float("inf")])
def test_a_malformed_time_cap_turns_it_off(bad: Any) -> None:
    assert parse_chronology_config({"wf_a3_chronology": True, "chronology_max_seconds": bad}) is None


def test_valid_caps_are_applied() -> None:
    cfg = parse_chronology_config(
        {"wf_a3_chronology": True, "chronology_max_rows": 7, "chronology_max_seconds": 5, "chronology_max_concurrent": 3}
    )
    assert cfg is not None and (cfg.max_rows, cfg.max_seconds, cfg.max_concurrent) == (7, 5.0, 3)


def test_the_shipped_configuration_is_off_and_an_unreadable_file_is_off(tmp_path: Path) -> None:
    assert load_chronology_config(REPO) is None
    text = (REPO / "configs" / "partner_api.yaml").read_text()
    assert "wf_a3_chronology" not in text or "wf_a3_chronology: false" in text
    assert load_chronology_config(tmp_path / "nowhere") is None
    (tmp_path / "configs").mkdir()
    (tmp_path / "configs" / "partner_api.yaml").write_text("wf_a3_chronology: [unclosed")
    assert load_chronology_config(tmp_path) is None


def test_the_module_is_tiny_and_imports_no_heavy_library() -> None:
    src = Path(cc.__file__).read_text()
    for banned in (
        "import pypdf",
        "import httpx",
        "import requests",
        "sqlite3",
        "chronology_dates",
        "from pravrudhi.application.chronology import",
    ):
        assert banned not in src
