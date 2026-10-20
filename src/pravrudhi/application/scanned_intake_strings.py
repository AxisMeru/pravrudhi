"""#1015: the customer-visible lines of scanned/garbled-page intake. R1's words (OCR_LIMITS_V2/V3, OCR_UPLOAD_LINES), pinned by
hash in tests; the engine's text must equal them exactly. Nothing here says "garbled", quotes a share, or claims a
page was fully read."""

from __future__ import annotations

UNREADABLE_PAGE = "This page could not be read."  # pages[].line
REASON_LINES = {
    "low_confidence": "This page could not be read well enough.",
    "timeout": "This page took too long to read.",
    "language_not_supported": "This page is in a language the tool does not read.",
    "ocr_unavailable": "Scanned pages cannot be read in this deployment.",
    # R1 940c7c869c69f005: over the per-document OCR page cap; the cap number is never printed
    "page_limit": "This page was not read because the paper has more pages than the tool reads at once.",
    # PENDING R1 (engine proposal): the whole paper used up its reading time before this page
    "document_time": "This page was not read because reading the paper took too long.",
}
DOCUMENT_LINE = "{n} page(s) could not be read, so this paper is only partly searched."  # when pages_unreadable > 0
OCR_LINE = "Pages read from page images may contain mistakes. Check each quote against the page image."  # ingest response
FROM_OCR_DATE_LINE = "Read from a page image: check this date against the page image."  # rows with from_ocr: true
# pages[].repaired (R1 6d58ad2bbb36f4d0: only a flagged text layer that OCR replaced, never a missing layer)
REPAIRED_PAGE_LINE = "The text in the file for this page was damaged, so the page was read from its image."
# R1 b19d6f4a591bfdb7 (replaces the earlier photo line)
PHOTO_LINE = "Photos and scans of other papers, such as an FIR or a chargesheet, may be read less well than a court order."
HEIC_REFUSED = "This photo format is not supported. Save it as JPEG or PNG and add it again."  # by mime image/heic, image/heif
# Web's line for pdf_too_large
TOO_LARGE = "This file is too large. The limit is 8 MB. Make the file smaller, or split it, and add it again."
