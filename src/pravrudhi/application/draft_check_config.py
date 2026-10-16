"""The switch of the whole-draft citation check (A1, flag `wf_a1_draft_citation_check`) and its caps.

Deliberately tiny and free of heavy imports: `partner.build_partner_router` imports it (inside a guard) on every start, so
this module may not touch the index, a model or the network. The check itself (`draft_check`) is imported ONLY when this
returns a config.

Fail closed: the check is on ONLY when `partner_api.yaml` holds the key with the boolean `true`. A missing file, an
unreadable file, a missing key, the string "true", `yes`, `on`, 1 or anything else is OFF. A malformed cap, with the flag on,
also turns the check OFF (and logs why): a typo in an optional feature's cap must never stop the engine's own routes.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from pravrudhi.application.config_files import config_file

FLAG = "wf_a1_draft_citation_check"
_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DraftCheckConfig:
    #: distinct citations looked up in one draft; the rest are reported "not checked: cap", never dropped
    max_citations: int = 150
    #: characters of text (a PDF's extracted text is held to the same cap)
    max_chars: int = 200_000
    #: wall-clock seconds for the lookups of one draft
    max_seconds: float = 30.0
    #: decoded size of an uploaded PDF
    max_pdf_bytes: int = 8_000_000
    #: pages read from a PDF
    max_pdf_pages: int = 400
    #: drafts checked at the same time (its own slots: it never takes an existing route's)
    max_concurrent: int = 2


_CAPS = {
    "draft_check_max_citations": ("max_citations", 1, 5_000),
    "draft_check_max_chars": ("max_chars", 1_000, 1_000_000),
    "draft_check_max_pdf_bytes": ("max_pdf_bytes", 1_000, 50_000_000),
    "draft_check_max_pdf_pages": ("max_pdf_pages", 1, 5_000),
    "draft_check_max_concurrent": ("max_concurrent", 1, 32),
}


def parse_draft_check_config(body: Any) -> DraftCheckConfig | None:
    """The config for a parsed `partner_api.yaml` body, or None (off). Pure."""
    if not isinstance(body, dict) or body.get(FLAG) is not True:
        return None
    values: dict[str, Any] = {}
    for key, (field, lo, hi) in _CAPS.items():
        if key not in body:
            continue
        raw = body[key]
        if isinstance(raw, bool) or not isinstance(raw, int) or not lo <= raw <= hi:
            _logger.warning("%s must be a whole number from %d to %d, got %r: the draft check stays OFF", key, lo, hi, raw)
            return None
        values[field] = raw
    if "draft_check_max_seconds" in body:
        raw = body["draft_check_max_seconds"]
        ok = isinstance(raw, (int, float)) and not isinstance(raw, bool)
        if not (ok and math.isfinite(raw) and 1 <= raw <= 600):
            _logger.warning("draft_check_max_seconds must be 1 to 600, got %r: the draft check stays OFF", raw)
            return None
        values["max_seconds"] = float(raw)
    return DraftCheckConfig(**values)


def load_draft_check_config(root: Path) -> DraftCheckConfig | None:
    """Read `partner_api.yaml` (the same file and lookup order the partner config uses). Never raises: any failure is OFF."""
    try:
        body = yaml.safe_load(config_file(Path(root), "partner_api.yaml").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - a missing or unreadable file means the optional route is simply not there
        return None
    return parse_draft_check_config(body)
