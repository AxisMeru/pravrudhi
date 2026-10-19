# ruff: noqa: E501
"""The switch of the cheque-case check (A6 slice 1, flag `wf_a6_cheque_case_check`) and its one cap.

Tiny and free of heavy imports, like `agenda_config`. With #986's flag peek the router imports this reader ONLY for a flag that reads true. The
check itself (`cheque_case_route`, Track A's `cheque_case`, and the A3, A5 and B3 modules it calls as functions) is imported only when this returns a
config. Those three slices' OWN flags are not required: only their modules are imported (lazily), as S1 does with A1.

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

FLAG = "wf_a6_cheque_case_check"
_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ChequeCaseConfig:
    max_concurrent: int = 2  # checks in flight (its own slots: it never takes an existing route's)


def parse_cheque_case_config(body: Any) -> ChequeCaseConfig | None:
    """The config for a parsed `partner_api.yaml` body, or None (off). Pure."""
    if not isinstance(body, dict) or body.get(FLAG) is not True:
        return None
    if "cheque_case_max_concurrent" in body:
        raw = body["cheque_case_max_concurrent"]
        if isinstance(raw, bool) or not isinstance(raw, int) or not 1 <= raw <= 32:
            _logger.warning("cheque_case_max_concurrent must be a whole number from 1 to 32, got %r: the cheque-case check stays OFF", raw)
            return None
        return ChequeCaseConfig(max_concurrent=raw)
    return ChequeCaseConfig()


def load_cheque_case_config(root: Path) -> ChequeCaseConfig | None:
    """Read `partner_api.yaml` (the same file and lookup order the partner config uses). Never raises: any failure is OFF."""
    try:
        body = yaml.safe_load(config_file(Path(root), "partner_api.yaml").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - a missing or unreadable file means the optional route is simply not there
        return None
    return parse_cheque_case_config(body)
