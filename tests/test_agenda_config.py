"""The A5 flag: fail closed (only the boolean true is on), a malformed cap with the flag on turns it OFF."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from pravrudhi.application.agenda_config import AgendaConfig, load_agenda_config, parse_agenda_config

REPO = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize("body", [None, {}, [], "wf_a5_ingredient_agenda", {"wf_a5_ingredient_agenda": False}])
def test_anything_but_the_key_with_true_is_off(body: Any) -> None:
    assert parse_agenda_config(body) is None


@pytest.mark.parametrize("value", ["true", "yes", 1, "on", None, 0, "false"])
def test_a_value_that_is_not_the_boolean_true_is_off(value: Any) -> None:
    assert parse_agenda_config({"wf_a5_ingredient_agenda": value}) is None


def test_true_is_on_with_the_default_cap() -> None:
    assert parse_agenda_config({"wf_a5_ingredient_agenda": True}) == AgendaConfig(max_concurrent=2)


def test_a_good_cap_is_read_and_a_malformed_one_turns_the_agenda_off() -> None:
    assert parse_agenda_config({"wf_a5_ingredient_agenda": True, "agenda_max_concurrent": 5}) == AgendaConfig(max_concurrent=5)
    for bad in (0, 33, -1, "2", 2.5, True, None):
        assert parse_agenda_config({"wf_a5_ingredient_agenda": True, "agenda_max_concurrent": bad}) is None, bad


def test_a_missing_or_broken_file_is_off(tmp_path: Path) -> None:
    assert load_agenda_config(tmp_path) is None
    (tmp_path / "configs").mkdir()
    (tmp_path / "configs" / "partner_api.yaml").write_text("wf_a5_ingredient_agenda: [unclosed", encoding="utf-8")
    assert load_agenda_config(tmp_path) is None


def test_the_shipped_config_has_the_agenda_off() -> None:
    assert load_agenda_config(REPO) is None
