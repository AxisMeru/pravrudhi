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


def _run_child(argv: list[str], data: bytes, timeout_s: float, what: str) -> tuple[bytes, Iterator[tuple[int, int]]]:
    """Run one isolated rasteriser child (see above) and validate its raster. OcrUnavailable on any failure, OcrTimeout when the
    child outlives `timeout_s` (its whole group is killed); OcrPageTooLarge when it reports a too-large page (exit 3)."""
    try:
        proc = subprocess.Popen(  # noqa: S603
            argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            env=child_env(), start_new_session=True, preexec_fn=_limits(timeout_s),  # noqa: PLW1509
        )  # fmt: skip
    except OSError as e:
        raise OcrUnavailable(type(e).__name__) from None
    try:
        out, _ = proc.communicate(data, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        raise OcrTimeout from None
    except OSError as e:
        raise OcrUnavailable(type(e).__name__) from None
    finally:
        _kill_group(proc)
    if proc.returncode == 3:
        raise OcrPageTooLarge  # too large to render or decode here: never tried, not a timeout
    if proc.returncode == 2:
        raise OcrUnavailable(f"{what} is not installed")
    if proc.returncode != 0 or len(out) < 8:
        raise OcrUnavailable(f"the {what} child exited {proc.returncode}")
    width, height = struct.unpack(">II", out[:8])
    gray = out[8:]
    if width <= 0 or height <= 0 or width * height > MAX_PIXELS or len(gray) != width * height:
        raise OcrUnavailable(f"the {what} child answered with an unexpected raster")
    return gray_to_png(width, height, gray), dark_pixels(width, height, gray)


def render_pdf_page(
    pdf: bytes, index: int, dpi: int, timeout_s: float = 30.0, argv: list[str] | None = None
) -> tuple[bytes, Iterator[tuple[int, int]]]:
    """(PNG bytes, dark pixels) of page `index` (0-based), rendered by the isolated child (pypdfium2)."""
    return _run_child(argv or _worker_argv(index, dpi), pdf, timeout_s, "pypdfium2")


# The image decoder (Pillow, the `scanned` extra) runs in the SAME kind of child. The decode-bomb guard: the header is read
# first (`Image.open` is lazy) and width x height is refused over the pixel cap BEFORE any pixel is decoded (exit 3); only PNG,
# JPEG and TIFF are accepted by FORMAT (not by the claimed mime alone); the first frame only; gray output. Exit codes as above.
IMAGE_WORKER_CODE = r"""
import struct, sys
max_pixels = int(sys.argv[1])
data = sys.stdin.buffer.read()
try:
    from PIL import Image
except ImportError:
    sys.exit(2)
try:
    import io
    Image.MAX_IMAGE_PIXELS = max_pixels
    img = Image.open(io.BytesIO(data), formats=["PNG", "JPEG", "TIFF"])
    width, height = img.size
    if width * height > max_pixels:
        sys.exit(3)
    img.seek(0)
    gray = img.convert("L")
    raw = gray.tobytes()
except SystemExit:
    raise
except Image.DecompressionBombError:
    sys.exit(3)
except Exception:
    sys.exit(4)
sys.stdout.buffer.write(struct.pack(">II", width, height) + raw)
"""


def render_image(
    data: bytes, timeout_s: float = 30.0, argv: list[str] | None = None
) -> tuple[bytes, Iterator[tuple[int, int]]]:
    """(PNG bytes, dark pixels) of an uploaded PNG, JPEG or TIFF image, decoded by the isolated child."""
    worker = [sys.executable, "-I", "-c", IMAGE_WORKER_CODE, str(MAX_PIXELS)]
    return _run_child(argv or worker, data, timeout_s, "the image decoder")
