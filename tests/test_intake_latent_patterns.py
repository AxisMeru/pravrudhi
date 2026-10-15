"""Three text patterns rewritten without adjacent quantifiers (header-furniture and party-separator checks). The earlier
patterns are kept verbatim below as oracles: each new pattern must give the same answer on every string of a random token mix,
and the rewritten ones must stay fast on long runs."""

import itertools
import random
import re
import time

from pravrudhi.application.a0_assist import intake
from pravrudhi.application.verify import _PARTY_SEPARATOR

_OLD_NEUTRAL = re.compile(
    r"\b(?:19|20)\d\d\s*:\s*[A-Za-z]+(?:\s*[-:]\s*[A-Za-z]+)*\s*:?\s*\d+|\bNC\s*:|\bCRL\.?\s*[A-Z]*\.?\s*NO", re.I
)
_OLD_PARTY_SEPARATOR = re.compile(r"\s+(?:v\.?|vs\.?|versus)\s+", re.IGNORECASE)

_CT = (
    r"(?:CRL|CRIMINAL|CRI|CR|CRLA|CRLP|CRLRC|CRLMP|CRP|WP|WA|OP|MC|SB|RFA|RSA|SA|CMP|MP|APPEAL|PETITION|REVISION|MISC|CASE)"
    r"(?:\.[A-Z]+)*\.?"
)
_YR = r"(?:19|20)\d\d"
_CASE_NO = rf"\d{{1,6}}(?:\s*(?:/|OF)\s*{_YR})?"
_OLD_CASE_STAMP = (
    rf"(?:\([A-Z]{{1,4}}\)\s*\.?\s*)?{_CT}(?:[ .\-/]*{_CT})*[ .]*(?:NOS?\.?)?[ .]*{_CASE_NO}"
    rf"(?:\s*(?:AND|&|,)\s*{_CASE_NO})*[ .]*"
)
_OLD_PAGE_STAMP = r"(?:PAGE|PG\.?)\s*(?:NO\.?)?\s*\d+(?:\s*(?:OF|/)\s*\d+)?(?:\s*//\s*\d+\s*//)?"
_NC_STAMP = rf"NC\s*:\s*{_YR}\s*:\s*[A-Z]+\s*:\s*\d+"
_NUM_PREFIX = r"[-–—:/ ]*\d{1,3}[-–—:/ ]*"
_OLD_BRACKET_STAMP = rf"\([A-Z]{{1,4}}\)\s*\.?\s*NOS?\.?\s*{_CASE_NO}"
_TAIL = r"(?:\s+\d{1,3})?"
_OLD_STAMP = re.compile(
    rf"^(?:{_OLD_PAGE_STAMP}|(?:{_NUM_PREFIX})?(?:{_NC_STAMP}\s*)?(?:{_OLD_CASE_STAMP}|{_OLD_BRACKET_STAMP})?{_TAIL}"
    rf"|https?://\S+(?:\s+(?:{_OLD_CASE_STAMP}|{_OLD_BRACKET_STAMP}))?{_TAIL}|www\.\S+{_TAIL})$",
    re.I,
)

_NEUTRAL_TOKENS = ["2020", "19", ":", " ", "  ", "\t", "-", "a", "ab", "NC", "CRL", ".", "NO", "No", "A", "12", "x", "\n"]
_SEP_TOKENS = ["v", "v.", "vs", "vs.", "versus", "Versus", " ", "  ", "\t", "\n", " ", "a", "x", ".", "1"]
_STAMP_TOKENS = [
    "PAGE", "PG", "PG.", "NO", "NO.", "NOS", "NOS.", "(CRL)", "(WP)", "CRL", "WP", "CRL.A.", "MISC", ".", " ", "  ", "\t", "-",
    "/", "//", "1", "23", "456", "2020", "OF", "AND", "&", ",", "NC", ":", "A", "https://x", "www.x", "x",
]  # fmt: skip


def _strings(tokens, seed, n, longest):
    rng = random.Random(seed)
    for _ in range(n):
        yield "".join(rng.choice(tokens) for _ in range(rng.randint(0, longest)))


def _check(old, new, tokens, search):
    hits = 0
    run = (lambda p, s: p.search(s)) if search else (lambda p, s: p.match(s))
    for s in itertools.chain((a + b + c for a, b, c in itertools.product(tokens, repeat=3)), _strings(tokens, 946, 150_000, 11)):
        o = bool(run(old, s))
        assert o == bool(run(new, s)), repr(s)
        hits += o
    assert hits > 500  # not vacuous


def test_the_neutral_citation_check_gives_the_same_answer_as_the_earlier_pattern():
    _check(_OLD_NEUTRAL, intake._NEUTRAL, _NEUTRAL_TOKENS, search=True)


def test_the_party_separator_gives_the_same_answer_as_the_earlier_pattern():
    _check(_OLD_PARTY_SEPARATOR, _PARTY_SEPARATOR, _SEP_TOKENS, search=True)


def test_the_header_stamp_gives_the_same_answer_as_the_earlier_pattern():
    _check(_OLD_STAMP, intake._STAMP, _STAMP_TOKENS, search=False)


def test_the_rewritten_patterns_stay_fast_on_long_runs():
    n = 200_000
    for text in (
        "CRL" + " " * n + "x",
        "CRL." + " " * n + "NO",
        "2020:a" + " " * n + "x",
        "2020:a" + " " * n + ":" + " " * n + "1",
    ):
        t0 = time.perf_counter()
        intake._NEUTRAL.search(text)
        assert time.perf_counter() - t0 < 1.0
    for text in (" " * n + "x", " " * n + "v" + " " * n, " " * n + "versus"):
        t0 = time.perf_counter()
        _PARTY_SEPARATOR.search(text)
        assert time.perf_counter() - t0 < 1.0
    for text in ("PAGE" + " " * n + "x", "PAGE NO." + " " * n + "1", "(CRL)" + " " * n + "x", "(CRL)." + " " * n + "NO. 1"):
        t0 = time.perf_counter()
        intake._STAMP.match(text)
        assert time.perf_counter() - t0 < 1.0


def test_a_long_line_is_never_given_to_the_stamp_pattern():
    """The stamp check returns before the pattern on a long line, whatever the pattern's own cost."""
    n = 200_000
    for text in ("1" + " " * n + "x", "CRL 1" + " " * n + "x", "." * n):
        t0 = time.perf_counter()
        assert intake._is_stamp(text) is False
        assert time.perf_counter() - t0 < 1.0
