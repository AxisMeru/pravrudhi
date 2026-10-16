"""The switch of the chronology (A3, flag `wf_a3_chronology`) and its caps.

Tiny and free of heavy imports, like `s138_checklist_config`: `partner.build_partner_router` imports it (inside a guard) on every
start. The chronology itself (`chronology`) is imported ONLY when this returns a config.

Fail closed: the chronology is on ONLY when `partner_api.yaml` holds the key with the boolean `true`. A missing file, a missing
key, the string "true", `yes`, 1 or anything else is OFF; a malformed cap with the flag on turns it OFF (and logs why).
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from pravrudhi.application.config_files import config_file

FLAG = "wf_a3_chronology"
_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ChronologyConfig:
    max_concurrent: int = 2  # requests in flight (its own slots: it never takes an existing route's)
    max_passages: int = 50_000  # passages scanned in one request; the rest are reported "not searched", never dropped
    max_chars: int = 5_000_000  # characters scanned in one request
    max_rows: int = 2_000  # rows SHOWN ("Showing N of M"); the denominator always counts every row found
    max_seconds: float = 20.0  # wall-clock seconds for one scan


_CAPS = {
    "chronology_max_concurrent": ("max_concurrent", 1, 32),
    "chronology_max_passages": ("max_passages", 1, 1_000_000),
    "chronology_max_chars": ("max_chars", 1_000, 100_000_000),
    "chronology_max_rows": ("max_rows", 1, 100_000),
}


def parse_chronology_config(body: Any) -> ChronologyConfig | None:
    """The config for a parsed `partner_api.yaml` body, or None (off). Pure."""
    if not isinstance(body, dict) or body.get(FLAG) is not True:
        return None
    values: dict[str, Any] = {}
    for key, (field, lo, hi) in _CAPS.items():
        if key not in body:
            continue
        raw = body[key]
        if isinstance(raw, bool) or not isinstance(raw, int) or not lo <= raw <= hi:
            _logger.warning("%s must be a whole number from %d to %d, got %r: the chronology stays OFF", key, lo, hi, raw)
            return None
        values[field] = raw
    if "chronology_max_seconds" in body:
        raw = body["chronology_max_seconds"]
        if isinstance(raw, bool) or not isinstance(raw, (int, float)) or not (math.isfinite(raw) and 1 <= raw <= 600):
            _logger.warning("chronology_max_seconds must be 1 to 600, got %r: the chronology stays OFF", raw)
            return None
        values["max_seconds"] = float(raw)
    return ChronologyConfig(**values)


def load_chronology_config(root: Path) -> ChronologyConfig | None:
    """Read `partner_api.yaml` (the same file and lookup order the partner config uses). Never raises: any failure is OFF."""
    try:
        body = yaml.safe_load(config_file(Path(root), "partner_api.yaml").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - a missing or unreadable file means the optional route is simply not there
        return None
    return parse_chronology_config(body)
