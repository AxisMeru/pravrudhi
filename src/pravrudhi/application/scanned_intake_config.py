"""#1015: the switch and the numbers of scanned/garbled-page intake (flag `wf_c1_scanned_intake`), read once from
partner_api.yaml.

Flag-off (the packaged default) imports nothing of the slice. With the flag on, EVERY number below must be present and valid
or the slice stays OFF (no hard-coded floor: Track A's calibration record sets them, in config only). Never raises.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from pravrudhi.application.config_files import config_file

FLAG = "wf_c1_scanned_intake"
_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ScannedIntakeConfig:
    min_page_confidence: float  # mean word confidence below this: the page is unreadable
    min_ink_coverage: float  # share of dark pixels inside recognised word boxes below this: unreadable
    max_low_word_share: float  # share of words under `low_word_confidence` above this: unreadable
    low_word_confidence: float
    garbled_min_chars: int  # a text-layer page with fewer non-space characters is not tested for garble
    garbled_max_odd_token_share: float  # odd-token share at or above this flags the page
    garbled_min_letter_share: float  # letter share below this flags the page
    agreement_min_similarity: float  # OCR text must differ from the layer by more than this (1 - similarity) to replace it
    max_pages: int  # pages OCR'd per document
    page_timeout_s: float
    document_deadline_s: float  # all OCR pages of ONE document together (one request holds a shared A2 slot)
    dpi: int
    tesseract_version: str = ""  # the calibrated engine: an image whose tesseract differs is NOT used (fail closed)
    eng_traineddata_sha256: str = ""
    languages: str = "eng"


#: key -> (field, kind, lo, hi); all are REQUIRED when the flag is on.
_KEYS: dict[str, tuple[str, type, float, float]] = {
    "ocr_min_page_confidence": ("min_page_confidence", float, 0.0, 100.0),
    "ocr_min_ink_coverage": ("min_ink_coverage", float, 0.0, 1.0),
    "ocr_max_low_word_share": ("max_low_word_share", float, 0.0, 1.0),
    "ocr_low_word_confidence": ("low_word_confidence", float, 0.0, 100.0),
    "ocr_garbled_min_chars": ("garbled_min_chars", int, 1, 100_000),
    "ocr_garbled_max_odd_token_share": ("garbled_max_odd_token_share", float, 0.0, 1.0),
    "ocr_garbled_min_letter_share": ("garbled_min_letter_share", float, 0.0, 1.0),
    "ocr_agreement_min_similarity": ("agreement_min_similarity", float, 0.0, 1.0),
    "ocr_max_pages": ("max_pages", int, 1, 400),
    "ocr_page_timeout_s": ("page_timeout_s", float, 1.0, 300.0),
    "ocr_document_deadline_s": ("document_deadline_s", float, 1.0, 900.0),
    "ocr_dpi": ("dpi", int, 72, 600),
}


def parse_scanned_intake_config(body: Any) -> ScannedIntakeConfig | None:
    """The config for a parsed `partner_api.yaml` body, or None (off). Pure. Only the boolean true turns the slice on, and then
    every key must be present, a real number (not a bool or string) and inside its range; one bad value keeps it OFF."""
    if not isinstance(body, dict) or body.get(FLAG) is not True:
        return None
    values: dict[str, Any] = {}
    for key, (field, kind, lo, hi) in _KEYS.items():
        raw: Any = body.get(key)
        ok = not isinstance(raw, bool) and (isinstance(raw, int) if kind is int else isinstance(raw, (int, float)))
        if not ok or not lo <= raw <= hi:
            _logger.warning("%s must be a number from %s to %s (and is required): scanned intake stays OFF", key, lo, hi)
            return None
        values[field] = kind(raw)
    for key, field in (("ocr_tesseract_version", "tesseract_version"), ("ocr_eng_traineddata_sha256", "eng_traineddata_sha256")):
        raw = body.get(key)
        bad_hash = field == "eng_traineddata_sha256" and not re.fullmatch(r"[0-9a-f]{64}", str(raw))
        if not isinstance(raw, str) or not raw.strip() or bad_hash:
            _logger.warning("%s must be a non-empty string (the sha256 as 64 lowercase hex): scanned intake stays OFF", key)
            return None
        values[field] = raw.strip()
    langs = body.get("ocr_languages", "eng")
    if langs != "eng":  # English only until Hindi ships: any other value is a typo that must not silently change the engine
        _logger.warning("ocr_languages must be \"eng\": scanned intake stays OFF")
        return None
    return ScannedIntakeConfig(**values)


def load_scanned_intake_config(root: Path) -> ScannedIntakeConfig | None:
    """Never raises: any failure is OFF."""
    try:
        body = yaml.safe_load(config_file(Path(root), "partner_api.yaml").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - a missing or unreadable file means the optional feature is simply not there
        return None
    return parse_scanned_intake_config(body)
