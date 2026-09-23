"""Tests for `application.citations`, the typed Indian-citation parsers (P3, LEG-PLAN-2026-09-23).

Fixtures marked MINED were extracted by regex sweep over real judgment text: the AIR/SCC/SCC-OnLine/SCR
strings come from `opennyaiorg/InJudgements` (`/home/ss/fusion-project/corpus-raw/hf/injudgements`,
shard0/shard1 `Text` column); the INSC strings come from a random sample of 2024 Supreme Court judgment PDFs
at `/home/ss/fusion-project/corpus-raw/supreme_court_judgments/2024/*.PDF` (`pypdf` text extraction, first 3
pages). Mining scripts are not committed (throwaway, scratchpad-only). No HC-neutral-citation string appears
in either corpus (InJudgements' High Court orders predate the 2023 neutral-citation circulars, and the SC
corpus is Supreme Court only) -- that fixture is built from the published Delhi HC neutral-citation circular
format instead and is marked SPEC, not MINED; flagged to the lead rather than silently treated as mined.
"""

from __future__ import annotations

from pravrudhi.application.citations import Citation, parse_citations


def test_air_mined() -> None:
    # MINED: InJudgements shard0.
    got = parse_citations("Reliance was placed on AIR 1958 SC 398 in support of the claim.")
    assert got == [Citation(reporter="AIR", year=1958, volume=None, page=398, court="SC", span=(23, 38))]


def test_air_named_court_mined() -> None:
    # MINED: InJudgements shard0 -- a non-SC AIR citation (state reporter series).
    got = parse_citations("the principle in AIR 1946 Mad 456 was followed")
    assert got == [Citation(reporter="AIR", year=1946, volume=None, page=456, court="Mad", span=(17, 33))]


def test_air_privy_council_mined() -> None:
    # MINED: InJudgements -- caught by the 200-citation recall check below (v1 of the AIR regex required
    # court to be Title-case, e.g. "Mad"/"Bom", and missed every all-caps "PC"/"FC" citation; this and the
    # next test pin the fix).
    got = parse_citations("In Ariffs case, AIR 1931 PC 79, above")
    assert got == [Citation(reporter="AIR", year=1931, volume=None, page=79, court="PC", span=(16, 30))]


def test_air_federal_court_mined() -> None:
    # MINED: InJudgements.
    got = parse_citations("Prasad v. Keshwar Lal, AIR 1941 FC 5 and")
    assert got == [Citation(reporter="AIR", year=1941, volume=None, page=5, court="FC", span=(23, 36))]


def test_air_two_letter_state_code_mined() -> None:
    # MINED: InJudgements -- the 200-citation recall check's remaining misses after the PC/FC fix were both
    # two-letter all-caps state reporter codes ("AIR 1997 MP 124", "AIR 1960 AP 359"), a different court-code
    # shape from the three-letter title-case ones ("Mad", "Bom") already covered.
    got = parse_citations("Tomar v. State of M.P. (AIR 1997 MP 124), Ni")
    assert got == [Citation(reporter="AIR", year=1997, volume=None, page=124, court="MP", span=(24, 39))]


def test_scc_mined() -> None:
    # MINED: InJudgements shard0.
    got = parse_citations("as held in (1994) 3 SCC 569, the appeal must fail")
    assert got == [Citation(reporter="SCC", year=1994, volume=3, page=569, court=None, span=(11, 27))]


def test_scc_no_space_before_scc_mined() -> None:
    # MINED: InJudgements shard0 -- real text has no space before the volume ("(2011)9 SCC 527").
    got = parse_citations("see (2011)9 SCC 527 for the test")
    assert got == [Citation(reporter="SCC", year=2011, volume=9, page=527, court=None, span=(4, 19))]


def test_scc_online_mined() -> None:
    # MINED: InJudgements shard0.
    got = parse_citations("reported at 2019 SCC OnLine Mad 13990")
    assert got == [
        Citation(reporter="SCC OnLine", year=2019, volume=None, page=13990, court="Mad", span=(12, 37))
    ]


def test_scc_online_pc_mined() -> None:
    # MINED: InJudgements shard0 -- Privy Council SCC OnLine cite.
    got = parse_citations("cf. 1907 SCC OnLine PC 9")
    assert got == [Citation(reporter="SCC OnLine", year=1907, volume=None, page=9, court="PC", span=(4, 24))]


def test_insc_mined() -> None:
    # MINED: 2024 SC judgment PDF sample.
    got = parse_citations("this Court in 2024 INSC 985 held that")
    assert got == [Citation(reporter="INSC", year=2024, volume=None, page=985, court="SC", span=(14, 27))]


def test_insc_embedded_newline_mined() -> None:
    # MINED: 2024 SC judgment PDF sample -- pypdf extraction breaks "2024 INSC 554" across a line, a real
    # wrinkle the parser must tolerate rather than a hand-invented edge case.
    got = parse_citations("as decided in 2024\nINSC 554 of that year")
    assert got == [Citation(reporter="INSC", year=2024, volume=None, page=554, court="SC", span=(14, 27))]


def test_scr_mined() -> None:
    # MINED: InJudgements shard0.
    got = parse_citations("reported at [1953] 2 SCR 603 on this point")
    assert got == [Citation(reporter="SCR", year=1953, volume=2, page=603, court=None, span=(12, 28))]


def test_scr_no_spaces_mined() -> None:
    # MINED: InJudgements shard0 -- real text with no internal spaces ("[1970]2SCR197").
    got = parse_citations("cf. [1970]2SCR197 for the older rule")
    assert got == [Citation(reporter="SCR", year=1970, volume=2, page=197, court=None, span=(4, 17))]


def test_scr_single_volume_mined() -> None:
    # MINED: InJudgements shard0 -- some SCR cites have no volume number at all ("[1975] SCR 930").
    got = parse_citations("see [1975] SCR 930")
    assert got == [Citation(reporter="SCR", year=1975, volume=None, page=930, court=None, span=(4, 18))]


def test_hc_neutral_spec() -> None:
    # SPEC (not mined -- see module docstring): Delhi HC neutral-citation circular format, YYYY:DHC:NNNN.
    got = parse_citations("in 2023:DHC:1234 the Court held")
    assert got == [Citation(reporter="HC-neutral", year=2023, volume=None, page=1234, court="DHC", span=(3, 16))]


def test_bns_bracket_tag_reuses_existing_cite_regex() -> None:
    # The existing [BNS/Section N] shape from nyaya.py's CITE regex, extended here to also parse into a
    # Citation so the same verify() path can handle both prose citations and corpus-tag citations.
    got = parse_citations("the element is met under [BNS/Section 103]")
    assert got == [Citation(reporter="BNS", year=None, volume=None, page=103, court=None, span=(25, 42))]


def test_bns_bracket_tag_with_subsection() -> None:
    got = parse_citations("[IPC/Section 34(b)]")
    assert got == [Citation(reporter="IPC", year=None, volume=None, page=34, court=None, span=(0, 19))]


def test_multiple_citations_same_text() -> None:
    got = parse_citations("compare AIR 1958 SC 398 with (1994) 3 SCC 569")
    assert [c.reporter for c in got] == ["AIR", "SCC"]


def test_no_citations() -> None:
    assert parse_citations("no citation appears in this sentence") == []


def test_malformed_air_missing_page_not_matched() -> None:
    # "AIR 1958 SC" with no page number is not a citation -- the parser must not guess a page.
    got = parse_citations("reliance on AIR 1958 SC generally")
    assert got == []
