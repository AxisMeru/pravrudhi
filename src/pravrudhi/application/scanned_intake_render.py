"""#1015 PR 2: the page rasteriser for scanned/garbled-page intake: pypdfium2 (the `scanned` extra, imported ONLY here and
only when a page needs OCR), grayscale at the configured dpi, the raster size capped BEFORE anything is allocated, dark pixels as
a generator, and the PNG that tesseract reads encoded with the standard library (no Pillow). A missing pypdfium2 is
`OcrUnavailable`."""

from __future__ import annotations

import struct
import subprocess
import sys
import zlib
from collections.abc import Iterator

from pravrudhi.application.scanned_intake_engine import (
    OcrPageTooLarge,
    OcrTimeout,
    OcrUnavailable,
    _kill_group,
    _limits,
    child_env,
)

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


# The rasteriser runs in a CHILD process (own process group, the same resource limits and minimal environment as the
# tesseract child): PDFium parses partner PDFs in native code, so a crash, a hang or a memory blow-up there must not take the
# engine with it. The child reads the PDF on stdin and writes `>II` width, height and the raw gray bytes on stdout. Exit codes:
# 2 no pypdfium2, 3 page too big, 4 any other failure. The script is a string so the child imports nothing of this project.
WORKER_CODE = r"""
import struct, sys
index, dpi, max_pixels = int(sys.argv[1]), int(sys.argv[2]), int(sys.argv[3])
data = sys.stdin.buffer.read()
try:
    import pypdfium2 as pdfium
except ImportError:
    sys.exit(2)
try:
    doc = pdfium.PdfDocument(data)
    page = doc[index]
    w_pt, h_pt = page.get_size()
    scale = dpi / 72.0
    if int(w_pt * scale) * int(h_pt * scale) > max_pixels:
        sys.exit(3)
    bitmap = page.render(scale=scale, grayscale=True)
    width, height, stride = bitmap.width, bitmap.height, bitmap.stride
    buf = bytes(bitmap.buffer)
except SystemExit:
    raise
except Exception:
    sys.exit(4)
rows = b"".join(buf[y * stride : y * stride + width] for y in range(height)) if stride != width else buf
sys.stdout.buffer.write(struct.pack(">II", width, height) + rows)
"""


def _worker_argv(index: int, dpi: int) -> list[str]:
    return [sys.executable, "-I", "-c", WORKER_CODE, str(index), str(dpi), str(MAX_PIXELS)]


def render_pdf_page(
    pdf: bytes, index: int, dpi: int, timeout_s: float = 30.0, argv: list[str] | None = None
) -> tuple[bytes, Iterator[tuple[int, int]]]:
    """(PNG bytes, dark pixels) of page `index` (0-based), rendered by the isolated child. OcrUnavailable without pypdfium2 or on
    any child failure; OcrTimeout when the page would exceed MAX_PIXELS or the child outlives `timeout_s` (its whole group is
    killed)."""
    try:
        proc = subprocess.Popen(  # noqa: S603
            argv or _worker_argv(index, dpi), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            env=child_env(), start_new_session=True, preexec_fn=_limits(timeout_s),  # noqa: PLW1509
        )  # fmt: skip
    except OSError as e:
        raise OcrUnavailable(type(e).__name__) from None
    try:
        out, _ = proc.communicate(pdf, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        raise OcrTimeout from None
    except OSError as e:
        raise OcrUnavailable(type(e).__name__) from None
    finally:
        _kill_group(proc)
    if proc.returncode == 3:
        raise OcrPageTooLarge  # the page is too large to render here: never tried, not a timeout
    if proc.returncode == 2:
        raise OcrUnavailable("pypdfium2 is not installed")
    if proc.returncode != 0 or len(out) < 8:
        raise OcrUnavailable(f"the page renderer exited {proc.returncode}")
    width, height = struct.unpack(">II", out[:8])
    gray = out[8:]
    if width <= 0 or height <= 0 or width * height > MAX_PIXELS or len(gray) != width * height:
        raise OcrUnavailable("the page renderer answered with an unexpected raster")
    return gray_to_png(width, height, gray), dark_pixels(width, height, gray)
