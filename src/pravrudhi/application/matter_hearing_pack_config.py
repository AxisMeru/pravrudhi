# ruff: noqa: E501
"""The switch of the matter hearing pack (A7 slice 2, flag `wf_a7_matter_hearing_pack`) and its one cap.

Tiny and free of heavy imports, like `agenda_config`. With #986's flag peek the router imports this reader ONLY for a flag that reads true. The
pack itself (`matter_hearing_pack_route`, Track A's `matter_hearing_pack`, and the A3, A5 and S1 modules it calls as functions) is imported only
when this returns a config. Not to be confused with the #415 `hearing_pack/` package (a decide-result pack).

Fail closed: on ONLY when `partner_api.yaml` holds the key with the boolean `true`. A missing file, a missing key, the string "true", 1 or anything
else is OFF; a malformed cap with the flag on turns it OFF (and logs why).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from pravrudhi.application.config_files import config_file

FLAG = "wf_a7_matter_hearing_pack"
_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MatterHearingPackConfig:
    max_concurrent: int = 2  # packs in flight (its own slots: it never takes an existing route's)


def parse_matter_hearing_pack_config(body: Any) -> MatterHearingPackConfig | None:
    """The config for a parsed `partner_api.yaml` body, or None (off). Pure."""
    if not isinstance(body, dict) or body.get(FLAG) is not True:
        return None
    if "hearing_pack_max_concurrent" in body:
        raw = body["hearing_pack_max_concurrent"]
        if isinstance(raw, bool) or not isinstance(raw, int) or not 1 <= raw <= 32:
            _logger.warning("hearing_pack_max_concurrent must be a whole number from 1 to 32, got %r: the hearing pack stays OFF", raw)
            return None
        return MatterHearingPackConfig(max_concurrent=raw)
    return MatterHearingPackConfig()


def load_matter_hearing_pack_config(root: Path) -> MatterHearingPackConfig | None:
    """Read `partner_api.yaml` (the same file and lookup order the partner config uses). Never raises: any failure is OFF."""
    try:
        body = yaml.safe_load(config_file(Path(root), "partner_api.yaml").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - a missing or unreadable file means the optional route is simply not there
        return None
    return parse_matter_hearing_pack_config(body)
