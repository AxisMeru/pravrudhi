# ruff: noqa: E501
"""A4 bail drafting switch and caps: fail closed, like every other slice."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from pravrudhi.application import bail_draft_config as C


@pytest.mark.parametrize("body", [None, {}, [], "wf_a4_bail_drafting", {C.FLAG: False}, {C.FLAG: "true"}, {C.FLAG: 1}, {C.FLAG: None}, {C.FLAG: [True]}])
def test_only_the_boolean_true_turns_it_on(body: Any) -> None:
    assert C.parse_bail_draft_config(body) is None


def test_the_flag_alone_gives_the_defaults_and_no_drafting_endpoint() -> None:
    cfg = C.parse_bail_draft_config({C.FLAG: True})
    assert cfg is not None and cfg == C.BailDraftConfig() and cfg.drafting_configured is False
    assert cfg.max_tokens_per_call >= 2500 and cfg.max_calls_per_matter == 2 and cfg.usd_per_matter == 0.6


def test_the_endpoint_is_configured_only_when_all_five_values_are_present() -> None:
    full = {C.FLAG: True, "a4_base_url": "https://x.example.test/v1", "a4_model": "m-1", "a4_api_key_env": "A4_KEY", "a4_usd_per_mtok_in": 2, "a4_usd_per_mtok_out": 10}
    cfg = C.parse_bail_draft_config(full)
    assert cfg is not None and cfg.drafting_configured and cfg.usd_per_mtok_in == 2.0 and cfg.usd_per_mtok_out == 10.0
    for k in ("a4_base_url", "a4_model", "a4_api_key_env", "a4_usd_per_mtok_in", "a4_usd_per_mtok_out"):
        part = {a: b for a, b in full.items() if a != k}
        c2 = C.parse_bail_draft_config(part)
        assert c2 is not None and c2.drafting_configured is False


@pytest.mark.parametrize(
    "bad",
    [
        {"a4_max_concurrent": 0}, {"a4_max_concurrent": True}, {"a4_max_concurrent": 1.5}, {"a4_max_calls_per_matter": 4}, {"a4_max_tokens_per_call": 99},
        {"a4_max_input_tokens_per_call": "6000"}, {"a4_max_facts": 0}, {"a4_usd_per_matter": -1}, {"a4_usd_per_matter": float("nan")}, {"a4_usd_per_day": True},
        {"a4_timeout_s": 0}, {"a4_usd_per_mtok_in": "2"}, {"a4_base_url": ""}, {"a4_model": 5}, {"a4_api_key_env": "x" * 301},
    ],
)
def test_a_malformed_value_with_the_flag_on_turns_it_off(bad: dict[str, Any]) -> None:
    assert C.parse_bail_draft_config({C.FLAG: True, **bad}) is None


def test_the_loader_never_raises_and_reads_the_partner_file(tmp_path: Path) -> None:
    assert C.load_bail_draft_config(tmp_path) is None  # no file
    (tmp_path / "configs").mkdir()
    f = tmp_path / "configs" / "partner_api.yaml"
    f.write_text("wf_a4_bail_drafting: [unclosed", encoding="utf-8")
    assert C.load_bail_draft_config(tmp_path) is None
    f.write_text("wf_a4_bail_drafting: true\na4_usd_per_matter: 0.25\n", encoding="utf-8")
    cfg = C.load_bail_draft_config(tmp_path)
    assert cfg is not None and cfg.usd_per_matter == 0.25
