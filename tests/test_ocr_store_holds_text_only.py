"""#1015 data story: with wf_c1_scanned_intake on, a scanned PDF added to a matter leaves ONLY extracted text and page refs in
the matter store (no page image bytes, no image_base64, no uploaded PDF bytes anywhere under the engine root), and deleting the
document or the matter removes the stored text. Route level (POST /api/v1/matter-spans/*), a fake renderer that returns a marked
image and a fake tesseract runner: CPU only, no network, no tesseract binary needed. Invented text."""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from pravrudhi.api import identity
from pravrudhi.api.partner import PartnerApiConfig, build_partner_router
from pravrudhi.application import matter_spans_config as msc
from pravrudhi.application import scanned_intake_config as sic
from pravrudhi.application import scanned_intake_ingest as ingest
from pravrudhi.application import scanned_intake_render as render_mod
from pravrudhi.application.matter_spans_config import MatterSpansConfig
from tests.flag_peek_util import peek_all_true
from tests.test_api_partner import _agent, _proof_script
from tests.test_scanned_intake_config_and_strings import GOOD
from tests.test_scanned_intake_ingest import blank_pdf
from tests.test_scanned_intake_pages import tsv

A2 = "/api/v1/matter-spans"
USER = identity.User("u1", "user@example.test", "user")
CFG = PartnerApiConfig(rate_limit_per_minute=1000, max_concurrent=1, trust_proxy_header=False)
WORDS = [("zephyrquillon", 95.0, 0), ("brantwick", 93.0, 40), ("moonlathe", 96.0, 80)]  # invented, found nowhere else
OCR_TEXT = "zephyrquillon brantwick moonlathe"
IMAGE = b"\x89PNG\r\n\x1a\n" + b"PAGE-IMAGE-SENTINEL-7f3a91c4" * 8  # what the fake renderer hands the fake OCR runner


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, Path]:
    monkeypatch.setenv(msc.DIR_ENV, str(tmp_path / "store"))
    monkeypatch.setattr(msc, "load_matter_spans_config", lambda _root: MatterSpansConfig())
    peek_all_true(monkeypatch)
    scan = sic.parse_scanned_intake_config(GOOD)
    assert scan is not None
    monkeypatch.setattr(sic, "load_scanned_intake_config", lambda _root: scan)
    monkeypatch.setattr(
        render_mod, "render_pdf_page", lambda pdf, i, dpi, timeout_s=30.0, argv=None: (IMAGE, [(5, 15), (45, 15), (85, 15)])
    )
    seen: list[bytes] = []

    def runner(png: bytes, _timeout: float) -> str:
        seen.append(png)
        return tsv(WORDS)

    monkeypatch.setattr(ingest, "_lazy_runner", lambda _scan=None: runner)
    app = FastAPI()
    app.include_router(build_partner_router(tmp_path, agent_factory=lambda _r: _agent(tmp_path, _proof_script()), config=CFG))
    app.dependency_overrides[identity.current_user] = lambda: USER
    c = TestClient(app, raise_server_exceptions=False)
    c.ocr_images_seen = seen
    return c, tmp_path


def _files(root: Path) -> list[Path]:
    return [p for p in root.rglob("*") if p.is_file()]


def _hits(root: Path, needle: bytes) -> list[str]:
    return [str(p.relative_to(root)) for p in _files(root) if needle in p.read_bytes()]


def _add_scanned(c: TestClient) -> tuple[str, dict[str, Any], str]:
    pdf = blank_pdf(2)
    m = c.post(f"{A2}/matters", json={}).json()["matter_id"]
    r = c.post(f"{A2}/documents", json={"matter_id": m, "pdf_base64": base64.b64encode(pdf).decode(), "name": "scan.pdf"})
    assert r.status_code == 200, r.text
    return m, r.json(), base64.b64encode(pdf).decode()


def test_a_scanned_page_leaves_only_text_in_the_store_and_delete_removes_it(env: tuple[TestClient, Path]) -> None:
    c, root = env
    m, doc, pdf_b64 = _add_scanned(c)
    assert c.ocr_images_seen == [
        IMAGE,
        IMAGE,
    ]  # the OCR path really ran on both pages (the control: otherwise this proves nothing)
    assert [p["source"] for p in doc["pages"]] == ["ocr", "ocr"]
    # the extracted text IS stored (positive control) and is the only page content kept
    assert _hits(root / "store", OCR_TEXT.encode()), "the OCR text should be in the matter store"
    # ... and no page image, no image field, no uploaded file bytes anywhere under the engine root (store, audit, anything)
    pdf = base64.b64decode(pdf_b64)
    for needle in (IMAGE, b"PAGE-IMAGE-SENTINEL", base64.b64encode(IMAGE), b"image_base64", pdf, pdf_b64.encode()):
        assert _hits(root, needle) == [], (needle[:24], _hits(root, needle))
    assert not [
        p for p in _files(root / "store") if p.suffix.lower() in {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".heic", ".pdf"}
    ]
    # the response carries page references, not images
    assert not [k for k in (*doc, *(k for p in doc["pages"] for k in p)) if "image" in k or "base64" in k]
    r = c.post(f"{A2}/query", json={"matter_id": m, "q": "zephyrquillon"})
    assert r.status_code == 200 and "zephyrquillon" in r.text and "PAGE-IMAGE-SENTINEL" not in r.text
    # delete the document: its text is gone from every file under the root
    doc_id = c.post(f"{A2}/list", json={"matter_id": m}).json()["documents"][0]["doc_id"]
    assert c.post(f"{A2}/delete", json={"matter_id": m, "doc_id": doc_id}).status_code == 200
    assert _hits(root, OCR_TEXT.encode()) == [] and _hits(root, b"zephyrquillon") == []


def test_deleting_the_matter_removes_the_scanned_text_and_the_folder(env: tuple[TestClient, Path]) -> None:
    c, root = env
    m, _doc, _b64 = _add_scanned(c)
    assert _hits(root / "store", b"brantwick")
    assert c.post(f"{A2}/delete", json={"matter_id": m}).status_code == 200
    for needle in (b"zephyrquillon", b"brantwick", b"moonlathe", IMAGE):
        assert _hits(root, needle) == [], needle
    assert not (root / "store" / m).exists()
