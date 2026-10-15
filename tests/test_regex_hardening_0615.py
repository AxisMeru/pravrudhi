"""#476: `_PAGEFRAG` and `_TRAILING_HONORIFIC` rewritten without adjacent quantifiers. The old patterns are kept here
verbatim as the oracle: the new ones must give the same answer on every string of a random token mix, and must not stall on a
long whitespace run."""

import random
import re
import time

from pravrudhi.application.a0_assist.intake import _PAGEFRAG
from pravrudhi.application.verify import _TRAILING_HONORIFIC

_OLD_PAGEFRAG = re.compile(r"^\W*page\s*(?:no\.?)?\s*#?\W*(?:\d+(?:\s*(?:/|of)\s*\d+)?)?\W*$", re.I)
_OLD_HONORIFIC = re.compile(r"\s+(?:and|&)?\s*(?:anr\.?|ors\.?|others?|etc\.?)\s*$", re.IGNORECASE)

_PAGE_TOKENS = [
    "page",
    "Page",
    "PAGE",
    "pageno",
    " ",
    "  ",
    "\t",
    "\n",
    "no",
    "No",
    "NO",
    ".",
    "#",
    "-",
    ",",
    "1",
    "23",
    "/",
    "of",
    "OF",
    "x",
    "of 2",
]
_HON_TOKENS = [
    "Aldric Vennoway",
    "State",
    "and",
    "&",
    "And",
    "Ors",
    "Ors.",
    "anr",
    "Anr.",
    "others",
    "other",
    "etc",
    "etc.",
    " ",
    "  ",
    "\t",
    "\n",
    "x",
    ".",
    "&Ors",
]


def _random_strings(tokens, seed, n, longest=9):
    rng = random.Random(seed)
    for _ in range(n):
        yield "".join(rng.choice(tokens) for _ in range(rng.randint(0, longest)))


def test_pagefrag_gives_the_same_answer_as_the_old_pattern_on_random_token_strings():
    hits = 0
    for t in _random_strings(_PAGE_TOKENS, 476, 120_000):
        old, new = bool(_OLD_PAGEFRAG.match(t)), bool(_PAGEFRAG.match(t))
        assert old == new, repr(t)
        hits += old
    assert hits > 500  # the comparison is not vacuous: many of the strings are real page stamps


def test_pagefrag_still_matches_the_page_stamps_it_was_written_for():
    for t in ["Page 3", "page 3 of 12", "PAGE NO. 4", "- page no 7 -", "page", "Page #5", "page 3/12", "pageno 2"]:
        assert _PAGEFRAG.match(t), t
    for t in ["page 3 of the judgment", "the page 3", "pages 3", "page no. x", "page 3 4"]:
        assert not _PAGEFRAG.match(t), t


def test_trailing_honorific_gives_the_same_substitution_as_the_old_pattern():
    changed = 0
    for t in _random_strings(_HON_TOKENS, 4760, 120_000, longest=7):
        old, new = _OLD_HONORIFIC.sub("", t), _TRAILING_HONORIFIC.sub("", t)
        assert old == new, repr(t)
        changed += old != t
    assert changed > 500


def test_a_long_whitespace_run_does_not_stall_either_pattern():
    n = 200_000
    for text in ("page" + " " * n + "x", "page" + " " * n + "no" + " " * n + "x", " " * n + "x"):
        t0 = time.perf_counter()
        _PAGEFRAG.match(text)
        assert time.perf_counter() - t0 < 1.0
    for text in ("a" + " " * n + "b", "a" + " " * n + "and" + " " * n + "b", " " * n + "Ors" + "x", "a" + " " * n):
        t0 = time.perf_counter()
        _TRAILING_HONORIFIC.sub("", text)
        assert time.perf_counter() - t0 < 1.0
