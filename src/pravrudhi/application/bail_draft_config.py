# ruff: noqa: E501
"""The switch of the bail-drafting slice (A4, flag `wf_a4_bail_drafting`), its caps and its optional drafting endpoint.

Tiny and free of heavy imports, like `agenda_config`: with #986's flag peek the router imports this reader ONLY for a flag that reads true,
so a flag-off process imports nothing of A4. The draft engine (`bail_draft`, `bail_draft_route`) is imported only when this returns a config.

Fail closed: the slice is on ONLY when `partner_api.yaml` holds the key with the boolean `true`. A missing file, a missing key, the string
"true", 1 or anything else is OFF; a malformed value with the flag on turns it OFF (and logs why).

Drafting endpoint (optional): `a4_base_url` + `a4_model` name an OpenAI-compatible chat endpoint and the pinned model id; `a4_api_key_env` names the
ENVIRONMENT VARIABLE holding its key (the key itself is never read from the yaml); `a4_usd_per_mtok_in` / `a4_usd_per_mtok_out` are the pinned prices
the cost is counted with. With any of the five absent the slice still runs, but every request answers the template only (no model is called).
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from pravrudhi.application.config_files import config_file

FLAG = "wf_a4_bail_drafting"
_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BailDraftConfig:
    max_concurrent: int = 2  # drafts in flight (its own slots: it never takes an existing route's)
    max_tokens_per_call: int = 3000  # output tokens allowed in one call (thinking is ON: the A4 run hit 1800 on 4 of 80 drafts)
    max_calls_per_matter: int = 2  # model calls for one draft: the first and ONE regeneration after a failed mechanical check
    max_input_tokens_per_call: int = 6000  # an over-size pack is refused before any call (a deliberately high ESTIMATE, labelled as one)
    usd_per_matter: float = 0.60  # worst case of every allowed call; over it, no call is made
    usd_per_day: float = 2.0  # counted spend for this process in one UTC day (in memory: a restart resets it); over it the draft is the template only
    max_facts: int = 40
    timeout_s: float = 120.0
    base_url: str | None = None
    model: str | None = None
    api_key_env: str | None = None
    usd_per_mtok_in: float | None = None
    usd_per_mtok_out: float | None = None

    @property
    def drafting_configured(self) -> bool:
        return None not in (self.base_url, self.model, self.api_key_env, self.usd_per_mtok_in, self.usd_per_mtok_out)


_INTS = {
    "a4_max_concurrent": ("max_concurrent", 1, 16),
    "a4_max_tokens_per_call": ("max_tokens_per_call", 100, 8000),
    "a4_max_calls_per_matter": ("max_calls_per_matter", 1, 3),
    "a4_max_input_tokens_per_call": ("max_input_tokens_per_call", 500, 100_000),
    "a4_max_facts": ("max_facts", 1, 200),
}
_FLOATS = {
    "a4_usd_per_matter": ("usd_per_matter", 0.0, 50.0),
    "a4_usd_per_day": ("usd_per_day", 0.0, 1000.0),
    "a4_timeout_s": ("timeout_s", 1.0, 600.0),
    "a4_usd_per_mtok_in": ("usd_per_mtok_in", 0.0, 1000.0),
    "a4_usd_per_mtok_out": ("usd_per_mtok_out", 0.0, 1000.0),
}
_STRS = {"a4_base_url": "base_url", "a4_model": "model", "a4_api_key_env": "api_key_env"}


def parse_drafting_fields(body: Any, flag: str, label: str = "bail drafting") -> BailDraftConfig | None:
    """The drafting config (caps and the optional endpoint) for a parsed `partner_api.yaml` body and the flag that switches a drafting slice on,
    or None (off). Pure. Every drafting slice (bail drafting, the synopsis and list of dates) reads the SAME `a4_*` keys: one endpoint, one set
    of caps, one in-process day ceiling."""
    if not isinstance(body, dict) or body.get(flag) is not True:
        return None
    values: dict[str, Any] = {}
    for key, (field, lo, hi) in _INTS.items():
        if key not in body:
            continue
        raw = body[key]
        if isinstance(raw, bool) or not isinstance(raw, int) or not lo <= raw <= hi:
            _logger.warning("%s must be a whole number from %s to %s, got %r: %s stays OFF", key, lo, hi, raw, label)
            return None
        values[field] = raw
    for key, (ffield, flo, fhi) in _FLOATS.items():
        if key not in body:
            continue
        raw = body[key]
        if isinstance(raw, bool) or not isinstance(raw, int | float) or not math.isfinite(raw) or not flo <= raw <= fhi:
            _logger.warning("%s must be a number from %s to %s, got %r: %s stays OFF", key, flo, fhi, raw, label)
            return None
        values[ffield] = float(raw)
    for key, sfield in _STRS.items():
        if key not in body:
            continue
        raw = body[key]
        if not isinstance(raw, str) or not raw.strip() or len(raw) > 300:
            _logger.warning("%s must be a non-empty string of at most 300 characters: %s stays OFF", key, label)
            return None
        values[sfield] = raw.strip()
    return BailDraftConfig(**values)


def parse_bail_draft_config(body: Any) -> BailDraftConfig | None:
    """The config for a parsed `partner_api.yaml` body, or None (off). Pure."""
    return parse_drafting_fields(body, FLAG)


def load_bail_draft_config(root: Path) -> BailDraftConfig | None:
    """Read `partner_api.yaml` (the same file and lookup order the partner config uses). Never raises: any failure is OFF."""
    try:
        body = yaml.safe_load(config_file(Path(root), "partner_api.yaml").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - a missing or unreadable file means the optional route is simply not there
        return None
    return parse_bail_draft_config(body)
