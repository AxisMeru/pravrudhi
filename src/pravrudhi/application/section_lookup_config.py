# ruff: noqa: E501
"""The switch of the old-section look-up (B1, flag `wf_b1_section_lookup`) and its one cap.

Tiny and free of heavy imports, like `quashing_config`: `partner.build_partner_router` imports it (inside a guard) on every start.
The look-up itself (`section_lookup`) is imported ONLY when this returns a config.

Fail closed: the look-up is on ONLY when `partner_api.yaml` holds the key with the boolean `true`. A missing file, a missing key, the
string "true", `yes`, 1 or anything else is OFF; a malformed cap with the flag on turns it OFF (and logs why).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from pravrudhi.application.config_files import config_file

FLAG = "wf_b1_section_lookup"
_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SectionLookupConfig:
    max_concurrent: int = 4  # requests in flight (its own slots: it never takes an existing route's)


def parse_section_lookup_config(body: Any) -> SectionLookupConfig | None:
    """The config for a parsed `partner_api.yaml` body, or None (off). Pure."""
    if not isinstance(body, dict) or body.get(FLAG) is not True:
        return None
    if "section_lookup_max_concurrent" in body:
        raw = body["section_lookup_max_concurrent"]
        if isinstance(raw, bool) or not isinstance(raw, int) or not 1 <= raw <= 32:
            _logger.warning(
                "section_lookup_max_concurrent must be a whole number from 1 to 32, got %r: the section look-up stays OFF", raw
            )
            return None
        return SectionLookupConfig(max_concurrent=raw)
    return SectionLookupConfig()


def load_section_lookup_config(root: Path) -> SectionLookupConfig | None:
    """Read `partner_api.yaml` (the same file and lookup order the partner config uses). Never raises: any failure is OFF."""
    try:
        body = yaml.safe_load(config_file(Path(root), "partner_api.yaml").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - a missing or unreadable file means the optional route is simply not there
        return None
    return parse_section_lookup_config(body)
