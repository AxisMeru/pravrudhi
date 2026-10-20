"""#1015 PR 2: per-page routing (fake rasteriser and runner): text layer kept, missing/garbled layers read from the image,
below-floor pages unreadable with no text, caps, deadlines and the response summary. Invented text only."""

from __future__ import annotations

from dataclasses import replace

from pravrudhi.application import scanned_intake_engine as E
from pravrudhi.application import scanned_intake_pages as PG
from pravrudhi.application import scanned_intake_strings as S
from pravrudhi.application.scanned_intake_config import parse_scanned_intake_config
from tests.test_scanned_intake_config_and_strings import GOOD
from tests.test_scanned_intake_engine import CLEAN, HEAD, SOUP

CFG = parse_scanned_intake_config(GOOD)
assert CFG is not None


def tsv(words: list[tuple[str, float, int]], par_every: int = 99) -> str:
    rows = [HEAD]
    for i, (t, c, x) in enumerate(words):
        rows.append(f"5\t1\t1\t{1 + i // par_every}\t1\t{i + 1}\t{x}\t10\t30\t12\t{c}\t{t}")
    return "\n".join(rows)


GOOD_WORDS = [("notice", 95.0, 0), ("sent", 93.0, 40), ("today", 96.0, 80)]
OCR_TEXT = "notice sent today"


def make(tsv_text: str, dark: list[tuple[int, int]] | None = None, calls: list[int] | None = None):
    pix = dark if dark is not None else [(5, 15), (45, 15), (85, 15)]  # inside the three word boxes

    def render(idx: int, dpi: int):
        if calls is not None:
            calls.append(idx)
        assert dpi == 300
        return b"png", list(pix)

    return render, (lambda _png, _t: tsv_text)


def test_a_good_text_layer_stays_and_the_runner_is_never_called():
    calls: list[int] = []
    render, runner = make(tsv(GOOD_WORDS), calls=calls)
    out = PG.process_pages([CLEAN], CFG, render, runner)
    assert [(r.page, r.source, r.text) for r in out] == [(1, "text_layer", CLEAN)] and calls == []


def test_a_missing_layer_is_read_from_its_image_and_a_low_confidence_page_has_no_text():
    render, runner = make(tsv(GOOD_WORDS))
    ok = PG.process_pages(["", CLEAN], CFG, render, runner)
    assert (ok[0].source, ok[0].text, ok[0].repaired) == ("ocr", OCR_TEXT, False) and ok[1].source == "text_layer"
    bad_render, bad_runner = make(tsv([("a", 40.0, 0), ("b", 45.0, 40)]))
    bad = PG.process_pages([""], CFG, bad_render, bad_runner)[0]
    assert (bad.source, bad.text, bad.reason, bad.readable) == ("unreadable", "", "low_confidence", False)
    sparse_render, sparse_runner = make(
        tsv(GOOD_WORDS), dark=[(5, 15), (500, 500), (600, 600), (700, 700)]
    )  # most ink outside the words
    assert PG.process_pages([""], CFG, sparse_render, sparse_runner)[0].reason == "low_confidence"


def test_a_garbled_layer_is_replaced_only_when_ocr_disagrees_and_marked_repaired():
    render, runner = make(tsv(GOOD_WORDS))
    r = PG.process_pages([SOUP], CFG, render, runner)[0]
    assert (r.source, r.text, r.repaired) == ("ocr", OCR_TEXT, True)
    agree_render, agree_runner = make(tsv([(w, 95.0, 40 * i) for i, w in enumerate(SOUP.split())][:3]))
    soup_like = SOUP.split()[:3]
    same = PG.process_pages(
        [" ".join(soup_like) + " " + SOUP], replace(CFG, agreement_min_similarity=0.0), agree_render, agree_runner
    )[0]
    assert same.source == "text_layer"  # with the agreement bar at 0 nothing is replaced


def test_a_flagged_layer_is_dropped_when_ocr_cannot_run_or_fails_the_floor():
    def no_render(_i: int, _d: int):
        raise E.OcrUnavailable

    r = PG.process_pages([SOUP], CFG, no_render, lambda _p, _t: "")[0]
    assert (r.source, r.text, r.reason) == ("unreadable", "", "ocr_unavailable")  # garbled text is never indexed
    low_render, low_runner = make(tsv([("a", 30.0, 0)]))
    assert PG.process_pages([SOUP], CFG, low_render, low_runner)[0].text == ""


def test_a_timeout_the_page_cap_and_a_slow_page_are_unreadable_with_reason_timeout():
    def slow_runner(_p: bytes, _t: float) -> str:
        raise E.OcrTimeout

    render, _ = make("")
    assert PG.process_pages([""], CFG, render, slow_runner)[0].reason == "timeout"
    r2, run2 = make(tsv(GOOD_WORDS))
    capped = PG.process_pages(["", ""], replace(CFG, max_pages=1), r2, run2)
    assert [x.source for x in capped] == ["ocr", "unreadable"] and capped[1].reason == "page_limit"  # never tried: not "timeout"
    ticks = iter([0.0, 0.0, 100.0, 200.0, 300.0])  # document start, page start, then past the deadline
    r3, run3 = make(tsv(GOOD_WORDS))
    assert (
        PG.process_pages([""], CFG, r3, run3, clock=lambda: next(ticks))[0].reason == "timeout"
    )  # render+OCR+coverage past the deadline


def test_assemble_and_summary_use_r1s_lines_and_never_say_garbled():
    render, runner = make(tsv(GOOD_WORDS))
    res = PG.process_pages([CLEAN, "", SOUP], CFG, render, runner)
    res.append(PG.PageResult(4, "unreadable", "", 52.3, "low_confidence"))
    # the extra form feed keeps the empty LAST page for A2
    assert PG.assemble(res).split("\f") == [CLEAN, OCR_TEXT, OCR_TEXT, "", ""]
    s = PG.summary(res)
    assert s["pages_unreadable"] == 1 and s["ocr_line"] == S.OCR_LINE and s["document_line"] == S.DOCUMENT_LINE.format(n=1)
    pages = s["pages"]
    assert (
        isinstance(pages, list)
        and pages[3]["line"] == S.UNREADABLE_PAGE
        and pages[3]["reason_line"] == S.REASON_LINES["low_confidence"]
    )
    assert pages[2]["repaired"] is True and pages[2]["repaired_line"] == S.REPAIRED_PAGE_LINE and pages[0]["line"] is None
    assert "garbled" not in str(s).lower()
    assert "ocr_line" not in PG.summary([PG.PageResult(1, "text_layer", CLEAN)])


def test_words_to_text_joins_lines_and_paragraphs():
    w = lambda t, b, p, ln: E.Word(t, 90.0, 0, 0, 1, 1, b, p, ln)  # noqa: E731
    assert (
        E.words_to_text([w("a", 1, 1, 1), w("b", 1, 1, 1), w("c", 1, 1, 2), w("d", 1, 2, 1), w("e", 2, 1, 1)])
        == "a b\nc\n\nd\n\ne"
    )
    assert E.words_to_text([]) == ""


def test_an_unreadable_last_page_is_kept_as_an_empty_page_in_a2_and_the_paper_is_not_fully_indexed(tmp_path):
    """Track A's gate finding on #574: the trailing empty page was dropped by A2 (n_pages short, indexed_complete true)."""
    from pravrudhi.application.matter_spans import MatterStore
    from pravrudhi.application.matter_spans_config import MatterSpansConfig

    store = MatterStore(tmp_path / "s", MatterSpansConfig())
    for pages, bad in (([CLEAN, CLEAN, ""], [3]), ([CLEAN, "", CLEAN], [2]), ([CLEAN, CLEAN], []), ([CLEAN, "", ""], [2, 3])):
        results = [
            PG.PageResult(i, "text_layer", t) if t else PG.PageResult(i, "unreadable", "", 40.0, "low_confidence")
            for i, t in enumerate(pages, 1)
        ]
        m = store.create_matter("org:t")
        out = store.add_document("org:t", m, PG.assemble(results), "paper")
        assert out["pages_without_text"] == bad and out["indexed_complete"] is (not bad), pages
        assert store.list_documents("org:t", m)["documents"][0]["n_pages"] == len(pages)


def test_the_document_deadline_stops_ocr_before_the_next_page_and_shortens_the_page_timeout():
    t = {"now": 0.0}
    seen: list[float] = []

    def clock() -> float:
        return t["now"]

    def runner(_png: bytes, timeout_s: float) -> str:
        seen.append(timeout_s)
        t["now"] += 50.0  # each page takes 50 s
        return tsv(GOOD_WORDS)

    render, _ = make("")
    cfg = replace(CFG, page_timeout_s=30.0, document_deadline_s=120.0)
    out = PG.process_pages(["", "", "", ""], cfg, render, runner, clock=clock)
    # 50 s per page is over the 30 s page timeout: each page is unreadable (the 120 s document deadline is not reached first)
    assert [r.source for r in out] == ["unreadable"] * 4
    assert seen[:2] == [30.0, 30.0]
    t["now"] = 0.0
    seen.clear()
    quick = replace(cfg, page_timeout_s=60.0, document_deadline_s=100.0)
    out2 = PG.process_pages(["", "", "", ""], quick, render, runner, clock=clock)
    assert [r.source for r in out2] == ["ocr", "ocr", "unreadable", "unreadable"]
    assert out2[2].reason == "document_time" and out2[3].reason == "document_time"
    assert seen == [60.0, 50.0]  # the second page got what was left of the document deadline


def test_each_never_or_slowly_read_page_maps_to_its_own_line_and_all_count_as_unreadable():
    """R1: timeout, page_limit, document_time and page_too_large have DISTINCT lines; a never-tried page never carries the
    per-page timeout line; all are counted in pages_unreadable and the document line; no number appears in any of them."""
    reasons = ("timeout", "page_limit", "document_time", "page_too_large")
    res = [PG.PageResult(i, "unreadable", "", None, r) for i, r in enumerate(reasons, 1)]
    res.append(PG.PageResult(5, "text_layer", CLEAN))
    s = PG.summary(res)
    lines = [p.get("reason_line") for p in s["pages"]]  # type: ignore[union-attr]
    assert lines[:4] == [S.REASON_LINES[r] for r in reasons] and len(set(lines[:4])) == 4 and lines[4] is None
    assert s["pages_unreadable"] == 4 and s["document_line"] == S.DOCUMENT_LINE.format(n=4)
    assert not any(ch.isdigit() for ch in "".join(str(x) for x in lines[:4]))  # no number (not 120 s, not the cap) in the text


def test_a_page_the_renderer_calls_too_large_is_page_too_large_and_never_a_timeout():
    def too_big(_i: int, _d: int):
        raise E.OcrPageTooLarge

    r = PG.process_pages([""], CFG, too_big, lambda _p, _t: "")[0]
    assert (r.source, r.text, r.reason) == ("unreadable", "", "page_too_large")
