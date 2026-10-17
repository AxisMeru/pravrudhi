# ruff: noqa: E501
"""The switch of the matter citation sweep (S1, flag `wf_s1_matter_citation_sweep`) and its caps.

Tiny and free of heavy imports, like `agenda_config`. With #986's flag peek the router imports this reader ONLY for a flag that reads true,
so a flag-off process imports nothing of S1. The sweep itself (`matter_sweep`, `matter_sweep_matter`) and A1's `draft_check` are imported
only when this returns a config.

Fail closed: the sweep is on ONLY when `partner_api.yaml` holds the key with the boolean `true`. A missing file, a missing key, the string
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

FLAG = "wf_s1_matter_citation_sweep"
_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SweepConfig:
    max_concurrent: int = 2  # sweeps in flight (its own slots: it never takes an existing route's)
    max_papers: int = 200  # papers searched in one sweep; the rest are reported "not searched", never dropped
    max_passages: int = 50_000  # passages scanned in one sweep (as A3)
    max_chars: int = 5_000_000  # characters scanned in one sweep (as A3)
    max_distinct_citations: int = 500  # distinct checkable citations in a matter; ABOVE it the sweep refuses before any lookup
    max_seconds: float = 60.0  # wall-clock seconds for the whole sweep
    max_rows: int = 5_000  # listed rows in one response; the counts stay exact beyond it
    max_bytes: int = 4_000_000  # bytes of one response; over it rows are dropped from the end and the flag is set


_CAPS = {
    "sweep_max_concurrent": ("max_concurrent", 1, 32),
    "sweep_max_papers": ("max_papers", 1, 5_000),
    "sweep_max_passages": ("max_passages", 1, 1_000_000),
    "sweep_max_chars": ("max_chars", 1_000, 100_000_000),
    "sweep_max_distinct_citations": ("max_distinct_citations", 1, 5_000),
    "sweep_max_rows": ("max_rows", 1, 100_000),
    "sweep_max_bytes": ("max_bytes", 10_000, 50_000_000),
}


def parse_sweep_config(body: Any) -> SweepConfig | None:
    """The config for a parsed `partner_api.yaml` body, or None (off). Pure."""
    if not isinstance(body, dict) or body.get(FLAG) is not True:
        return None
    values: dict[str, Any] = {}
    for key, (field, lo, hi) in _CAPS.items():
        if key not in body:
            continue
        raw = body[key]
        if isinstance(raw, bool) or not isinstance(raw, int) or not lo <= raw <= hi:
            _logger.warning("%s must be a whole number from %s to %s, got %r: the sweep stays OFF", key, lo, hi, raw)
            return None
        values[field] = raw
    if "sweep_max_seconds" in body:
        raw = body["sweep_max_seconds"]
        if isinstance(raw, bool) or not isinstance(raw, int | float) or not math.isfinite(raw) or not 1 <= raw <= 600:
            _logger.warning("sweep_max_seconds must be 1 to 600, got %r: the sweep stays OFF", raw)
            return None
        values["max_seconds"] = float(raw)
    return SweepConfig(**values)


def load_sweep_config(root: Path) -> SweepConfig | None:
    """Read `partner_api.yaml` (the same file and lookup order the partner config uses). Never raises: any failure is OFF."""
    try:
        body = yaml.safe_load(config_file(Path(root), "partner_api.yaml").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - a missing or unreadable file means the optional route is simply not there
        return None
    return parse_sweep_config(body)
