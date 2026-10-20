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


def _child(tmp_path, code: str) -> list[str]:
    return [sys.executable, "-I", "-c", code]


def test_the_real_worker_without_pypdfium2_is_ocr_unavailable():
    try:
        import pypdfium2  # noqa: F401
    except ImportError:
        with pytest.raises(E.OcrUnavailable):  # exit code 2 from the isolated child
            R.render_pdf_page(b"%PDF", 0, 300)
    else:
        pytest.skip("pypdfium2 is installed: the real render runs in the image gate")


def test_a_raster_the_child_returns_is_checked_and_turned_into_a_png_and_dark_pixels(tmp_path):
    code = "import struct,sys; sys.stdin.buffer.read(); "
    code += "sys.stdout.buffer.write(struct.pack('>II',3,2)+bytes([255,0,255,0,255,9]))"
    png, dark = R.render_pdf_page(b"%PDF", 0, 300, argv=_child(tmp_path, code))
    assert png.startswith(b"\x89PNG") and list(dark) == [(1, 0), (0, 1), (2, 1)]


def test_the_exit_codes_and_a_bad_raster_map_to_the_two_reasons(tmp_path):
    for exit_code, exc in ((3, E.OcrPageTooLarge), (2, E.OcrUnavailable), (4, E.OcrUnavailable), (1, E.OcrUnavailable)):
        with pytest.raises(exc):
            code = f"import sys; sys.stdin.buffer.read(); sys.exit({exit_code})"
            R.render_pdf_page(b"%PDF", 0, 300, argv=_child(tmp_path, code))
    bad = [
        "import sys; sys.stdout.buffer.write(b'x')",  # shorter than the header
        "import struct,sys; sys.stdout.buffer.write(struct.pack('>II',3,2)+b'ab')",  # too few bytes
        "import struct,sys; sys.stdout.buffer.write(struct.pack('>II',0,2))",  # zero width
        "import struct,sys; sys.stdout.buffer.write(struct.pack('>II',99999,99999))",  # over the pixel cap
    ]
    for code in bad:
        with pytest.raises(E.OcrUnavailable):
            R.render_pdf_page(b"%PDF", 0, 300, argv=_child(tmp_path, "import sys; sys.stdin.buffer.read()\n" + code))


def test_a_hung_renderer_is_killed_with_its_whole_group_after_the_timeout(tmp_path):
    import contextlib
    import os
    import time

    code = "import subprocess,sys,time; subprocess.Popen(['sleep','28.456']); time.sleep(60)"
    with pytest.raises(E.OcrTimeout):
        R.render_pdf_page(b"%PDF", 0, 300, timeout_s=0.8, argv=_child(tmp_path, code))
    time.sleep(0.3)
    survivors = [d for d in os.listdir("/proc") if d.isdigit() and _cmdline(d) == "sleep\x0028.456\x00"]
    for pid in survivors:
        with contextlib.suppress(OSError):
            os.kill(int(pid), 9)
    assert survivors == []


def _cmdline(pid: str) -> str:
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            return f.read().decode("utf-8", "replace")
    except OSError:
        return ""


def test_the_worker_script_is_self_contained_and_takes_the_page_cap_from_the_parent():
    assert "pravrudhi" not in R.WORKER_CODE and "import pypdfium2" in R.WORKER_CODE
    argv = R._worker_argv(4, 300)
    assert argv[1] == "-I" and argv[-3:] == ["4", "300", str(R.MAX_PIXELS)]


def test_the_real_image_worker_without_pillow_is_ocr_unavailable():
    try:
        import PIL  # noqa: F401
    except ImportError:
        with pytest.raises(E.OcrUnavailable):  # exit code 2 from the isolated child
            R.render_image(b"\x89PNG")
    else:
        pytest.skip("Pillow is installed: the real decode runs in the image gate")


def test_the_image_worker_is_self_contained_guards_the_header_before_decoding_and_accepts_three_formats_only():
    code = R.IMAGE_WORKER_CODE
    assert "pravrudhi" not in code and 'formats=["PNG", "JPEG", "TIFF"]' in code
    assert "Image.MAX_IMAGE_PIXELS = max_pixels" in code
    # the size is refused before any pixel is decoded
    assert code.index("width * height > max_pixels") < code.index('convert("L")')
    assert code.index("sys.exit(3)") < code.index("img.seek(0)")


def test_a_decoded_image_is_validated_and_the_exit_codes_map_like_the_pdf_worker(tmp_path):
    ok = "import struct,sys; sys.stdin.buffer.read(); sys.stdout.buffer.write(struct.pack('>II',2,2)+bytes([0,255,255,0]))"
    png, dark = R.render_image(b"x", argv=_child(tmp_path, ok))
    assert png.startswith(b"\x89PNG") and list(dark) == [(0, 0), (1, 1)]
    for code, exc in ((3, E.OcrPageTooLarge), (2, E.OcrUnavailable), (4, E.OcrUnavailable)):  # bomb, no Pillow, undecodable
        with pytest.raises(exc):
            R.render_image(b"x", argv=_child(tmp_path, f"import sys; sys.stdin.buffer.read(); sys.exit({code})"))
