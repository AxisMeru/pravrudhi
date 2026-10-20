"""#1015 PR 2: the ingest seam (fake renderer and runner, a tiny real PDF): text passes through, PDFs are routed page by page,
images are refused by mime, the size and base64 rules, and the request validator. Invented data."""

from __future__ import annotations

import base64

import pytest
from pydantic import ValidationError

from pravrudhi.application import scanned_intake_ingest as I  # noqa: E741
from pravrudhi.application import scanned_intake_strings as S
from pravrudhi.application.matter_spans import DocumentRequest, MatterError
from pravrudhi.application.matter_spans_config import MatterSpansConfig
from pravrudhi.application.scanned_intake_config import parse_scanned_intake_config
from tests.test_scanned_intake_config_and_strings import GOOD
from tests.test_scanned_intake_pages import GOOD_WORDS, OCR_TEXT, make, tsv

SCAN = parse_scanned_intake_config(GOOD)
A2 = MatterSpansConfig()
assert SCAN is not None
M = "a" * 32


def blank_pdf(n: int) -> bytes:
    from io import BytesIO

    from pypdf import PdfWriter

    w = PdfWriter()
    for _ in range(n):
        w.add_blank_page(width=200, height=200)
    b = BytesIO()
    w.write(b)
    return b.getvalue()


def req(**kw):
    return DocumentRequest(matter_id=M, **kw)


def test_the_request_takes_exactly_one_source_and_mime_only_with_an_image():
    assert req(text="x").text == "x"
    for bad in (
        {}, {"text": "x", "pdf_base64": "QQ=="}, {"image_base64": "QQ=="}, {"text": "x", "mime": "image/png"},
        {"image_base64": "QQ==", "mime": "image/gif"}, {"text": "x", "image_base64": "QQ==", "mime": "image/png"},
    ):  # fmt: skip
        with pytest.raises(ValidationError):
            req(**bad)
    assert req(image_base64="QQ==", mime="image/png").mime == "image/png"


def test_plain_text_passes_through_with_no_extra_fields():
    assert I.ingest_document(req(text="A paper."), A2, SCAN) == ("A paper.", {})


def test_images_are_refused_heic_with_r1s_line_and_the_rest_until_a_decoder_is_chosen():
    for mime in ("image/heic", "image/heif"):
        with pytest.raises(MatterError) as e:
            I.ingest_document(req(image_base64="QQ==", mime=mime), A2, SCAN)
        assert str(e.value) == S.HEIC_REFUSED and e.value.code == "image_format_not_supported"
    with pytest.raises(MatterError) as e2:
        I.ingest_document(req(image_base64="QQ==", mime="image/jpeg"), A2, SCAN)
    assert e2.value.code == "image_not_supported_yet" and str(e2.value) == I.IMAGE_NOT_SUPPORTED_YET


def test_pdf_size_and_encoding_rules_and_the_reworded_too_large_line():
    with pytest.raises(MatterError) as e:
        I.ingest_document(req(pdf_base64="A" * 20_000), MatterSpansConfig(max_pdf_bytes=1_000), SCAN)
    assert e.value.code == "pdf_too_large" and str(e.value) == "The file is larger than 1000 bytes"
    assert e.value.status_code == 413
    with pytest.raises(MatterError) as e2:
        I.ingest_document(req(pdf_base64="***not base64***"), A2, SCAN)
    assert e2.value.code == "pdf_bad_encoding"


def test_a_pdf_with_blank_pages_is_read_from_the_images_and_returns_the_page_fields():
    pdf = base64.b64encode(blank_pdf(2)).decode()
    render, runner = make(tsv(GOOD_WORDS))
    text, extra = I.ingest_document(req(pdf_base64=pdf), A2, SCAN, render=render, runner=runner)
    assert text.split("\f") == [OCR_TEXT, OCR_TEXT]
    assert extra["pages_unreadable"] == 0 and extra["ocr_line"] == S.OCR_LINE
    assert [p["source"] for p in extra["pages"]] == ["ocr", "ocr"]
    bad_render, bad_runner = make(tsv([("a", 30.0, 0)]))
    text2, extra2 = I.ingest_document(req(pdf_base64=pdf), A2, SCAN, render=bad_render, runner=bad_runner)
    assert text2 == "\f" and extra2["pages_unreadable"] == 2  # empty pages: A2 says the paper is not fully indexed
    assert extra2["document_line"] == S.DOCUMENT_LINE.format(n=2)
