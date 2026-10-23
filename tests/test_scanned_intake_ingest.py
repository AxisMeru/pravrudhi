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


def test_every_image_gets_the_same_no_promise_refusal_while_images_are_refused():
    import hashlib

    assert hashlib.sha256(I.IMAGE_NOT_SUPPORTED_YET.encode()).hexdigest()[:16] == "b7857975b161bd99"  # R1
    for mime in ("image/heic", "image/heif", "image/jpeg", "image/png", "image/tiff"):
        with pytest.raises(MatterError) as e:
            I.ingest_document(req(image_base64="QQ==", mime=mime), A2, SCAN)
        assert (e.value.code, str(e.value), e.value.status_code) == ("image_not_supported_yet", I.IMAGE_NOT_SUPPORTED_YET, 415)
        assert "JPEG or PNG" not in str(e.value)  # never send the user to a format this build also refuses


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
    assert text2 == "\f\f" and extra2["pages_unreadable"] == 2  # empty pages: A2 says the paper is not fully indexed
    assert extra2["document_line"] == S.DOCUMENT_LINE.format(n=2)


def test_the_render_child_never_outlives_the_documents_remaining_time(monkeypatch: pytest.MonkeyPatch):
    from dataclasses import replace

    from pravrudhi.application import scanned_intake_render as R

    seen: list[float] = []

    def fake_render(data: bytes, i: int, dpi: int, timeout_s: float = 30.0, argv=None):
        seen.append(timeout_s)
        return b"png", iter(())

    monkeypatch.setattr(R, "render_pdf_page", fake_render)
    scan = replace(SCAN, page_timeout_s=30.0, document_deadline_s=4.0)
    render, runner = make(tsv(GOOD_WORDS))
    I.ingest_document(req(pdf_base64=base64.b64encode(blank_pdf(2)).decode()), A2, scan, runner=runner)
    assert len(seen) == 2 and all(0 < t <= 4.0 for t in seen)  # min(page timeout 30, the document's 4 s left)
    assert seen[1] <= seen[0]  # the second page has less time left than the first
    roomy = replace(SCAN, page_timeout_s=3.0, document_deadline_s=900.0)
    seen.clear()
    I.ingest_document(req(pdf_base64=base64.b64encode(blank_pdf(1)).decode()), A2, roomy, runner=runner)
    assert seen == [pytest.approx(3.0, abs=0.5)] or all(t <= 3.0 for t in seen)  # the page timeout still caps a roomy document


IMG_SCAN = parse_scanned_intake_config({**GOOD, "ocr_images_enabled": True})
assert IMG_SCAN is not None and IMG_SCAN.images_enabled and not SCAN.images_enabled


def _img(**kw):
    return req(image_base64=base64.b64encode(b"img").decode(), mime=kw.pop("mime", "image/jpeg"), **kw)


def test_an_image_is_read_as_one_page_without_a_text_layer_when_images_are_enabled(monkeypatch: pytest.MonkeyPatch):
    from pravrudhi.application import scanned_intake_render as R

    render, runner = make(tsv(GOOD_WORDS))
    seen = {}
    def fake(data, mime, timeout_s):
        seen.update(data=data, mime=mime)
        return render(0, 300)
    monkeypatch.setattr(R, "render_image", fake)
    text, extra = I.ingest_document(_img(), A2, IMG_SCAN, runner=runner)
    assert seen == {"data": b"img", "mime": "image/jpeg"}
    assert text == OCR_TEXT and extra["pages_unreadable"] == 0 and extra["ocr_line"] == S.OCR_LINE
    assert [p["source"] for p in extra["pages"]] == ["ocr"]


def test_an_unreadable_image_is_the_requests_422_with_r1s_line_not_a_page_reason(monkeypatch: pytest.MonkeyPatch):
    import hashlib

    from pravrudhi.application import scanned_intake_render as R

    assert hashlib.sha256(S.IMAGE_UNREADABLE.encode()).hexdigest()[:16] == "96054078bc8e5d5c"  # R1

    def boom(data, mime, timeout_s):
        raise R.ImageUnreadable("corrupt")
    monkeypatch.setattr(R, "render_image", boom)
    with pytest.raises(MatterError) as e:
        I.ingest_document(_img(), A2, IMG_SCAN, runner=make(tsv(GOOD_WORDS))[1])
    assert (e.value.code, str(e.value), e.value.status_code) == ("image_unreadable", S.IMAGE_UNREADABLE, 422)
    with pytest.raises(MatterError) as e2:
        I.ingest_document(req(image_base64="***", mime="image/png"), A2, IMG_SCAN)
    assert e2.value.code == "image_unreadable"


def test_other_image_failures_are_the_pages_reason_and_a_misread_image_keeps_low_confidence(monkeypatch: pytest.MonkeyPatch):
    from pravrudhi.application import scanned_intake_engine as E
    from pravrudhi.application import scanned_intake_render as R

    cases = ((E.OcrPageTooLarge(), "page_too_large"), (E.OcrUnavailable("x"), "ocr_unavailable"), (E.OcrTimeout(), "timeout"))
    for exc, reason in cases:
        def fail(data, mime, timeout_s, exc=exc):
            raise exc
        monkeypatch.setattr(R, "render_image", fail)
        text, extra = I.ingest_document(_img(), A2, IMG_SCAN, runner=make(tsv(GOOD_WORDS))[1])
        assert text == "\f" and extra["pages_unreadable"] == 1 and extra["pages"][0]["reason"] == reason
    render, runner = make(tsv([("a", 30.0, 0)]))
    monkeypatch.setattr(R, "render_image", lambda data, mime, timeout_s: render(0, 300))
    _, extra = I.ingest_document(_img(), A2, IMG_SCAN, runner=runner)
    assert extra["pages"][0]["reason"] == "low_confidence"


def test_heic_and_oversized_images_are_refused_when_images_are_enabled():
    for mime in ("image/heic", "image/heif"):
        with pytest.raises(MatterError) as e:
            I.ingest_document(_img(mime=mime), A2, IMG_SCAN)
        assert (e.value.code, str(e.value), e.value.status_code) == ("image_format_not_supported", S.HEIC_REFUSED, 415)
    with pytest.raises(MatterError) as e2:
        I.ingest_document(req(image_base64="A" * 20_000, mime="image/png"), MatterSpansConfig(max_pdf_bytes=1_000), IMG_SCAN)
    assert (e2.value.code, e2.value.status_code) == ("image_too_large", 413)


def test_images_stay_refused_unless_the_config_says_boolean_true():
    for v in (False, "true", 1, None):
        cfg = parse_scanned_intake_config({**GOOD, "ocr_images_enabled": v})
        assert cfg is not None and cfg.images_enabled is False
    with pytest.raises(MatterError) as e:
        I.ingest_document(_img(), A2, SCAN)
    assert e.value.code == "image_not_supported_yet"
