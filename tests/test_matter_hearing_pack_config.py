# ruff: noqa: E501
"""A7 matter hearing pack switch: fail closed, like every other slice."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from pravrudhi.application import matter_hearing_pack_config as C


@pytest.mark.parametrize("body", [None, {}, [], "x", {C.FLAG: False}, {C.FLAG: "true"}, {C.FLAG: 1}, {C.FLAG: None}, {C.FLAG: [True]}])
def test_only_the_boolean_true_turns_it_on(body: Any) -> None:
    assert C.parse_matter_hearing_pack_config(body) is None


def test_the_flag_alone_gives_the_default_and_a_valid_cap_is_taken() -> None:
    assert C.parse_matter_hearing_pack_config({C.FLAG: True}) == C.MatterHearingPackConfig()
    assert C.parse_matter_hearing_pack_config({C.FLAG: True, "hearing_pack_max_concurrent": 5}) == C.MatterHearingPackConfig(max_concurrent=5)


@pytest.mark.parametrize("bad", [0, 33, True, 1.5, "2", None])
def test_a_malformed_cap_with_the_flag_on_turns_it_off(bad: Any) -> None:
    assert C.parse_matter_hearing_pack_config({C.FLAG: True, "hearing_pack_max_concurrent": bad}) is None


def test_the_loader_never_raises(tmp_path: Path) -> None:
    assert C.load_matter_hearing_pack_config(tmp_path) is None
    (tmp_path / "configs").mkdir()
    f = tmp_path / "configs" / "partner_api.yaml"
    f.write_text("wf_a7_matter_hearing_pack: [unclosed", encoding="utf-8")
    assert C.load_matter_hearing_pack_config(tmp_path) is None
    f.write_text("wf_a7_matter_hearing_pack: true\n", encoding="utf-8")
    assert C.load_matter_hearing_pack_config(tmp_path) == C.MatterHearingPackConfig()
