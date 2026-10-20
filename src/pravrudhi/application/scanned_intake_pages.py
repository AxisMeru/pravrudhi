"""#1015 PR 2: per-page routing of a PDF's pages through the scanned/garbled-page intake (pure orchestration; the rasteriser
and the tesseract runner are injected, so tests need neither). For each page: a good text layer stays `text_layer`; a MISSING
layer, or a layer the detector flags as garbled and OCR disagrees with, is read from its image (`ocr`); a page below the floor,
over the page cap, too slow or without OCR is `unreadable` and contributes NO text (neither layer is guessed or kept).
Indic-script pages stay as their layer (English OCR cannot read them)."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from pravrudhi.application import scanned_intake_engine as E
from pravrudhi.application import scanned_intake_strings as S
from pravrudhi.application.scanned_intake_config import ScannedIntakeConfig

Render = Callable[[int, int], tuple[bytes, Iterable[tuple[int, int]]]]  # (page index 0-based, dpi) -> (png bytes, dark pixels)


@dataclass(frozen=True)
class PageResult:
    page: int  # 1-based
    source: str  # text_layer | ocr | unreadable
    text: str
    confidence: float | None = None
    reason: str | None = None
    repaired: bool = False  # a text-layer page that was re-read from its image

    @property
    def readable(self) -> bool:
        return self.source != "unreadable"


def _unreadable(page: int, reason: str, conf: float | None = None) -> PageResult:
    return PageResult(page, "unreadable", "", conf, reason)


def _ocr_page(page: int, cfg: ScannedIntakeConfig, render: Render, runner: E.Runner, clock: Callable[[], float]) -> PageResult:
    started = clock()
    try:
        png, dark = render(page - 1, cfg.dpi)
        words = E.parse_tsv(runner(png, cfg.page_timeout_s))
        coverage = E.ink_coverage(dark, words)
    except E.OcrTimeout:
        return _unreadable(page, "timeout")
    except E.OcrUnavailable:
        return _unreadable(page, "ocr_unavailable")
    if clock() - started > cfg.page_timeout_s:  # the deadline covers render + OCR + coverage, not only the child process
        return _unreadable(page, "timeout")
    stats = E.page_stats(words, coverage, cfg.low_word_confidence)
    ok, reason = E.decide(stats, cfg)
    if not ok:
        return _unreadable(page, reason or "low_confidence", stats.mean_conf)
    return PageResult(page, "ocr", E.words_to_text(words), stats.mean_conf)


def process_pages(
    layers: list[str], cfg: ScannedIntakeConfig, render: Render, runner: E.Runner, clock: Callable[[], float] = time.monotonic
) -> list[PageResult]:
    out: list[PageResult] = []
    ocr_done = 0
    for i, layer in enumerate(layers, 1):
        blank = not layer.strip()
        flagged = (not blank) and E.layer_is_garbled(layer, cfg)
        if not blank and not flagged:
            out.append(PageResult(i, "text_layer", layer))
            continue
        if blank and E.is_indic_page(layer):  # unreachable for a blank page; kept explicit: Indic pages are never OCR'd
            out.append(PageResult(i, "text_layer", layer))
            continue
        if ocr_done >= cfg.max_pages:
            out.append(_unreadable(i, "timeout"))  # over the per-document OCR page cap: refused, not guessed
            continue
        ocr_done += 1
        res = _ocr_page(i, cfg, render, runner, clock)
        if flagged and res.source == "ocr" and not E.ocr_replaces_layer(layer, res.text, cfg):
            out.append(PageResult(i, "text_layer", layer))  # OCR agrees with the layer: it was not really garbled
        elif flagged and res.source == "ocr":
            out.append(PageResult(i, "ocr", res.text, res.confidence, repaired=True))
        else:
            out.append(
                res
            )  # blank page read, or an unreadable page (a flagged layer is dropped too: garbled text is worse than none)
    return out


def assemble(results: list[PageResult]) -> str:
    """The document text for A2: page texts joined by form feed (the intake convention); an unreadable page is an empty page, so
    A2 records it in `pages_without_text` and the document is `indexed_complete` only when every page is readable."""
    return "\f".join(r.text for r in results)


def summary(results: list[PageResult]) -> dict[str, object]:
    """The additive ingest-response fields: pages[], pages_unreadable and R1's lines (nothing says garbled or quotes a share)."""
    pages: list[dict[str, object]] = []
    for r in results:
        row: dict[str, object] = {
            "page": r.page,
            "source": r.source,
            "confidence": r.confidence,
            "readable": r.readable,
            "reason": r.reason,
        }
        row["line"] = None if r.readable else S.UNREADABLE_PAGE
        if r.reason:
            row["reason_line"] = S.REASON_LINES.get(r.reason)
        if r.repaired:
            row["repaired"] = True
            row["repaired_line"] = S.REPAIRED_PAGE_LINE
        pages.append(row)
    n_bad = sum(1 for r in results if not r.readable)
    out: dict[str, object] = {"pages": pages, "pages_unreadable": n_bad}
    if any(r.source == "ocr" for r in results):
        out["ocr_line"] = S.OCR_LINE
    if n_bad:
        out["document_line"] = S.DOCUMENT_LINE.format(n=n_bad)
    return out
