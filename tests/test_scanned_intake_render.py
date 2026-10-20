"""#1015 PR 2: the stdlib PNG encoder, dark pixels, the raster cap and the missing-renderer case. A real render runs only when
pypdfium2 is installed (the image gate)."""

from __future__ import annotations

import struct
import sys
import zlib

import pytest

from pravrudhi.application import scanned_intake_engine as E
from pravrudhi.application import scanned_intake_render as R


def test_the_png_round_trips_and_the_chunks_have_valid_crcs():
    width, height = 5, 3
    gray = bytes(range(15))
    png = R.gray_to_png(width, height, gray)
    pos, idat, dims = 8, b"", (0, 0)
    while pos < len(png):
        n = struct.unpack(">I", png[pos : pos + 4])[0]
        kind, data = png[pos + 4 : pos + 8], png[pos + 8 : pos + 8 + n]
        assert struct.unpack(">I", png[pos + 8 + n : pos + 12 + n])[0] == zlib.crc32(kind + data) & 0xFFFFFFFF
        if kind == b"IHDR":
            dims = struct.unpack(">II", data[:8])
        if kind == b"IDAT":
            idat += data
        pos += 12 + n
    assert dims == (5, 3)
    raw = zlib.decompress(idat)
    rows = [raw[i * 6 + 1 : (i + 1) * 6] for i in range(3)]
    assert b"".join(rows) == gray and all(raw[i * 6] == 0 for i in range(3))
    for bad in ((0, 3, b""), (5, 3, b"x"), (-1, 1, b"")):
        with pytest.raises(ValueError):
            R.gray_to_png(*bad)


def test_dark_pixels_are_those_below_the_threshold():
    gray = bytes([255, 0, 127, 128, 200, 5])
    assert list(R.dark_pixels(3, 2, gray)) == [(1, 0), (2, 0), (2, 1)]


def test_a_missing_pypdfium2_is_ocr_unavailable(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setitem(sys.modules, "pypdfium2", None)  # an import of None raises ImportError
    with pytest.raises(E.OcrUnavailable):
        R.render_pdf_page(b"%PDF", 0, 300)


def test_an_oversize_raster_is_refused_before_it_is_allocated(monkeypatch: pytest.MonkeyPatch):
    class Page:
        def get_size(self):
            return (100000.0, 100000.0)

        def render(self, **_k):  # would be called only after the cap check
            raise AssertionError("rendered an oversize page")

    class Doc:
        def __init__(self, _d):
            pass

        def __getitem__(self, _i):
            return Page()

    fake = type(sys)("pypdfium2")
    fake.PdfDocument = Doc  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "pypdfium2", fake)
    with pytest.raises(E.OcrTimeout):
        R.render_pdf_page(b"%PDF", 0, 300)


def test_a_renderer_failure_is_ocr_unavailable(monkeypatch: pytest.MonkeyPatch):
    fake = type(sys)("pypdfium2")

    def boom(_d):
        raise RuntimeError("bad pdf")

    fake.PdfDocument = boom  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "pypdfium2", fake)
    with pytest.raises(E.OcrUnavailable):
        R.render_pdf_page(b"%PDF", 0, 300)
