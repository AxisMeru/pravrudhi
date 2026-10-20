"""The switch of the matter span-index (A2, flag `wf_a2_span_index`), its caps and its store directory.

Tiny and free of heavy imports, like `draft_check_config`: `partner.build_partner_router` imports it (inside a guard) on every
start. The store module (`matter_spans`) is imported only when the flag is on, or when a store directory already exists (the
delete path must outlive the flag: data stored while the flag was on must stay deletable after it is turned off).

Fail closed: the index is on ONLY when `partner_api.yaml` holds the key with the boolean `true`. A missing file, a missing key,
the string "true", `yes`, 1 or anything else is OFF; a malformed cap with the flag on turns it OFF (and logs why).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from pravrudhi.application.config_files import config_file

FLAG = "wf_a2_span_index"
DIR_ENV = "PRAVRUDHI_MATTER_SPANS_DIR"
DEFAULT_DIR_NAME = "matter_spans"
_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MatterSpansConfig:
    max_docs: int = 200  # documents in one matter
    max_chars: int = 2_000_000  # characters of one document
    max_total_chars: int = 20_000_000  # characters in one matter
    max_pdf_bytes: int = 8_000_000
    max_pdf_pages: int = 400
    max_results: int = 10  # spans returned by one query
    max_concurrent: int = 4  # requests in flight (its own slots)
    max_matters_per_tenant: int = 200
    max_tenant_bytes: int = 5_000_000_000  # everything one tenant holds on disk (bytes)
    max_spans_per_doc: int = 100_000
    max_list_matters: int = 50  # matters one my-matters listing opens and returns (the rest are reported as truncated)
    list_budget_s: int = 30  # seconds one listing may spend opening matters; kept well under the ~100 s edge limit
    min_free_bytes: int = 500_000_000  # an ingest is refused (503) if it would leave less free space than this


_CAPS = {
    "matter_spans_max_docs": ("max_docs", 1, 5_000),
    "matter_spans_max_chars": ("max_chars", 1_000, 10_000_000),
    "matter_spans_max_total_chars": ("max_total_chars", 1_000, 200_000_000),
    "matter_spans_max_pdf_bytes": ("max_pdf_bytes", 1_000, 8_000_000),
    "matter_spans_max_pdf_pages": ("max_pdf_pages", 1, 5_000),
    "matter_spans_max_results": ("max_results", 1, 50),
    "matter_spans_max_concurrent": ("max_concurrent", 1, 32),
    "matter_spans_max_matters_per_tenant": ("max_matters_per_tenant", 1, 10_000),
    "matter_spans_max_tenant_bytes": ("max_tenant_bytes", 1_000_000, 10_000_000_000_000),
    "matter_spans_max_spans_per_doc": ("max_spans_per_doc", 1, 1_000_000),
    "matter_spans_max_list_matters": ("max_list_matters", 1, 1_000),
    "matter_spans_list_budget_s": ("list_budget_s", 1, 90),
    "matter_spans_min_free_bytes": ("min_free_bytes", 0, 10_000_000_000_000),
}


def parse_matter_spans_config(body: Any) -> MatterSpansConfig | None:
    """The config for a parsed `partner_api.yaml` body, or None (off). Pure."""
    if not isinstance(body, dict) or body.get(FLAG) is not True:
        return None
    values: dict[str, Any] = {}
    for key, (field, lo, hi) in _CAPS.items():
        if key not in body:
            continue
        raw = body[key]
        if isinstance(raw, bool) or not isinstance(raw, int) or not lo <= raw <= hi:
            _logger.warning("%s must be a whole number from %d to %d, got %r: the matter span-index stays OFF", key, lo, hi, raw)
            return None
        values[field] = raw
    return MatterSpansConfig(**values)


def _body(root: Path) -> Any:
    try:
        return yaml.safe_load(config_file(Path(root), "partner_api.yaml").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - a missing or unreadable file means the optional feature is simply not there
        return None


def load_matter_spans_config(root: Path) -> MatterSpansConfig | None:
    """Never raises: any failure is OFF."""
    return parse_matter_spans_config(_body(root))


def store_dir(root: Path) -> Path:
    """Where matters are stored: the environment variable, else the yaml key `matter_spans_dir`, else `<root>/matter_spans`.
    Computing it creates nothing."""
    env = os.environ.get(DIR_ENV, "").strip()
    if env:
        return Path(env).expanduser()
    body = _body(root)
    raw = body.get("matter_spans_dir") if isinstance(body, dict) else None
    if isinstance(raw, str) and raw.strip():
        return Path(raw).expanduser()
    return Path(root) / DEFAULT_DIR_NAME
