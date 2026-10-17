# ruff: noqa: E501
"""Test seam for #986: the optional slices' route tests switch a slice on by patching its config loader; since the router reads the
flags ONCE through `partner._peek_flags` (so a flag-off process imports no reader module), those tests also make the peek say
"true" for every slice, and the patched loader then decides (None = off, a config = on)."""

from __future__ import annotations

import pytest

from pravrudhi.api import partner

ALL_FLAG_KEYS = (
    "wf_a1_draft_citation_check",
    "wf_a2_span_index",
    "wf_a3_chronology",
    "wf_a5_ingredient_agenda",
    "wf_b2_quashing_agenda",
    "wf_s1_matter_citation_sweep",
    "wf_s2_tabular_fact_table",
    "wf_b3_s138_checklist",
    "wf_a6_cheque_case_check",
)


def peek_all_true(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(partner, "_peek_flags", lambda _root: dict.fromkeys(ALL_FLAG_KEYS, True))
