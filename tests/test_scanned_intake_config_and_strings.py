"""#1015 PR 1: the config reader (both arms) and R1's customer lines pinned by hash. Invented values only."""

from __future__ import annotations

import hashlib

import pytest

from pravrudhi.application import scanned_intake_config as C
from pravrudhi.application import scanned_intake_strings as S

GOOD = {
    "wf_c1_scanned_intake": True, "ocr_min_page_confidence": 80, "ocr_min_ink_coverage": 0.90, "ocr_max_low_word_share": 0.10,
    "ocr_low_word_confidence": 50, "ocr_garbled_min_chars": 120, "ocr_garbled_max_odd_token_share": 0.15,
    "ocr_garbled_min_letter_share": 0.5, "ocr_agreement_min_similarity": 0.80, "ocr_max_pages": 60, "ocr_page_timeout_s": 30,
    "ocr_document_deadline_s": 120,
    "ocr_dpi": 300,
    "ocr_tesseract_version": "5.5.0",
    "ocr_eng_traineddata_sha256": "7d4322bd2a7749724879683fc3912cb542f19906c83bcc1a52132556427170b2",
}  # fmt: skip


def h(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()[:16]


def test_only_the_boolean_true_with_every_key_present_and_in_range_turns_it_on():
    cfg = C.parse_scanned_intake_config(GOOD)
    assert cfg is not None and cfg.min_page_confidence == 80.0 and cfg.max_pages == 60 and cfg.languages == "eng"
    for flag in (False, None, "true", 1, 0, "yes"):
        assert C.parse_scanned_intake_config({**GOOD, C.FLAG: flag}) is None
    assert C.parse_scanned_intake_config(None) is None and C.parse_scanned_intake_config({}) is None


_STRING_KEYS = ("ocr_tesseract_version", "ocr_eng_traineddata_sha256")


@pytest.mark.parametrize("key", [k for k in GOOD if k != "wf_c1_scanned_intake" and k not in _STRING_KEYS])
def test_a_missing_or_bad_value_for_any_key_keeps_it_off(key: str):
    missing = {k: v for k, v in GOOD.items() if k != key}
    assert C.parse_scanned_intake_config(missing) is None
    for bad in (True, "80", None, -1, 10**9):
        assert C.parse_scanned_intake_config({**GOOD, key: bad}) is None, (key, bad)


def test_languages_must_be_english_and_integers_must_be_integers():
    assert C.parse_scanned_intake_config({**GOOD, "ocr_languages": "hin"}) is None
    assert C.parse_scanned_intake_config({**GOOD, "ocr_languages": "eng"}) is not None
    assert C.parse_scanned_intake_config({**GOOD, "ocr_dpi": 300.5}) is None


def test_the_loader_never_raises_and_is_off_without_the_file(tmp_path):
    assert C.load_scanned_intake_config(tmp_path / "nowhere") is None


def test_every_customer_line_is_r1s_exactly():
    pins = {
        S.UNREADABLE_PAGE: "27d902d9a913ef3a", S.REASON_LINES["low_confidence"]: "c83d9723e60da83d",
        S.REASON_LINES["timeout"]: "4f3bc8352bdb2893", S.REASON_LINES["language_not_supported"]: "6ceb2553c4ccbae1",
        S.REASON_LINES["ocr_unavailable"]: "1b75b9fead542580", S.DOCUMENT_LINE: "e012b937c611ed63",
        S.OCR_LINE: "b2497ad790916492",
        S.FROM_OCR_DATE_LINE: "02dcab75a2b5c7d4", S.REPAIRED_PAGE_LINE: "6d58ad2bbb36f4d0", S.PHOTO_LINE: "b19d6f4a591bfdb7",
        S.REASON_LINES["page_limit"]: "940c7c869c69f005",
        S.REASON_LINES["page_too_large"]: "be4147060076bdfb",
        S.REASON_LINES["document_time"]: "8de5818f97bb2088", S.HEIC_REFUSED: "6e81ca38216b082b", S.TOO_LARGE: "9d84711266c2da06",
    }  # fmt: skip
    for text, want in pins.items():
        assert h(text) == want, text


def test_no_customer_line_says_garbled_or_claims_completeness():
    lines = [S.UNREADABLE_PAGE, *S.REASON_LINES.values(), S.DOCUMENT_LINE, S.OCR_LINE, S.FROM_OCR_DATE_LINE]
    lines += [S.REPAIRED_PAGE_LINE, S.PHOTO_LINE]
    for line in lines:
        low = line.lower()
        assert "garbled" not in low and "fully searched" not in low and "complete" not in low and "all pages" not in low


def test_the_packaged_config_is_off_and_valid_when_the_flag_is_flipped():
    from pathlib import Path

    import yaml

    body = yaml.safe_load((Path(__file__).resolve().parent.parent / "configs" / "partner_api.yaml").read_text(encoding="utf-8"))
    assert body["wf_c1_scanned_intake"] is False and C.parse_scanned_intake_config(body) is None  # shipped off
    cfg = C.parse_scanned_intake_config({**body, C.FLAG: True})
    assert cfg is not None and (cfg.min_page_confidence, cfg.min_ink_coverage, cfg.max_low_word_share) == (80.0, 0.90, 0.10)
    assert (cfg.garbled_max_odd_token_share, cfg.garbled_min_chars, cfg.dpi, cfg.languages) == (0.10, 120, 300, "eng")


def test_the_calibrated_engine_pins_are_required_strings_and_the_hash_is_64_lowercase_hex():
    for key in _STRING_KEYS:
        assert C.parse_scanned_intake_config({k: v for k, v in GOOD.items() if k != key}) is None
        for bad in (None, "", "  ", 5, True):
            assert C.parse_scanned_intake_config({**GOOD, key: bad}) is None, (key, bad)
    for bad in ("7D43" * 16, "abc", "g" * 64):
        assert C.parse_scanned_intake_config({**GOOD, "ocr_eng_traineddata_sha256": bad}) is None
    cfg = C.parse_scanned_intake_config(GOOD)
    assert cfg is not None and cfg.tesseract_version == "5.5.0" and len(cfg.eng_traineddata_sha256) == 64
