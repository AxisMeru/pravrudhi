"""#1015 PR 1: the pure core of scanned/garbled-page intake: tesseract TSV parsing, the page floor, the garbled-layer detector
and the subprocess runner. Pure functions (a fake runner in tests); nothing here is wired into a route (PR 2) and nothing runs
while the flag `wf_c1_scanned_intake` is off. Settings follow Track A's calibration (300 dpi gray, `--psm 3`, tsv, English
only). A page below the floor is NEVER guessed: it has no text."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from bisect import bisect_right
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from pravrudhi.application.scanned_intake_config import ScannedIntakeConfig

TESSERACT_ENV = "PRAVRUDHI_TESSERACT_BIN"
ARGS = ("--psm", "3", "-l", "eng", "tsv")  # stdin -> stdout; the same settings as the calibration


class OcrUnavailable(Exception):
    """No tesseract binary, or it failed to start or exited non-zero: the page's reason is `ocr_unavailable`."""


class OcrTimeout(Exception):
    """The page took longer than the configured timeout: the page's reason is `timeout`."""


@dataclass(frozen=True)
class Word:
    text: str
    conf: float
    left: int
    top: int
    width: int
    height: int
    block: int = 0
    par: int = 0
    line: int = 0


Runner = Callable[[bytes, float], str]  # (png bytes, timeout seconds) -> the tsv text


def parse_tsv(tsv: str) -> list[Word]:
    """The recognised words of a tesseract TSV (level-5 rows with a real confidence and some text); anything else is skipped."""
    words: list[Word] = []
    for line in tsv.splitlines()[1:]:
        cols = line.split("\t")
        if len(cols) < 12 or cols[0] != "5":
            continue
        try:
            conf = float(cols[10])
            box = (int(cols[6]), int(cols[7]), int(cols[8]), int(cols[9]))
        except ValueError:
            continue
        text = cols[11].strip()
        if conf < 0 or not text:
            continue
        words.append(Word(text, conf, *box, block=int(cols[2]), par=int(cols[3]), line=int(cols[4])))
    return words


@dataclass(frozen=True)
class PageStats:
    n_words: int
    mean_conf: float
    low_word_share: float
    ink_coverage: float


# a page with more dark pixels than this is refused as too heavy (reason `timeout`), never scanned at length
MAX_DARK_PIXELS = 4_000_000


def _row_intervals(words: list[Word]) -> dict[int, list[tuple[int, int]]]:
    """Per pixel row, the merged x intervals covered by word boxes (so a pixel is tested by one bisect, not by every box)."""
    raw: dict[int, list[tuple[int, int]]] = {}
    for w in words:
        for y in range(w.top, w.top + w.height):
            raw.setdefault(y, []).append((w.left, w.left + w.width))
    merged: dict[int, list[tuple[int, int]]] = {}
    for y, ivs in raw.items():
        ivs.sort()
        out: list[tuple[int, int]] = []
        for a, b in ivs:
            if out and a <= out[-1][1]:
                out[-1] = (out[-1][0], max(out[-1][1], b))
            else:
                out.append((a, b))
        merged[y] = out
    return merged


def ink_coverage(dark_pixels: Iterable[tuple[int, int]], words: list[Word], limit: int = MAX_DARK_PIXELS) -> float:
    """Share of the page's dark pixels that lie inside a recognised word's box. 0.0 when the page has no dark pixel
    (nothing to read)."""
    rows = _row_intervals(words)
    total = inside = 0
    for x, y in dark_pixels:
        total += 1
        if total > limit:
            raise OcrTimeout  # bounded work: a page this heavy is refused, not scanned for minutes
        ivs = rows.get(y)
        if ivs:
            i = bisect_right(ivs, (x, 1 << 60)) - 1
            if i >= 0 and ivs[i][0] <= x < ivs[i][1]:
                inside += 1
    return inside / total if total else 0.0


def page_stats(words: list[Word], coverage: float, low_word_confidence: float) -> PageStats:
    if not words:
        return PageStats(0, 0.0, 1.0, coverage)
    low = sum(1 for w in words if w.conf < low_word_confidence)
    return PageStats(len(words), sum(w.conf for w in words) / len(words), low / len(words), coverage)


def decide(stats: PageStats, cfg: ScannedIntakeConfig) -> tuple[bool, str | None]:
    """(readable, reason). A page with no words, mean confidence under the floor, ink coverage under the floor or too
    large a share
    of low-confidence words is unreadable with reason `low_confidence`; the page then contributes NO text."""
    if (
        stats.n_words == 0
        or stats.mean_conf < cfg.min_page_confidence
        or stats.ink_coverage < cfg.min_ink_coverage
        or stats.low_word_share > cfg.max_low_word_share
    ):
        return False, "low_confidence"
    return True, None


_INDIC = re.compile("[ऀ-෿]")
_ODD_OK = re.compile(r"[A-Za-z0-9.,;:()'\"/\-]*")


def _tokens(text: str) -> list[str]:
    return text.split()


def odd_token_share(text: str) -> float:
    """Share of tokens with a character outside [A-Za-z0-9.,;:()'"/-] and outside the Indic blocks (Track A's detector)."""
    toks = _tokens(text)
    if not toks:
        return 0.0
    odd = sum(1 for t in toks if not _ODD_OK.fullmatch(_INDIC.sub("", t)))
    return odd / len(toks)


def letter_share(text: str) -> float:
    """Letters over non-space characters (Researcher's second signal); Indic letters count as letters."""
    chars = [c for c in text if not c.isspace()]
    return sum(1 for c in chars if c.isalpha()) / len(chars) if chars else 1.0


def is_indic_page(text: str) -> bool:
    """A page that is mostly Indic script stays text_layer: English OCR cannot read it."""
    chars = [c for c in text if not c.isspace()]
    return bool(chars) and sum(1 for c in chars if _INDIC.match(c)) / len(chars) > 0.3


def layer_is_garbled(text: str, cfg: ScannedIntakeConfig) -> bool:
    """True when a text-layer page should be OCR'd instead: enough characters to judge, not an Indic page, and either
    signal trips."""
    if len([c for c in text if not c.isspace()]) < cfg.garbled_min_chars or is_indic_page(text):
        return False
    return odd_token_share(text) >= cfg.garbled_max_odd_token_share or letter_share(text) < cfg.garbled_min_letter_share


def similarity(a: str, b: str) -> float:
    """Word-set Jaccard similarity of two page texts (1.0 identical, 0.0 nothing shared); used for the OCR-agreement check."""
    wa, wb = set(a.lower().split()), set(b.lower().split())
    return len(wa & wb) / len(wa | wb) if wa | wb else 1.0


def ocr_replaces_layer(layer_text: str, ocr_text: str, cfg: ScannedIntakeConfig) -> bool:
    """A flagged layer is replaced only when the OCR text DISAGREES with it (similarity below the config value); when they
    agree the
    layer is kept (the page was not really garbled)."""
    return similarity(layer_text, ocr_text) < cfg.agreement_min_similarity


def child_env() -> dict[str, str]:
    """A minimal environment for the tesseract child: no inherited secrets, one thread, a fixed locale."""
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "OMP_THREAD_LIMIT": "1", "LC_ALL": "C"}
    if os.environ.get("TESSDATA_PREFIX"):
        env["TESSDATA_PREFIX"] = os.environ["TESSDATA_PREFIX"]
    return env


def tesseract_runner(binary: str | None = None) -> Runner:
    """The real runner: argv list (no shell), the PNG on stdin, a hard timeout. Raises OcrUnavailable / OcrTimeout."""
    exe = binary or os.environ.get(TESSERACT_ENV) or shutil.which("tesseract")
    if not exe:
        raise OcrUnavailable("no tesseract binary")

    def run(png: bytes, timeout_s: float) -> str:
        try:
            p = subprocess.run(  # noqa: S603
                [exe, "stdin", "stdout", *ARGS], input=png, capture_output=True, timeout=timeout_s, check=False, env=child_env()
            )
        except subprocess.TimeoutExpired:
            raise OcrTimeout from None
        except OSError as e:
            raise OcrUnavailable(type(e).__name__) from None
        if p.returncode != 0:
            raise OcrUnavailable(f"tesseract exited {p.returncode}")
        return p.stdout.decode("utf-8", "replace")

    return run


def words_to_text(words: list[Word]) -> str:
    """The page text: words of a line joined by a space, lines by a newline, paragraphs by a blank line (A2 cuts spans there)."""
    out: list[str] = []
    prev: tuple[int, int, int] | None = None
    for w in words:
        key = (w.block, w.par, w.line)
        if prev is None:
            out.append(w.text)
        elif key[:2] != prev[:2]:
            out.append("\n\n" + w.text)
        elif key != prev:
            out.append("\n" + w.text)
        else:
            out.append(" " + w.text)
        prev = key
    return "".join(out)
