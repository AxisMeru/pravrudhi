"""#1015 PR 2: the ingest seam of scanned/garbled-page intake: one document request in, the text for A2 plus the additive
response fields out. Only reached when `wf_c1_scanned_intake` is on (the route decides); PDFs are routed page by page, text
passes through, HEIC and other image uploads follow R1's rules. Images other than HEIC are refused with a plain line until a
decoder is chosen (no Pillow decision yet)."""

from __future__ import annotations

import base64
import binascii
import time
from typing import Any

from pravrudhi.application import scanned_intake_engine as E
from pravrudhi.application import scanned_intake_pages as PG
from pravrudhi.application.matter_spans import DocumentRequest, MatterError
from pravrudhi.application.matter_spans_config import MatterSpansConfig
from pravrudhi.application.scanned_intake_config import ScannedIntakeConfig

IMAGE_NOT_SUPPORTED_YET = "Photos cannot be added here. Add the paper as a PDF."  # R1 b7857975b161bd99 (no promise)


def _lazy_runner(scan: ScannedIntakeConfig | None = None) -> E.Runner:
    """The real tesseract runner, created at the first page that needs it (a missing binary is that page's `ocr_unavailable`)."""
    box: list[E.Runner] = []

    def run(png: bytes, timeout_s: float) -> str:
        if not box:
            box.append(E.tesseract_runner(cfg=scan))
        return box[0](png, timeout_s)

    return run


def ingest_document(
    req: DocumentRequest,
    a2: MatterSpansConfig,
    scan: ScannedIntakeConfig,
    *,
    render: PG.Render | None = None,
    runner: E.Runner | None = None,
) -> tuple[str, dict[str, Any]]:
    """(text for A2, extra response fields). Plain text passes through unchanged with no extra fields."""
    if req.image_base64 is not None:
        # While images are refused, HEIC gets the SAME answer as any other image: R1's HEIC line ("save it as JPEG or PNG")
        # would send the user to formats this build also refuses. It returns the day JPEG, PNG and TIFF are accepted.
        raise MatterError("image_not_supported_yet", IMAGE_NOT_SUPPORTED_YET, 415)
    if req.pdf_base64 is None:
        return req.text or "", {}
    from pravrudhi.application.draft_check import DraftRefused, pdf_text

    if len(req.pdf_base64) > (a2.max_pdf_bytes * 4) // 3 + 16:
        raise MatterError("pdf_too_large", f"The file is larger than {a2.max_pdf_bytes} bytes", 413)
    try:
        data = base64.b64decode(req.pdf_base64, validate=True)
    except (binascii.Error, ValueError) as e:
        raise MatterError("pdf_bad_encoding", "The PDF is not valid base64") from e
    if len(data) > a2.max_pdf_bytes:
        raise MatterError("pdf_too_large", f"The file is larger than {a2.max_pdf_bytes} bytes", 413)
    try:
        layered = pdf_text(data, max_pages=a2.max_pdf_pages, max_seconds=30.0, max_chars=a2.max_chars)
    except DraftRefused as e:
        raise MatterError(e.code, str(e), e.status_code) from e
    if render is None:
        from pravrudhi.application.scanned_intake_render import render_pdf_page

        doc_started = time.monotonic()  # the document deadline starts with the first page, as in `process_pages`

        def render(i: int, dpi: int) -> Any:  # noqa: F811
            left = scan.document_deadline_s - (time.monotonic() - doc_started)  # the render child never outlives the paper's time
            return render_pdf_page(data, i, dpi, timeout_s=max(0.001, min(scan.page_timeout_s, left)))

    results = PG.process_pages(layered.split("\f"), scan, render, runner or _lazy_runner(scan))
    return PG.assemble(results), PG.summary(results)
