# ruff: noqa: E501
"""The switch of the synopsis and list of dates (A8, flag `wf_a8_synopsis_list_of_dates`).

Tiny and free of heavy imports. The drafting caps and the optional OpenAI-compatible endpoint are the SAME `a4_*` keys bail drafting reads (one
endpoint, one set of caps, one in-process day ceiling: `bail_draft_route.shared_ceiling()`); only the flag differs. With #986's flag peek the router
imports this reader ONLY for a flag that reads true. Fail closed: on ONLY when `partner_api.yaml` holds the key with the boolean `true`; a malformed
drafting value with the flag on turns it OFF (and logs why).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from pravrudhi.application.bail_draft_config import BailDraftConfig, parse_drafting_fields
from pravrudhi.application.config_files import config_file

FLAG = "wf_a8_synopsis_list_of_dates"


def parse_synopsis_dates_config(body: Any) -> BailDraftConfig | None:
    """The drafting config for a parsed `partner_api.yaml` body, or None (off). Pure."""
    return parse_drafting_fields(body, FLAG, "the synopsis and list of dates")


def load_synopsis_dates_config(root: Path) -> BailDraftConfig | None:
    """Read `partner_api.yaml` (the same file and lookup order the partner config uses). Never raises: any failure is OFF."""
    try:
        body = yaml.safe_load(config_file(Path(root), "partner_api.yaml").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - a missing or unreadable file means the optional route is simply not there
        return None
    return parse_synopsis_dates_config(body)
