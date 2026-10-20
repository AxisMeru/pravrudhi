# ruff: noqa: E501
"""#575 switch-on prerequisite: the packaged OCR pins and the engine image agree. Track A recalibrated scanned intake on the image's own
tesseract (Debian 5.5.0, English data sha256 unchanged) and found two things that kept every scanned page at `ocr_unavailable`:
the packaged pin said 5.5.3, and the image set no TESSDATA_PREFIX, so the identity check hashed `<unset>/eng.traineddata`, found nothing and
refused. The flag stays false here; these tests pin the two corrections so neither can drift back."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pytest
import yaml

from pravrudhi.application import scanned_intake_config as C
from pravrudhi.application import scanned_intake_engine as E

REPO = Path(__file__).resolve().parent.parent
IMAGE_TESSERACT_VERSION = "5.5.0"  # `tesseract --version` of the engine image (Debian tesseract-ocr 5.5.0-1+b1)
IMAGE_ENG_SHA256 = (
    "7d4322bd2a7749724879683fc3912cb542f19906c83bcc1a52132556427170b2"  # /usr/share/tesseract-ocr/5/tessdata/eng.traineddata
)
IMAGE_TESSDATA_DIR = "/usr/share/tesseract-ocr/5/tessdata"


def _packaged() -> dict[str, object]:
    body = yaml.safe_load((REPO / "configs" / "partner_api.yaml").read_text(encoding="utf-8"))
    assert isinstance(body, dict)
    return body


def test_the_packaged_pins_are_the_ones_the_engine_image_carries_and_the_flag_stays_off() -> None:
    body = _packaged()
    assert body["ocr_tesseract_version"] == IMAGE_TESSERACT_VERSION
    assert body["ocr_eng_traineddata_sha256"] == IMAGE_ENG_SHA256
    assert body[C.FLAG] is False  # this change prepares the switch-on, it does not make it
    assert C.parse_scanned_intake_config(body) is None  # flag off: nothing is read or served
    cfg = C.parse_scanned_intake_config({**body, C.FLAG: True})  # with the flag forced on in a test, every packaged key parses
    assert cfg is not None and cfg.tesseract_version == IMAGE_TESSERACT_VERSION and cfg.eng_traineddata_sha256 == IMAGE_ENG_SHA256


def test_the_engine_image_sets_tessdata_prefix_to_the_directory_the_identity_check_hashes() -> None:
    lines = (REPO / "deploy" / "docker" / "Dockerfile").read_text(encoding="utf-8").splitlines()
    envs = [ln for ln in lines if re.match(r"^\s*ENV\s+TESSDATA_PREFIX[= ]", ln)]
    assert envs == [f"ENV TESSDATA_PREFIX={IMAGE_TESSDATA_DIR}"]  # exactly one, explicit, the Debian data directory
    installs = [i for i, ln in enumerate(lines) if "tesseract-ocr-eng" in ln and "apt-get install" in ln]
    env_at = next(i for i, ln in enumerate(lines) if ln == envs[0])
    assert installs and env_at > installs[0]  # set after the package that provides the data


def test_without_tessdata_prefix_the_identity_check_cannot_see_the_data_and_with_it_the_hash_matches(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Why the image needs the variable: the check reads `<TESSDATA_PREFIX>/eng.traineddata` and nothing else."""
    data = b"invented traineddata"
    (tmp_path / "eng.traineddata").write_bytes(data)

    class P:
        stdout, stderr, returncode = b"tesseract 5.5.0\n leptonica-1.84.1", b"", 0

    monkeypatch.setattr(E.subprocess, "run", lambda *_a, **_k: P())
    monkeypatch.delenv("TESSDATA_PREFIX", raising=False)
    assert E.tesseract_identity("tesseract") == ("5.5.0", None)  # the unset case Track A hit: no digest, a guaranteed mismatch
    monkeypatch.setenv("TESSDATA_PREFIX", str(tmp_path))
    assert E.tesseract_identity("tesseract") == ("5.5.0", hashlib.sha256(data).hexdigest())
