"""#1015 PR 2: the page rasteriser for scanned/garbled-page intake: pypdfium2 (the `scanned` extra, imported ONLY here and
only when a page needs OCR), grayscale at the configured dpi, the raster size capped BEFORE anything is allocated, dark pixels as
a generator, and the PNG that tesseract reads encoded with the standard library (no Pillow). A missing pypdfium2 is
`OcrUnavailable`."""

from __future__ import annotations

import struct
import zlib
from collections.abc import Iterator

from pravrudhi.application.scanned_intake_engine import OcrTimeout, OcrUnavailable

MAX_PIXELS = 50_000_000  # width x height cap before rendering (an A4 page at 300 dpi is about 8.7 million)
DARK = 128  # a gray value below this is ink


def gray_to_png(width: int, height: int, gray: bytes) -> bytes:
    """An 8-bit grayscale PNG from `width * height` gray bytes (row-major, no padding)."""
    if width <= 0 or height <= 0 or len(gray) != width * height:
        raise ValueError("bad raster")
    raw = b"".join(b"\x00" + gray[y * width : (y + 1) * width] for y in range(height))  # filter type 0 on every row

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b"")


def dark_pixels(width: int, height: int, gray: bytes) -> Iterator[tuple[int, int]]:
    for y in range(height):
        row = gray[y * width : (y + 1) * width]
        for x, v in enumerate(row):
            if v < DARK:
                yield x, y


def render_pdf_page(pdf: bytes, index: int, dpi: int) -> tuple[bytes, Iterator[tuple[int, int]]]:
    """(PNG bytes, dark pixels) of page `index` (0-based). OcrUnavailable without pypdfium2; OcrTimeout when the raster would
    exceed MAX_PIXELS (refused before it is allocated)."""
    try:
        import pypdfium2 as pdfium  # type: ignore[import-not-found,unused-ignore]
    except ImportError:
        raise OcrUnavailable("pypdfium2 is not installed") from None
    try:
        doc = pdfium.PdfDocument(pdf)
        page = doc[index]
        w_pt, h_pt = page.get_size()
        scale = dpi / 72.0
        if int(w_pt * scale) * int(h_pt * scale) > MAX_PIXELS:
            raise OcrTimeout
        bitmap = page.render(scale=scale, grayscale=True)
        width, height, stride = bitmap.width, bitmap.height, bitmap.stride
        buf = bytes(bitmap.buffer)
    except (OcrTimeout, OcrUnavailable):
        raise
    except Exception as e:  # noqa: BLE001 - any renderer failure is "this page cannot be rendered here"
        raise OcrUnavailable(type(e).__name__) from None
    gray = b"".join(buf[y * stride : y * stride + width] for y in range(height)) if stride != width else buf
    return gray_to_png(width, height, gray), dark_pixels(width, height, gray)
