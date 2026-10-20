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
    assert [x.source for x in capped] == ["ocr", "unreadable"] and capped[1].reason == "timeout"
    ticks = iter([0.0, 100.0, 200.0])
    r3, run3 = make(tsv(GOOD_WORDS))
    assert (
        PG.process_pages([""], CFG, r3, run3, clock=lambda: next(ticks))[0].reason == "timeout"
    )  # render+OCR+coverage past the deadline


def test_assemble_and_summary_use_r1s_lines_and_never_say_garbled():
    render, runner = make(tsv(GOOD_WORDS))
    res = PG.process_pages([CLEAN, "", SOUP], CFG, render, runner)
    res.append(PG.PageResult(4, "unreadable", "", 52.3, "low_confidence"))
    assert PG.assemble(res).split("\f") == [CLEAN, OCR_TEXT, OCR_TEXT, ""]
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
