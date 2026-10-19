# ruff: noqa: E501
"""A8 synopsis and list of dates switch: fail closed; the drafting keys are the SAME a4_* keys bail drafting reads."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from pravrudhi.application import bail_draft_config as B
from pravrudhi.application import synopsis_dates_config as S


@pytest.mark.parametrize("body", [None, {}, [], "x", {S.FLAG: False}, {S.FLAG: "true"}, {S.FLAG: 1}, {S.FLAG: None}, {S.FLAG: [True]}, {B.FLAG: True}])
def test_only_this_slices_own_flag_as_the_boolean_true_turns_it_on(body: Any) -> None:
    assert S.parse_synopsis_dates_config(body) is None  # bail drafting's flag alone does not switch the synopsis on


def test_the_flag_alone_gives_bails_defaults_and_the_same_keys_are_read() -> None:
    assert S.parse_synopsis_dates_config({S.FLAG: True}) == B.BailDraftConfig()
    body = {S.FLAG: True, "a4_usd_per_day": 1.5, "a4_base_url": "https://x.example.test/v1", "a4_model": "m", "a4_api_key_env": "K", "a4_usd_per_mtok_in": 2, "a4_usd_per_mtok_out": 10}
    cfg = S.parse_synopsis_dates_config(body)
    assert cfg is not None and cfg.usd_per_day == 1.5 and cfg.drafting_configured and cfg == B.parse_drafting_fields(body, S.FLAG)
    assert B.parse_bail_draft_config({**body, S.FLAG: False, B.FLAG: True}) == cfg  # bail drafting reads the very same block


@pytest.mark.parametrize("bad", [{"a4_max_calls_per_matter": 4}, {"a4_usd_per_matter": float("nan")}, {"a4_base_url": ""}, {"a4_usd_per_day": True}])
def test_a_malformed_drafting_value_with_the_flag_on_turns_it_off(bad: dict[str, Any]) -> None:
    assert S.parse_synopsis_dates_config({S.FLAG: True, **bad}) is None


def test_the_loader_never_raises(tmp_path: Path) -> None:
    assert S.load_synopsis_dates_config(tmp_path) is None
    (tmp_path / "configs").mkdir()
    f = tmp_path / "configs" / "partner_api.yaml"
    f.write_text("wf_a8_synopsis_list_of_dates: [unclosed", encoding="utf-8")
    assert S.load_synopsis_dates_config(tmp_path) is None
    f.write_text("wf_a8_synopsis_list_of_dates: true\n", encoding="utf-8")
    assert S.load_synopsis_dates_config(tmp_path) == B.BailDraftConfig()
