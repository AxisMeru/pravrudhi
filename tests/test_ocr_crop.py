"""#1015 PR 3: a passage read from a page image carries its crop box and the page's confidence (`read_from`, `crop`,
`confidence`). The box is the union of the words under the passage; a matter with no image-read page is unchanged byte for byte
(no key, no column). Invented text and boxes."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from pravrudhi.application import scanned_intake_engine as E
from pravrudhi.application import scanned_intake_ingest as I  # noqa: E741
from pravrudhi.application import scanned_intake_pages as PG
from pravrudhi.application import scanned_intake_render as R
from pravrudhi.application.matter_spans import DB_NAME, DocumentRequest, MatterStore
from pravrudhi.application.matter_spans_config import MatterSpansConfig
from tests.test_scanned_intake_config_and_strings import GOOD
from tests.test_scanned_intake_pages import CFG, HEAD

T = "org:acme"
PAGE_W, PAGE_H = 200, 300


def _png() -> bytes:
    return R.gray_to_png(PAGE_W, PAGE_H, bytes([255]) * (PAGE_W * PAGE_H))


def _tsv(rows: list[tuple[str, float, int, int, int, int, int]]) -> str:
    """(text, conf, left, top, width, height, paragraph) -> tesseract tsv, one word per line."""
    out = [HEAD]
    for i, (t, c, x, y, w, h, par) in enumerate(rows):
        out.append(f"5\t1\t1\t{par}\t1\t{i + 1}\t{x}\t{y}\t{w}\t{h}\t{c}\t{t}")
    return "\n".join(out)


# two paragraphs: A2 cuts a passage at the blank line
WORDS = [
    ("The", 95.0, 10, 20, 30, 12, 1), ("notice", 95.0, 45, 22, 50, 12, 1), ("was", 95.0, 100, 21, 25, 12, 1),
    ("sent", 95.0, 10, 60, 35, 14, 2), ("late", 95.0, 50, 62, 30, 14, 2),
]  # fmt: skip


def _store(tmp: Path) -> MatterStore:
    return MatterStore(tmp / "s", MatterSpansConfig())


def _results() -> list[PG.PageResult]:
    def render(idx: int, dpi: int) -> Any:
        return _png(), [(12, 25), (50, 28), (105, 26), (12, 66), (55, 68)]

    return PG.process_pages(["Typed first page.", ""], CFG, render, lambda png, t: _tsv(WORDS))


def test_the_words_with_offsets_cut_exactly_the_text_words_to_text_returns() -> None:
    words = E.parse_tsv(_tsv(WORDS))
    text, offs = E.words_with_offsets(words)
    assert text == E.words_to_text(words) == "The notice was\n\nsent late"
    assert [text[s:e] for s, e, _ in offs] == ["The", "notice", "was", "sent", "late"]


def test_an_ocr_page_keeps_its_word_boxes_and_the_raster_size() -> None:
    page = PG.process_pages([""], CFG, lambda i, d: (_png(), [(12, 25), (50, 28), (105, 26), (12, 66), (55, 68)]),
                            lambda png, t: _tsv(WORDS))[0]  # fmt: skip
    assert page.source == "ocr" and page.size == (PAGE_W, PAGE_H) and len(page.layout) == 5
    assert page.layout[0] == (0, 3, 10, 20, 30, 12)
    lay = PG.ocr_layout([page], 300)
    assert lay[1]["size"] == (PAGE_W, PAGE_H) and lay[1]["dpi"] == 300 and lay[1]["confidence"] == page.confidence
    unreadable = PG.process_pages([""], CFG, lambda i, d: (_png(), []), lambda png, t: _tsv([("a", 20.0, 0, 0, 5, 5, 1)]))
    assert PG.ocr_layout(unreadable, 300) == {}  # an unreadable page has no layout


def test_each_passage_on_an_image_page_gets_the_union_box_and_the_page_confidence(tmp_path: Path) -> None:
    results = _results()
    text = PG.assemble(results)
    s = _store(tmp_path)
    m = s.create_matter(T)
    s.add_document(T, m, text, "scan.pdf", [2], ocr_layout=PG.ocr_layout(results, 300))
    first = s.query(T, m, "notice")["spans"][0]
    assert first["read_from"] == "ocr" and first["from_ocr"] is True and first["page"] == 2
    assert first["crop"] == {"page": 2, "x": 10, "y": 20, "w": 115, "h": 14, "page_w": PAGE_W, "page_h": PAGE_H,
                             "unit": "px at ocr_dpi", "ocr_dpi": 300}  # fmt: skip
    assert first["confidence"] == 95.0
    second = s.query(T, m, "late")["spans"][0]
    assert (second["crop"]["x"], second["crop"]["y"], second["crop"]["w"], second["crop"]["h"]) == (10, 60, 70, 16)
    typed = s.query(T, m, "typed")["spans"][0]
    assert typed["read_from"] == "text_layer" and typed["crop"] is None and typed["confidence"] is None


def test_a_box_is_clamped_to_the_page_and_a_passage_with_no_word_has_no_crop() -> None:
    layout = {"size": (100, 100), "dpi": 300, "words": ((0, 5, -10, -5, 200, 300),)}
    from pravrudhi.application.matter_spans import _crop_for

    assert _crop_for(0, 5, layout) == {"x": 0, "y": 0, "w": 100, "h": 100, "page_w": 100, "page_h": 100,
                                       "unit": "px at ocr_dpi", "ocr_dpi": 300}  # fmt: skip
    assert _crop_for(50, 60, layout) is None
    assert _crop_for(0, 5, {"size": None, "words": layout["words"]}) is None


def test_a_matter_without_an_image_page_gets_no_new_key_and_no_new_column(tmp_path: Path) -> None:
    s = _store(tmp_path)
    m = s.create_matter(T)
    s.add_document(T, m, "A page about a notice.\n\nSecond paragraph.", "typed.pdf", None, ocr_layout=None)
    got = s.query(T, m, "notice")["spans"][0]
    assert set(got) == {"span_id", "doc_id", "name", "page", "start", "end", "text"}
    db = next((tmp_path / "s").rglob(f"{m}/{DB_NAME}"))
    with sqlite3.connect(db) as c:
        assert "ocr_box" not in [r[1] for r in c.execute("PRAGMA table_info(span)")]


def test_the_ingest_seam_fills_the_layout_for_the_route() -> None:
    import base64

    from pravrudhi.application.scanned_intake_config import parse_scanned_intake_config
    from tests.test_scanned_intake_ingest import A2, blank_pdf  # a tiny real PDF with blank pages

    scan = parse_scanned_intake_config(GOOD)
    assert scan is not None
    layout: dict[int, dict[str, object]] = {}
    pdf = base64.b64encode(blank_pdf(1)).decode()
    text, extra = I.ingest_document(
        DocumentRequest(matter_id="a" * 32, pdf_base64=pdf), A2, scan,
        render=lambda i, d: (_png(), [(12, 25), (50, 28), (105, 26), (12, 66), (55, 68)]), runner=lambda png, t: _tsv(WORDS),
        layout_out=layout,
    )  # fmt: skip
    assert extra["pages"][0]["source"] == "ocr" and list(layout) == [1] and layout[1]["size"] == (PAGE_W, PAGE_H)
