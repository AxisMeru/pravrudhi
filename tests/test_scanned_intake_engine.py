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


def _script(tmp_path, body: str):
    exe = tmp_path / "fake-tesseract"
    exe.write_text("#!/bin/sh\n" + body)
    exe.chmod(0o755)
    return str(exe)


def test_the_runner_maps_failures_to_the_two_reasons(monkeypatch: pytest.MonkeyPatch, tmp_path):
    monkeypatch.delenv(E.TESSERACT_ENV, raising=False)
    monkeypatch.setattr(E.shutil, "which", lambda _n: None)
    with pytest.raises(E.OcrUnavailable):
        E.tesseract_runner()
    with pytest.raises(E.OcrUnavailable):  # the binary does not exist: not a crash
        E.tesseract_runner("/nowhere/tesseract")(b"png", 1.0)
    with pytest.raises(E.OcrUnavailable):  # a non-zero exit
        E.tesseract_runner(_script(tmp_path, "cat >/dev/null\nexit 3\n"))(b"png", 5.0)
    with pytest.raises(E.OcrTimeout):
        E.tesseract_runner(_script(tmp_path, "sleep 20\n"))(b"png", 0.5)


def test_the_runner_passes_the_calibrated_settings_png_on_stdin_and_the_text_on_stdout(tmp_path):
    argv_file, stdin_file = tmp_path / "argv", tmp_path / "stdin"
    exe = _script(tmp_path, f'echo "$@" > {argv_file}\ncat > {stdin_file}\necho text-out\n')
    # the child may not write files (RLIMIT_FSIZE 0): redirecting to a file kills it, so assert the refusal, then the stdout path
    with pytest.raises(E.OcrUnavailable):
        E.tesseract_runner(exe)(b"PNGDATA", 5.0)
    plain = _script(tmp_path, "cat >/dev/null\necho text-out\n")
    assert E.tesseract_runner(plain)(b"PNGDATA", 5.0).strip() == "text-out"
    assert E.ARGS == ("--psm", "3", "-l", "eng", "tsv")


def test_a_timeout_kills_the_whole_process_group_not_only_the_child(tmp_path):
    import contextlib
    import os
    import time

    exe = _script(tmp_path, "sleep 29.123 &\nwait\n")  # an unusual duration: no other process is mistaken for it
    with pytest.raises(E.OcrTimeout):
        E.tesseract_runner(exe)(b"png", 0.6)
    time.sleep(0.3)
    survivors = [d for d in os.listdir("/proc") if d.isdigit() and _cmdline(d) == "sleep\x0029.123\x00"]
    for pid in survivors:  # never leave a stray behind, whichever way the assertion goes
        with contextlib.suppress(OSError):
            os.kill(int(pid), 9)
    assert survivors == []  # the grandchild died with the group


def _cmdline(pid: str) -> str:
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            return f.read().decode("utf-8", "replace")
    except OSError:
        return ""


def _fake_version(monkeypatch: pytest.MonkeyPatch, tmp_path, out: str, data: bytes | None):
    E._VERIFIED.clear()  # the build check is remembered once per process: each case starts fresh

    class P:
        stdout, stderr, returncode = out.encode(), b"", 0

    monkeypatch.setattr(E.subprocess, "run", lambda *_a, **_k: P())
    if data is not None:
        (tmp_path / "eng.traineddata").write_bytes(data)
        monkeypatch.setenv("TESSDATA_PREFIX", str(tmp_path))
    else:
        monkeypatch.delenv("TESSDATA_PREFIX", raising=False)


def test_the_runner_is_refused_unless_the_tesseract_build_and_english_data_are_the_calibrated_ones(monkeypatch, tmp_path):
    import hashlib
    from dataclasses import replace

    data = b"invented traineddata"
    cfg = replace(CFG, tesseract_version="5.5.3", eng_traineddata_sha256=hashlib.sha256(data).hexdigest())
    _fake_version(monkeypatch, tmp_path, "tesseract 5.5.3\n leptonica-1.87.0", data)
    assert callable(E.tesseract_runner("/opt/tesseract", cfg))  # the calibrated build and data: accepted
    _fake_version(monkeypatch, tmp_path, "tesseract 5.3.0\n", data)
    with pytest.raises(E.OcrUnavailable):  # a different version (for example a distro package)
        E.tesseract_runner("/opt/tesseract", cfg)
    _fake_version(monkeypatch, tmp_path, "tesseract 5.5.3\n", b"other data")
    with pytest.raises(E.OcrUnavailable):  # the right version with other language data
        E.tesseract_runner("/opt/tesseract", cfg)
    _fake_version(monkeypatch, tmp_path, "tesseract 5.5.3\n", None)
    with pytest.raises(E.OcrUnavailable):  # no data directory to check: not trusted
        E.tesseract_runner("/opt/tesseract", cfg)
    assert E.tesseract_identity.__name__ == "tesseract_identity"


def test_an_unreadable_data_file_is_ocr_unavailable_and_not_remembered_but_a_real_mismatch_is(monkeypatch, tmp_path):
    from dataclasses import replace

    cfg = replace(CFG, tesseract_version="5.5.3", eng_traineddata_sha256="0" * 64)
    _fake_version(monkeypatch, tmp_path, "tesseract 5.5.3\n", b"data")
    calls = {"n": 0}
    real = E.tesseract_identity

    def counting(exe, timeout_s=10.0):
        calls["n"] += 1
        return real(exe, timeout_s)

    monkeypatch.setattr(E, "tesseract_identity", counting)
    real_open = open

    def deny(path, *a, **k):
        if str(path).endswith("eng.traineddata"):
            raise PermissionError
        return real_open(path, *a, **k)

    monkeypatch.setattr("builtins.open", deny)
    for _ in range(3):  # a transient failure (unreadable data) answers ocr_unavailable each time and is NOT cached
        with pytest.raises(E.OcrUnavailable):
            E.tesseract_runner("/opt/tesseract", cfg)
    assert calls["n"] == 3
    monkeypatch.setattr("builtins.open", real_open)
    calls["n"] = 0
    for _ in range(3):  # a REAL mismatch (other data) is remembered: checked once
        with pytest.raises(E.IdentityMismatch):
            E.tesseract_runner("/opt/tesseract", cfg)
    assert calls["n"] == 1


def test_kill_group_touches_only_a_live_child_and_always_closes_the_pipes(monkeypatch):
    killed: list[int] = []
    monkeypatch.setattr(E.os, "killpg", lambda pid, sig: killed.append(pid))

    class Pipe:
        closed = False

        def close(self):
            self.closed = True

    class Proc:
        pid = 4242

        def __init__(self, rc):
            self.rc, self.stdin, self.stdout, self.waited = rc, Pipe(), Pipe(), False

        def poll(self):
            return self.rc

        def kill(self):
            pass

        def wait(self):
            self.waited = True

    reaped, live = Proc(0), Proc(None)
    E._kill_group(reaped)  # type: ignore[arg-type]
    assert killed == [] and reaped.stdin.closed and reaped.stdout.closed  # a reaped pid's group is never signalled
    E._kill_group(live)  # type: ignore[arg-type]
    assert killed == [4242] and live.waited and live.stdin.closed and live.stdout.closed


def test_the_child_limits_fail_closed_where_resource_is_missing_and_the_module_still_imports(
    monkeypatch: pytest.MonkeyPatch, tmp_path
):
    """`resource` is POSIX-only: it is imported inside `_limits` (the parent), never at module level, and a platform without it
    gets `OcrUnavailable` (the page reads ocr_unavailable) instead of an internal error."""
    import sys

    monkeypatch.setitem(sys.modules, "resource", None)  # `import resource` now raises ImportError
    with pytest.raises(E.OcrUnavailable):
        E._limits(5.0)
    with pytest.raises(E.OcrUnavailable):  # and a runner built on it fails closed the same way
        E.tesseract_runner(_script(tmp_path, "cat >/dev/null\n"))(b"png", 5.0)
