"""#1015 PR 1: the pure core (TSV parsing, floor, garbled detector, agreement, runner errors). A fake runner; invented
text only."""

from __future__ import annotations

import pytest

from pravrudhi.application import scanned_intake_engine as E
from pravrudhi.application.scanned_intake_config import parse_scanned_intake_config
from tests.test_scanned_intake_config_and_strings import GOOD

CFG = parse_scanned_intake_config({**GOOD, "ocr_garbled_max_odd_token_share": 0.15})
assert CFG is not None
HEAD = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext"


def row(level: int, conf: float, text: str, x: int = 10, y: int = 10, w: int = 30, h: int = 12) -> str:
    return f"{level}\t1\t1\t1\t1\t1\t{x}\t{y}\t{w}\t{h}\t{conf}\t{text}"


def test_parse_tsv_keeps_only_real_words_with_a_confidence():
    tsv = "\n".join(
        [HEAD, row(4, -1, ""), row(5, 91.5, "notice"), row(5, -1, "ghost"), row(5, 80, "  "), row(5, 60, "sent", 50, 10), "junk"]
    )
    ws = E.parse_tsv(tsv)
    assert [(w.text, w.conf) for w in ws] == [("notice", 91.5), ("sent", 60.0)] and ws[1].left == 50


def test_ink_coverage_is_the_share_of_dark_pixels_inside_word_boxes():
    ws = [E.Word("a", 90, 0, 0, 10, 10)]
    assert E.ink_coverage([(1, 1), (2, 2), (50, 50), (60, 60)], ws) == 0.5
    assert E.ink_coverage([], ws) == 0.0 and E.ink_coverage([(1, 1)], []) == 0.0


def test_the_floor_refuses_on_any_one_of_the_three_signals_and_on_no_words():
    ok = E.PageStats(100, 91.0, 0.02, 0.97)
    assert E.decide(ok, CFG) == (True, None)
    for bad in (
        E.PageStats(100, 79.9, 0.02, 0.97),
        E.PageStats(100, 91.0, 0.02, 0.89),
        E.PageStats(100, 91.0, 0.11, 0.97),
        E.PageStats(0, 0.0, 1.0, 0.99),
    ):
        assert E.decide(bad, CFG) == (False, "low_confidence"), bad
    assert E.decide(E.PageStats(100, 80.0, 0.10, 0.90), CFG) == (True, None)  # the thresholds themselves pass


def test_page_stats_from_words():
    ws = [E.Word("a", 90, 0, 0, 1, 1), E.Word("b", 40, 0, 0, 1, 1)]
    s = E.page_stats(ws, 0.95, 50.0)
    assert (s.n_words, s.mean_conf, s.low_word_share, s.ink_coverage) == (2, 65.0, 0.5, 0.95)
    assert E.page_stats([], 0.5, 50.0).n_words == 0


CLEAN = ("The statutory notice was sent on 4 May and the accused did not reply to the allegation. " * 4).strip()
SOUP = ("®§½¤ þ¶µ »©« ¬± ¥¢ " * 12).strip()


def test_the_garbled_detector_flags_symbol_soup_and_keeps_clean_and_short_and_indic_pages():
    assert E.layer_is_garbled(SOUP, CFG) is True and E.layer_is_garbled(CLEAN, CFG) is False
    assert E.layer_is_garbled("®§ ½", CFG) is False  # under garbled_min_chars: not judged
    indic = ("ਪੰਜਾਬ ਹਰਿਆਣਾ " * 30).strip()
    assert E.is_indic_page(indic) and E.layer_is_garbled(indic, CFG) is False  # Indic pages stay text_layer
    assert E.odd_token_share("") == 0.0 and E.letter_share("") == 1.0
    assert (
        E.letter_share("1234 5678 9") < 0.5 and E.layer_is_garbled("1234 5678 9012 " * 20, CFG) is True
    )  # the letter-share signal alone


def test_ocr_replaces_a_flagged_layer_only_when_it_disagrees():
    assert E.similarity("a b c", "a b c") == 1.0 and E.similarity("", "") == 1.0 and E.similarity("a", "b") == 0.0
    assert E.ocr_replaces_layer(SOUP, CLEAN, CFG) is True  # the soup layer and the clean OCR text disagree
    assert E.ocr_replaces_layer(CLEAN, CLEAN, CFG) is False  # they agree: the layer stays


def test_the_runner_maps_failures_to_the_two_reasons(monkeypatch: pytest.MonkeyPatch):
    import subprocess

    monkeypatch.delenv(E.TESSERACT_ENV, raising=False)
    monkeypatch.setattr(E.shutil, "which", lambda _n: None)
    with pytest.raises(E.OcrUnavailable):
        E.tesseract_runner()

    def boom(*_a, **_k):
        raise subprocess.TimeoutExpired("t", 1)

    monkeypatch.setattr(E.subprocess, "run", boom)
    with pytest.raises(E.OcrTimeout):
        E.tesseract_runner("/bin/true")(b"png", 1.0)

    def oserr(*_a, **_k):
        raise FileNotFoundError

    monkeypatch.setattr(E.subprocess, "run", oserr)
    with pytest.raises(E.OcrUnavailable):
        E.tesseract_runner("/nowhere/tesseract")(b"png", 1.0)

    class P:
        returncode, stdout = 1, b""

    monkeypatch.setattr(E.subprocess, "run", lambda *_a, **_k: P())
    with pytest.raises(E.OcrUnavailable):
        E.tesseract_runner("/bin/true")(b"png", 1.0)


def test_the_runner_passes_the_calibrated_settings_as_an_argv_list_with_no_shell(monkeypatch: pytest.MonkeyPatch):
    seen: dict[str, object] = {}

    class P:
        returncode, stdout = 0, b"ok"

    def fake(argv, **kw):
        seen.update(argv=argv, **kw)
        return P()

    monkeypatch.setattr(E.subprocess, "run", fake)
    assert E.tesseract_runner("/opt/tesseract")(b"png", 7.0) == "ok"
    assert seen["argv"] == ["/opt/tesseract", "stdin", "stdout", "--psm", "3", "-l", "eng", "tsv"]
    assert seen["input"] == b"png" and seen["timeout"] == 7.0 and "shell" not in seen


def test_ink_coverage_is_bounded_work_and_matches_the_naive_definition_on_overlapping_boxes():
    import random

    rng = random.Random(3)
    ws = [E.Word("w", 90, rng.randint(0, 80), rng.randint(0, 80), rng.randint(1, 30), rng.randint(1, 15)) for _ in range(25)]
    px = [(rng.randint(0, 120), rng.randint(0, 120)) for _ in range(3000)]
    naive = sum(1 for x, y in px if any(w.left <= x < w.left + w.width and w.top <= y < w.top + w.height for w in ws)) / len(px)
    assert E.ink_coverage(px, ws) == pytest.approx(naive)
    with pytest.raises(E.OcrTimeout):
        E.ink_coverage(((i, 0) for i in range(11)), ws, limit=10)


def test_the_child_environment_is_minimal_and_carries_no_inherited_secret(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "secret")
    monkeypatch.setenv("TESSDATA_PREFIX", "/opt/tessdata")
    env = E.child_env()
    assert set(env) == {"PATH", "OMP_THREAD_LIMIT", "LC_ALL", "TESSDATA_PREFIX"} and "secret" not in str(env)
