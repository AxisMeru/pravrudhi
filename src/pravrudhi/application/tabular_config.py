# ruff: noqa: E501
"""The switch of the matter tabular table (S2, flag `wf_s2_tabular_fact_table`) and its caps.

Tiny and free of heavy imports, like `agenda_config`. With #986's flag peek the router imports this reader ONLY for a flag that reads true,
so a flag-off process imports nothing of S1. The table itself (`tabular`, `tabular_sections`, `tabular_matter`) and A3's `chronology_dates` are imported
only when this returns a config.

Fail closed: the table is on ONLY when `partner_api.yaml` holds the key with the boolean `true`. A missing file, a missing key, the string
"true", 1 or anything else is OFF; a malformed cap with the flag on turns it OFF (and logs why).
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from pravrudhi.application.config_files import config_file

FLAG = "wf_s2_tabular_fact_table"
_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TabularConfig:
    max_concurrent: int = 2  # tables in flight (its own slots: it never takes an existing route's)
    max_papers: int = 200  # more than this REFUSES the whole request, naming the cap
    max_passages: int = 50_000  # as A3, but a refusal here, not "not searched"
    max_chars: int = 5_000_000
    max_cells_per_paper: int = 2_000  # over it REFUSES that paper's row only
    max_cells_per_matter: int = 20_000  # over it REFUSES the whole request
    max_response_bytes: int = 4_000_000  # the serialised response; over it REFUSES the whole request
    max_seconds: float = 30.0  # over it REFUSES the whole request with one fixed text, never a partial table


_CAPS = {
    "tab_max_concurrent": ("max_concurrent", 1, 32),
    "tab_max_papers": ("max_papers", 1, 5_000),
    "tab_max_passages": ("max_passages", 1, 1_000_000),
    "tab_max_chars": ("max_chars", 1_000, 100_000_000),
    "tab_max_cells_per_paper": ("max_cells_per_paper", 1, 100_000),
    "tab_max_cells_per_matter": ("max_cells_per_matter", 1, 1_000_000),
    "tab_max_response_bytes": ("max_response_bytes", 10_000, 50_000_000),
}


def parse_tabular_config(body: Any) -> TabularConfig | None:
    """The config for a parsed `partner_api.yaml` body, or None (off). Pure."""
    if not isinstance(body, dict) or body.get(FLAG) is not True:
        return None
    values: dict[str, Any] = {}
    for key, (field, lo, hi) in _CAPS.items():
        if key not in body:
            continue
        raw = body[key]
        if isinstance(raw, bool) or not isinstance(raw, int) or not lo <= raw <= hi:
            _logger.warning("%s must be a whole number from %s to %s, got %r: the table stays OFF", key, lo, hi, raw)
            return None
        values[field] = raw
    if "tab_max_seconds" in body:
        raw = body["tab_max_seconds"]
        if isinstance(raw, bool) or not isinstance(raw, int | float) or not math.isfinite(raw) or not 1 <= raw <= 600:
            _logger.warning("tab_max_seconds must be 1 to 600, got %r: the table stays OFF", raw)
            return None
        values["max_seconds"] = float(raw)
    return TabularConfig(**values)


def load_tabular_config(root: Path) -> TabularConfig | None:
    """Read `partner_api.yaml` (the same file and lookup order the partner config uses). Never raises: any failure is OFF."""
    try:
        body = yaml.safe_load(config_file(Path(root), "partner_api.yaml").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - a missing or unreadable file means the optional route is simply not there
        return None
    return parse_tabular_config(body)
