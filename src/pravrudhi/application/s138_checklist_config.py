"""The switch of the s.138 cheque checklist (B3, flag `wf_b3_s138_checklist`) and its caps.

Tiny and free of heavy imports, like `draft_check_config`: `partner.build_partner_router` imports it (inside a guard) on every
start. The checklist itself (`s138_checklist`) is imported ONLY when this returns a config.

Fail closed: the checklist is on ONLY when `partner_api.yaml` holds the key with the boolean `true`. A missing file, a missing
key, the string "true", `yes`, 1 or anything else is OFF; a malformed cap with the flag on turns it OFF (and logs why).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from pravrudhi.application.config_files import config_file

FLAG = "wf_b3_s138_checklist"
_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class S138ChecklistConfig:
    max_concurrent: int = 4  # requests in flight (its own slots: it never takes an existing route's)
    max_dates_per_role: int = 20  # candidate dates listed for one role (a longer list is refused, never cut)
    #: the earliest year a date may have. 1989 is the year s.138 was inserted into the Act: the held India Code item
    #: corpus-raw/india_code/ni_act_1881_api/s138_item.json (sha256
    #: e831f10cf167b4d3c866737d3f6d792595cf8b3fbcec94bbbaff423cc7345d61),
    #: section_footnote 1: "Ins. by Act 66 of 1988, s, 4 (w.e.f. 1-4-1989)"; also in the asset's provenance.footnotes. An earlier
    #: year is a typing error (Track B's low: a 1915 typo for 2015) and REFER. A config value: a lawyer may lower it
    min_year: int = 1989


_CAPS = {
    "s138_checklist_max_concurrent": ("max_concurrent", 1, 32),
    "s138_checklist_max_dates_per_role": ("max_dates_per_role", 1, 100),
    "s138_checklist_min_year": ("min_year", 1881, 2100),
}


def parse_s138_checklist_config(body: Any) -> S138ChecklistConfig | None:
    """The config for a parsed `partner_api.yaml` body, or None (off). Pure."""
    if not isinstance(body, dict) or body.get(FLAG) is not True:
        return None
    values: dict[str, Any] = {}
    for key, (field, lo, hi) in _CAPS.items():
        if key not in body:
            continue
        raw = body[key]
        if isinstance(raw, bool) or not isinstance(raw, int) or not lo <= raw <= hi:
            _logger.warning("%s must be a whole number from %d to %d, got %r: the s.138 checklist stays OFF", key, lo, hi, raw)
            return None
        values[field] = raw
    return S138ChecklistConfig(**values)


def load_s138_checklist_config(root: Path) -> S138ChecklistConfig | None:
    """Read `partner_api.yaml` (the same file and lookup order the partner config uses). Never raises: any failure is OFF."""
    try:
        body = yaml.safe_load(config_file(Path(root), "partner_api.yaml").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - a missing or unreadable file means the optional route is simply not there
        return None
    return parse_s138_checklist_config(body)
