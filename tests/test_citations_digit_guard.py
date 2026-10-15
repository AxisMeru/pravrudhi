"""A long digit run in pasted text must never raise: `int()` refuses more than 4,300 digits, and the citation patterns used to
capture an unbounded run as the page (or volume), so one pasted line made the intake route answer 500. The digit runs are bounded
now (a page at most nine digits, a volume at most four), so such a run is not a citation and the parser returns without raising.
Both arms: every reporter pattern with a poisoned run; the ordinary citations still parse exactly as before; the intake route
answers 200."""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient  # noqa: F401  (the route test builds its client in tests/test_intake_routes.py)

from pravrudhi.application.citations import parse_citations

RUN = "1" * 5000
HUGE = "1" * 100_000  # the intake route takes up to 200,000 characters

POISONED = [
    "(1992) 1 SCC " + RUN,
    "(1992) " + RUN + " SCC 5",
    "1992 SCC (1) " + RUN,
    "1992 (1) SCC " + RUN,
    "1992 Supp (1) SCC " + RUN,
    "AIR 1992 SC " + RUN,
    "1992 SCC OnLine Del " + RUN,
    "1992 INSC " + RUN,
    "[1992] 1 SCR " + RUN,
    "[1992] " + RUN + " SCR 5",
    "1992:DEL:" + RUN,
    "[Act/Section " + RUN + "]",
]


@pytest.mark.parametrize("text", POISONED, ids=[t[:24] for t in POISONED])
def test_a_long_digit_run_does_not_raise(text: str) -> None:
    parse_citations(text)
    parse_citations("1. The complainant relies on X v. Y " + text + " for the rule.")


@pytest.mark.parametrize("text", POISONED[:11], ids=[t[:24] for t in POISONED[:11]])
def test_a_long_digit_run_is_not_a_citation(text: str) -> None:
    assert parse_citations(text) == []


def test_a_section_tag_with_a_huge_number_has_page_zero_not_a_crash() -> None:
    (c,) = parse_citations("[Act/Section " + RUN + "]")
    assert c.page == 0


@pytest.mark.parametrize(
    ("text", "want"),
    [
        ("(1977) 3 SCC 247", ("SCC", 1977, 3, 247)),
        ("1996 SCC (3) 709", ("SCC", 1996, 3, 709)),
        ("1996 (3) SCC 709", ("SCC", 1996, 3, 709)),
        ("AIR 1950 SC 27", ("AIR", 1950, None, 27)),
        ("2019 SCC OnLine Del 8", ("SCC OnLine", 2019, None, 8)),
        ("2023 INSC 123", ("INSC", 2023, None, 123)),
        ("[1990] 2 SCR 100", ("SCR", 1990, 2, 100)),
        ("[1990] SCR 100", ("SCR", 1990, None, 100)),
        ("2023:DHC:4567", ("HC-neutral", 2023, None, 4567)),
        ("1992 Supp (1) SCC 335", ("SCC Supp", 1992, 1, 335)),
        ("(2014) 8 SCC 123456789", ("SCC", 2014, 8, 123456789)),  # the longest page kept: nine digits
        ("(2014) 8 SCC 1234", ("SCC", 2014, 8, 1234)),
    ],
)
def test_ordinary_citations_parse_exactly_as_before(text: str, want: tuple[str, int, int | None, int]) -> None:
    (c,) = parse_citations(text)
    assert (c.reporter, c.year, c.volume, c.page) == want


def test_a_ten_digit_page_is_not_a_citation_and_nine_is() -> None:
    assert parse_citations("(2014) 8 SCC 1234567890") == []
    assert len(parse_citations("(2014) 8 SCC 123456789")) == 1


def test_the_intake_route_answers_200_for_a_pasted_line_with_a_huge_page(tmp_path: Path) -> None:
    from tests.test_intake_routes import PLAIN, _client

    logging.disable(logging.CRITICAL)
    try:
        c = _client(Path(tempfile.mkdtemp()))
        text = (
            "1. The complainant relies on X v. Y (1992) 1 SCC "
            + RUN
            + " for the rule.\n\n2. The accused did not refund the deposit."
        )
        r = c.post(PLAIN, json={"text": text})
        assert r.status_code == 200, r.text[:200]
    finally:
        logging.disable(logging.NOTSET)


@pytest.mark.parametrize("text", POISONED, ids=[t[:24] for t in POISONED])
def test_a_100000_digit_run_is_handled_in_a_small_time_bound(text: str) -> None:
    import time

    huge = text.replace(RUN, HUGE)
    t = time.perf_counter()
    parse_citations(huge)
    parse_citations("x " * 5000 + huge + " y" * 5000)
    assert time.perf_counter() - t < 1.0


def test_extract_citations_and_the_route_do_not_raise_on_a_100000_digit_run() -> None:
    from pravrudhi.application.a0_assist import intake

    text = "1. The complainant relies on X v. Y (1992) 1 SCC " + HUGE + " for the rule."
    assert intake.extract_citations(text) == []


def test_the_intake_route_answers_200_for_each_family_with_a_100000_digit_run() -> None:
    from tests.test_intake_routes import PLAIN, _client

    logging.disable(logging.CRITICAL)
    try:
        c = _client(Path(tempfile.mkdtemp()))
        for family in [
            "(1992) 1 SCC ",
            "AIR 1992 SC ",
            "1992 INSC ",
            "[1992] 1 SCR ",
            "1992 SCC OnLine Del ",
            "1992:DEL:",
            "1992 Supp (1) SCC ",
        ]:
            text = (
                f"1. The complainant relies on X v. Y {family}{HUGE} for the rule.\n\n2. The accused did not refund the deposit."
            )
            r = c.post(PLAIN, json={"text": text})
            assert r.status_code == 200, (family, r.text[:120])
    finally:
        logging.disable(logging.NOTSET)


# ---- adjacent whitespace gaps (R2's finding on 3bba47ce: `_SCR` backtracked quadratically after "[1992]") ----
_CAP = 200_000  # IntakeRequest.text allows this many characters, and parse_citations runs over all of it
_PREFIXES = [
    "(1992)", "(1992) 1 SCC", "1992 SCC (1)", "1992 SCC", "1992 (1) SCC", "AIR 1992", "AIR 1992 SC", "1992 SCC OnLine",
    "1992 SCC OnLine Del", "1992 INSC", "[1992]", "[1992] 1", "[1992] 1 SCR", "1992:DEL:", "1992 Supp", "1992 Supp (1) SCC",
    "1992",
]  # fmt: skip


@pytest.mark.parametrize("prefix", _PREFIXES)
@pytest.mark.parametrize("gap", [" ", "\t", "\n", " \t\n"])
def test_a_long_whitespace_gap_after_any_citation_prefix_does_not_stall_the_parser(prefix: str, gap: str) -> None:
    import time

    text = prefix + gap * (_CAP // len(gap) // 2)
    t = time.perf_counter()
    parse_citations(text)
    parse_citations(text + "x")
    assert time.perf_counter() - t < 1.0, (prefix, repr(gap))
