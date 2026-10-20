"""A1 whole-draft citation check (wf_a1_draft_citation_check): the pure parts. Invented parties; real-shaped years and
multi-paragraph drafts.

Every rule has both arms. The acceptance tests are the parser-drop cases: a draft whose citations the parser cannot read must
never come back as "no problems" without saying how much was not read."""

from __future__ import annotations

import base64
from typing import Any

import pytest

from pravrudhi.application import draft_check as D
from pravrudhi.application.draft_check_config import DraftCheckConfig, parse_draft_check_config

CFG = DraftCheckConfig()
GOOD = "(2014) 8 SCC 512"  # a citation the fake index "has" (real-shaped year; the parties in these tests are invented)


def lookup(text: str) -> tuple[str, bool]:
    """A fake index: only GOOD exists; a typed name is "compared" and a name other than Aldric Vennoway mismatches."""
    if "512" in text:
        if " v. " in text:
            return ("IN_INDEX", True) if "Vennoway" in text else ("NAME_MISMATCH", True)
        return "IN_INDEX", False
    if "777" in text:
        return "CONFLICT", False
    return "NOT_IN_INDEX", False


def run(text: str, cfg: DraftCheckConfig = CFG, look: Any = lookup) -> dict[str, Any]:
    out = D.check_draft(text, cfg, look)["draft_check"]
    d = out["denominator"]
    # the invariants hold for EVERY input
    assert d["like_strings"] == d["read"] + d["not_read"]
    assert d["read"] == d["found"] + d["not_found"] + d["conflict"] + d["name_mismatch"] + d["not_checked"]
    assert len(out["citations"]) == d["read"] and len(out["not_read"]) == d["not_read"]
    if d["not_read"] or d["pages_not_read"]:
        assert out["state"] in ("incomplete", "nothing_checked")
    return out  # type: ignore[no-any-return]


# ---- the setting: fail closed -----------------------------------------------------------------------------------------------


@pytest.mark.parametrize("flag", [True])
def test_only_the_boolean_true_turns_the_check_on(flag: Any) -> None:
    assert parse_draft_check_config({"wf_a1_draft_citation_check": flag}) == DraftCheckConfig()


@pytest.mark.parametrize("flag", [False, None, "true", "True", "yes", "on", 1, 0, "1", [], {}])
def test_every_other_spelling_of_the_flag_is_off(flag: Any) -> None:
    assert parse_draft_check_config({"wf_a1_draft_citation_check": flag}) is None


@pytest.mark.parametrize("body", [None, {}, [], "x", 5, {"other": True}])
def test_a_missing_or_foreign_body_is_off(body: Any) -> None:
    assert parse_draft_check_config(body) is None


def test_caps_are_read_and_a_malformed_cap_turns_the_check_off_not_the_engine() -> None:
    cfg = parse_draft_check_config(
        {"wf_a1_draft_citation_check": True, "draft_check_max_citations": 20, "draft_check_max_seconds": 5}
    )
    assert cfg is not None and cfg.max_citations == 20 and cfg.max_seconds == 5.0
    for bad in ({"draft_check_max_citations": 0}, {"draft_check_max_citations": "20"}, {"draft_check_max_citations": True},
                {"draft_check_max_seconds": 0}, {"draft_check_max_seconds": float("nan")}, {"draft_check_max_pdf_pages": 10**9}):
        assert parse_draft_check_config({"wf_a1_draft_citation_check": True, **bad}) is None


# ---- the acceptance tests: the parser-drop cases ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "text"),
    [
        ("line_break_hyphen", "as held in (2014) 8 S-\nCC 512 the court said"),
        ("ocr_digit_in_year", "as held in (2O14) 8 SCC 512 the court said"),
        ("ocr_digit_in_page", "as held in (2014) 8 SCC 5l2 the court said"),
        ("zero_width_character", "as held in (2014) 8 S​CC 512 the court said"),
        ("manu", "as held in MANU/SC/0123/2014 the court said"),
        ("cri_lj_damaged", "as held in 2014 Cri L-\nJ 1234 the court said"),
        ("soft_hyphen", "as held in (2014) 8 S­CC 512 the court said"),
        ("dotted_scc_damaged", "as held in (2014) 8 S.C.\nC. 5l2 the court said"),
    ],
)
def test_a_citation_the_parser_cannot_read_is_listed_as_not_read_and_nothing_is_checked(name: str, text: str) -> None:
    out = run(text)
    d = out["denominator"]
    assert (d["read"], d["not_read"], d["checked"]) == (0, 1, 0), name
    assert out["state"] == "nothing_checked" and out["not_read"][0]["reason"] == "form_not_read"
    assert out["summary"].startswith("No citation could be checked")
    assert "1 string that looks like a citation was not read" in out["summary"] and out["summary"].endswith("Existence only.")


def test_a_draft_that_reads_and_checks_everything_is_complete_and_says_it_is_existence_only() -> None:
    out = run(f"The decision in {GOOD} is cited.\n\nIt is cited again in (2016) 2 SCC 512.")
    assert out["state"] == "complete" and out["denominator"]["not_read"] == 0 and out["denominator"]["found"] == 2
    assert out["summary"] == (
        "All 2 citations checked were found in our index. Existence only: a citation found in our index is not confirmed to say "
        "what the draft says."
    )


def test_one_readable_and_one_unreadable_citation_are_counted_apart_even_when_close() -> None:
    out = run(f"see {GOOD}, (2O14) 8 SCC 512 and again later")
    d = out["denominator"]
    assert (d["read"], d["not_read"], d["like_strings"], d["checked"]) == (1, 1, 2, 1)
    assert out["state"] == "incomplete"
    assert "1 string that looks like a citation was not read" in out["summary"]


def test_a_manu_and_a_cri_lj_next_to_a_good_citation_are_not_swallowed_by_it() -> None:
    out = run(f"see MANU/SC/0123/2014 and 2014 Cri L-\nJ 1234 and {GOOD} together")
    assert out["denominator"]["read"] == 1 and out["denominator"]["not_read"] == 2
    assert "2 strings that look like a citation were not read" in out["summary"]


def test_words_that_only_resemble_a_reporter_are_not_citations() -> None:
    out = run("The air was heavy; scr and scc are lower-case words; AIR India and SCALE models were discussed at length.")
    assert out["denominator"]["like_strings"] == 0 and out["state"] == "none_recognised"


def test_no_recognised_form_says_what_was_looked_for_and_is_never_complete() -> None:
    out = run("The Supreme Court Cases, volume 8, page 273, held the point, and the case in Hindi text is cited in the margin.")
    assert out["state"] == "none_recognised"
    expect = "No citation in a form this tool recognises was found; that does not mean the draft cites nothing"
    assert out["summary"].startswith(expect)
    assert "SCC" in out["summary"] and "AIR" in out["summary"]
    assert out["forms"]["checked"] == ["SCC", "SCC Supp"] and "DLT" in out["forms"]["recognised_not_checked"]


# ---- every citation-like string is counted: a real-shaped draft of High Court and other reporters (B2) ------------------------

DRAFT_HC = """IN THE HIGH COURT OF DELHI AT NEW DELHI

1. The petitioner relies on the decision in Aldric Vennoway v. State of Zorbland, (2012) 190 DLT 45, and on ILR (2009) 2 Del 34.

2. Reference is also made to 2008 (2) KLT 123, 1999 (4) Bom CR 123, 2007 (1) Mad LJ 88 and 2011 (4) Cal LJ 12.

3. See also 2015 (2) All LJ 5; 2013 (3) RCR (Cri) 77; 2014 (1) JCC 5; (2014) 2 GLR 55; (2014) 8 SC 273 and (2014) 8 S.C.C. 273.
"""


def test_a_brief_of_high_court_citations_reads_n_like_citations_zero_checked_never_none_found() -> None:
    out = run(DRAFT_HC)
    d = out["denominator"]
    assert (d["like_strings"], d["read"], d["not_read"], d["checked"]) == (12, 12, 0, 0)
    assert (d["not_checked_reporter_not_in_index"], d["not_checked_spelling"]) == (10, 2)
    assert out["state"] == "nothing_checked"
    assert out["summary"].startswith("No citation could be checked (12 read)")
    assert "10 citations are of a kind the index does not hold" in out["summary"]
    assert "2 possible SCC citations in a non-standard spelling; not checked" in out["summary"]
    assert all(c["status"] == "not_checked" for c in out["citations"])
    assert sorted(c["reason"] for c in out["citations"]).count("unusual_spelling") == 2
    assert "No citation was found" not in out["summary"]


@pytest.mark.parametrize(
    "cite",
    [
        "(2012) 190 DLT 45", "ILR (2009) 2 Del 34", "2008 (2) KLT 123", "1999 (4) Bom CR 123", "2007 (1) Mad LJ 88",
        "2011 (4) Cal LJ 12", "2015 (2) All LJ 5", "2013 (3) RCR (Cri) 77", "2014 (1) JCC 5", "(2014) 2 GLR 55",
        "A.I.R. 2014 SC 1234", "AIR 2014 SC 1234", "2014 SCC OnLine Del 99",
    ],
)
def test_each_other_reporter_form_is_read_and_not_checked(cite: str) -> None:
    out = run(f"The point was decided in {cite} and nowhere else.")
    d = out["denominator"]
    assert (d["read"], d["not_read"], d["not_checked_reporter_not_in_index"]) == (1, 0, 1), cite
    assert out["citations"][0]["text"] == cite


@pytest.mark.parametrize(
    "cite",
    [
        "AIR 2001 SUPREME COURT 1456", "AIR2001SC1456", "(2005) AIR SCW 5560", "2013 SCC online Del 3120", "1982 SCC (L&S) 272",
        "1963 SCR Supl. (1) 599", "2001 (1) SCC (Cr.) 410", "2005 Cri. L. J. 299", "2019 SCC Online Bom 1857",
        "1988 SCR (2) 401", "JT 1987 (4) 665", "1955 SCR (1)1083",
    ],
)
def test_forms_seen_in_real_judgment_text_are_read_and_not_checked(cite: str) -> None:
    out = run(f"The point was decided in {cite} and nowhere else.")
    d = out["denominator"]
    assert (d["read"], d["not_read"]) == (1, 0), cite
    assert out["citations"][0]["text"] == cite and out["citations"][0]["status"] == "not_checked"


@pytest.mark.parametrize(
    "pin",
    ["(1990) 3 SCC 1, SCC p. 238, para 12", "AIR 2001 SC 144, AIR p. 1370, para 10", "(1990) 3 SCC 1 (SCC pp. 147-48, para 11)"],
)
def test_a_pinpoint_into_a_case_already_cited_is_not_a_second_citation(pin: str) -> None:
    out = run(pin)
    assert out["denominator"]["not_read"] == 0 and out["denominator"]["read"] == 1, pin


@pytest.mark.parametrize("cite", ["(2014) 8 SC 273", "(2014) 8 S.C.C. 273", "[2014] 8 SCC 273"])
def test_scc_in_an_unusual_spelling_is_read_and_reported_as_such_not_as_another_reporter(cite: str) -> None:
    out = run(f"The point was decided in {cite} and nowhere else.")
    d = out["denominator"]
    assert (d["read"], d["not_checked_spelling"], d["not_checked_reporter_not_in_index"]) == (1, 1, 0)
    assert out["citations"][0]["reason"] == "unusual_spelling" and out["state"] == "nothing_checked"
    assert "1 possible SCC citation in a non-standard spelling; not checked" in out["summary"]
    assert "AIR, SCC OnLine" not in out["summary"]


def test_a_form_the_lookup_calls_malformed_is_counted_not_read_in_its_own_bucket() -> None:
    def look(t: str) -> tuple[str, bool]:
        return "MALFORMED", False

    out = run("The point was decided in (2016)10SCC386 and nowhere else.", look=look)
    d = out["denominator"]
    assert (d["read"], d["not_read"], d["not_read_could_not_be_read_as_a_citation"], d["not_checked"]) == (0, 1, 1, 0)
    assert (d["not_checked_lookup_failed"], d["like_strings"]) == (0, 1)
    assert out["citations"] == [] and out["not_read"][0]["reason"] == "could_not_be_read_as_a_citation"
    assert out["state"] == "nothing_checked" and "lookup failure" not in out["summary"]
    assert "1 string that looks like a citation was not read" in out["summary"]


def test_the_summary_grammar_is_right_for_one_and_for_many() -> None:
    one = run(f"see (2015) 8 SCC 999 and {GOOD}")["summary"]
    assert one.startswith("1 of the 2 citations checked needs attention")
    many = run("see (2015) 8 SCC 999 and (2016) 8 SCC 998 and (2017) 8 SCC 997")["summary"]
    assert many.startswith("3 of the 3 citations checked need attention")
    assert run("see (2015) 8 SCC 999")["summary"].startswith("1 of the 1 citation checked needs attention")


def test_a_damaged_high_court_citation_is_not_read() -> None:
    out = run("The point was decided in (2012) 190 D-\nLT 45, which the court followed.")
    assert out["denominator"]["not_read"] == 1 and out["state"] == "nothing_checked"


def test_a_mixed_brief_is_incomplete_and_names_what_the_index_does_not_hold() -> None:
    text = (
        "1. The petitioner relies on (2014) 8 SCC 512 and (2016) 2 SCC 999.\n\n"
        "2. The High Court followed 2008 (2) KLT 123 and AIR 2015 SC 44, and cited MANU/SC/0123/2014.\n"
    )
    out = run(text)
    d = out["denominator"]
    assert (d["read"], d["not_read"], d["checked"], d["not_checked"]) == (4, 1, 2, 2)
    assert out["state"] == "incomplete"
    s = out["summary"]
    assert s.startswith("1 of the 2 citations checked needs attention: 1 not found in our index")
    assert "2 citations are of a kind the index does not hold" in s and "1 string that looks like a citation was not read" in s
    assert s.endswith("is not confirmed to say what the draft says.") and "typed beside" not in s


def test_an_all_air_draft_is_nothing_checked_not_complete() -> None:
    out = run("Held in AIR 1973 SC 1461 and AIR 2014 SC 1234, both after the Constitution Bench.")
    assert out["state"] == "nothing_checked" and out["checked"] == {"checked": 0, "checkable": 0, "read": 2}
    assert "No problems" not in out["summary"] and "complete" not in out["state"]
    assert out["summary"].startswith("No citation could be checked (2 read)")


# ---- the denominator and the report -----------------------------------------------------------------------------------------


def test_every_status_is_counted_and_listed() -> None:
    text = f"{GOOD}; (2014) 8 SCC 999; (2014) 8 SCC 777; AIR 2014 SC 1234."
    out = run(text)
    d = out["denominator"]
    got = (d["found"], d["not_found"], d["conflict"], d["not_checked"], d["not_checked_reporter_not_in_index"])
    assert got == (1, 1, 1, 1, 1)
    assert [c["status"] for c in out["citations"]] == ["found", "not_found", "conflict", "not_checked"]
    assert out["state"] == "incomplete"  # one citation is of a kind the index does not hold: never "complete"
    expect = "2 of the 3 citations checked need attention: 1 not found in our index, 1 could not be matched to one case, 0 with"
    assert out["summary"].startswith(expect)
    assert "1 citation is of a kind the index does not hold" in out["summary"]


def test_an_air_or_scc_online_citation_is_not_checked_never_not_found() -> None:
    out = run("see AIR 2014 SC 1234 and 2014 SCC OnLine SC 99 here")
    assert [c["status"] for c in out["citations"]] == ["not_checked", "not_checked"]
    assert all(c["reason"] == "reporter_not_in_index" for c in out["citations"])
    assert out["denominator"]["not_found"] == 0


def test_a_typed_name_that_does_not_match_is_a_mismatch_and_a_matching_one_is_confirmed() -> None:
    out = run(f"as held in Aldric Vennoway v. State of Zorbland, {GOOD}; and in Brandel Thackeray v. Union of Zorbland, {GOOD}.")
    assert [c["status"] for c in out["citations"]] == ["found", "name_mismatch"]
    assert [c["name_state"] for c in out["citations"]] == ["name_matched", "name_mismatch"]
    assert "1 with a case name that did not match" in out["summary"]


def test_a_citation_with_no_readable_name_is_found_but_the_name_is_not_read() -> None:
    out = run(f"as the court said, {GOOD}.")
    c = out["citations"][0]
    assert c["status"] == "found" and c["name"] is None and c["name_state"] == "name_not_read"
    assert out["denominator"]["found_name_not_confirmed"] == 1
    assert "case name" not in out["summary"]  # no name was typed: nothing is said about names


def test_a_repeated_citation_is_looked_up_once_and_answered_alike() -> None:
    calls: list[str] = []

    def look(t: str) -> tuple[str, bool]:
        calls.append(t)
        return "IN_INDEX", False

    out = run(f"{GOOD}; again {GOOD}; and once more {GOOD}.", look=look)
    assert len(calls) == 1 and out["denominator"]["looked_up"] == 1 and out["denominator"]["found"] == 3


def test_parallel_citations_share_a_group_and_a_quoted_citation_is_flagged() -> None:
    out = run(f'{GOOD} : AIR 2014 SC 1234 : 2014 SCC OnLine SC 99. He said "in (2013) 1 SCC 1 it was held" and stopped.')
    g = [c["group"] for c in out["citations"]]
    assert g[0] == g[1] == g[2] and g[0] is not None and g[3] is None
    assert [c["quoted"] for c in out["citations"]] == [False, False, False, True]
    assert out["denominator"]["quoted"] == 1


def test_a_straight_double_quote_after_a_digit_is_an_inch_mark_not_a_quotation() -> None:
    out = run(f'The cheque was 6" wide. As held in {GOOD} the court said so.')
    assert out["citations"][0]["quoted"] is False


def test_a_wrong_year_with_a_real_page_is_not_found_not_fuzzy_matched() -> None:
    out = run("see (2015) 8 SCC 999 here")  # the fake index, like the real one, answers by the whole key
    assert out["citations"][0]["status"] == "not_found"


def test_a_no_index_deployment_says_nothing_was_checked() -> None:
    out = run(f"see {GOOD}", look=None)
    assert out["citations"][0]["status"] == "not_checked" and out["citations"][0]["reason"] == "no_index"
    assert out["state"] == "nothing_checked" and out["denominator"]["not_checked_no_index"] == 1
    assert "(no citation index is available here)" in out["summary"]


def test_a_failing_lookup_is_one_not_checked_and_the_rest_still_run() -> None:
    def look(t: str) -> tuple[str, bool]:
        if "777" in t:
            raise RuntimeError("index busy")
        return lookup(t)

    out = run(f"{GOOD}; (2014) 8 SCC 777; (2014) 8 SCC 999", look=look)
    assert [c["status"] for c in out["citations"]] == ["found", "not_checked", "not_found"]
    assert out["citations"][1]["reason"] == "lookup_failed" and out["state"] == "incomplete"


def test_the_count_cap_reports_the_numbers_and_never_drops_a_citation() -> None:
    cfg = DraftCheckConfig(max_citations=2)
    text = " ".join(f"(2014) 8 SCC {900 + i}" for i in range(6))
    out = run(text, cfg)
    d = out["denominator"]
    assert d["looked_up"] == 2 and d["not_checked_cap"] == 4 and d["read"] == 6
    assert out["checked"] == {"checked": 2, "checkable": 6, "read": 6} and out["state"] == "incomplete"
    assert [c["reason"] for c in out["citations"][2:]] == ["cap_reached"] * 4


def test_the_time_cap_cuts_exactly_where_the_clock_says() -> None:
    t = [0.0]

    def clock() -> float:
        t[0] += 10.0
        return t[0]

    cfg = DraftCheckConfig(max_seconds=15)
    out = D.check_draft("(2014) 8 SCC 901 (2014) 8 SCC 902 (2014) 8 SCC 903", cfg, lookup, clock=clock)["draft_check"]
    # started at 10; the first lookup is at 20 (10 elapsed, allowed); the second at 30 (20 elapsed, over 15): cut there
    assert out["denominator"]["looked_up"] == 1 and out["denominator"]["not_checked_cap"] == 2
    assert [c["reason"] for c in out["citations"]] == [None, "time_cap", "time_cap"]


def test_a_big_draft_of_one_citation_is_bounded_and_the_counts_stay_exact() -> None:
    text = "(2014) 8 SCC 900 " * 6_000
    out = D.check_draft(text, DraftCheckConfig(max_chars=200_000), lookup)["draft_check"]
    assert out["denominator"]["read"] == 6_000 and out["listing_truncated"] is True and len(out["citations"]) == D.MAX_LISTED
    assert out["denominator"]["looked_up"] == 1


def test_a_draft_of_thousands_of_distinct_citations_is_capped_at_the_lookup_cap() -> None:
    text = " ".join(f"(2014) 8 SCC {1000 + i}" for i in range(5_000))
    out = D.check_draft(text, DraftCheckConfig(max_chars=200_000, max_citations=150), lookup)["draft_check"]
    d = out["denominator"]
    assert (d["read"], d["looked_up"], d["not_checked_cap"]) == (5_000, 150, 4_850) and out["state"] == "incomplete"


def test_the_report_names_what_it_does_not_assess() -> None:
    out = run(f"see {GOOD}")
    assert "whether a case says what the draft says it says" in out["not_assessed"]
    assert "not that the case says what the draft says" in out["wording"]
    assert "fake" not in str(out).lower() and "hallucinat" not in str(out).lower()


def test_the_denominator_of_a_real_shaped_four_paragraph_draft_is_exact() -> None:
    text = DRAFT_HC + f"\n4. The respondent relies on {GOOD}, AIR 2015 SC 44 and MANU/SC/0123/2014.\n"
    d = run(text)["denominator"]
    assert (d["like_strings"], d["read"], d["not_read"], d["checked"], d["not_checked"]) == (15, 14, 1, 1, 13)


# ---- the typed name ---------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("prefix", "want"),
    [
        ("As held in Aldric Vennoway v. State of Zorbland, ", "Aldric Vennoway v. State of Zorbland"),
        ("See Quill Mendelsohn vs. The Union of Zorbland ", "Quill Mendelsohn v. The Union of Zorbland"),
        ("In State of U.P. v. R. K. Sharma, ", "State of U.P. v. R. K. Sharma"),
        ("Aldric Vennoway v. State of Zorbland; ", "Aldric Vennoway v. State of Zorbland"),
        ("the ruling in Brandel & Co. v. Thackeray Ltd. (", "Brandel & Co. v. Thackeray Ltd."),
    ],
)
def test_a_name_in_front_of_the_citation_is_read(prefix: str, want: str) -> None:
    assert D.extract_name_before(prefix + GOOD, len(prefix)) == want


@pytest.mark.parametrize(
    "prefix",
    [
        "As the court said, ",  # no party pair
        "Aldric Vennoway v. State of Zorbland held that the matter was reported in ",  # prose inside the right party
        "Aldric Vennoway v. State of Zorbland; and Brandel v. Thackeray; held ",  # a clause break inside the right party
        "x v. " + "word " * 20,  # too long
    ],
)
def test_a_name_that_cannot_be_bounded_is_not_read_never_guessed(prefix: str) -> None:
    assert D.extract_name_before(prefix + GOOD, len(prefix)) is None


# ---- the draft itself: text and text-layer PDF ------------------------------------------------------------------------------


def make_pdf(pages: list[str]) -> bytes:
    """A minimal valid PDF with one text line per page (an empty string makes a page with no text layer)."""
    objs: list[bytes] = []
    n = len(pages)
    kids = " ".join(f"{4 + 2 * i} 0 R" for i in range(n))
    objs.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objs.append(f"<< /Type /Pages /Kids [{kids}] /Count {n} >>".encode())
    objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    for i, t in enumerate(pages):
        body = f"BT /F1 12 Tf 72 720 Td ({t}) Tj ET" if t else ""
        res = "/Resources << /Font << /F1 3 0 R >> >>"
        objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {5 + 2 * i} 0 R {res} >>".encode())
        objs.append(f"<< /Length {len(body)} >>\nstream\n{body}\nendstream".encode())
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + o + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def test_a_text_layer_pdf_is_read_page_by_page_with_the_citation_page() -> None:
    pdf = make_pdf([f"First page cites {GOOD}", "Second page cites (2014) 8 SCC 999"])
    text = D.read_draft(D.DraftCheckRequest(pdf_base64=b64(pdf)), CFG)
    out = run(text)
    assert [c["page"] for c in out["citations"]] == [1, 2] and out["denominator"]["pages_total"] == 2
    assert out["state"] == "complete" and out["unread_pages"] == []


def test_a_page_with_no_text_layer_is_counted_not_read_and_the_draft_is_incomplete() -> None:
    pdf = make_pdf([f"First page cites {GOOD}", "", "Third page, nothing citable here at all, only plain prose words."])
    out = run(D.read_draft(D.DraftCheckRequest(pdf_base64=b64(pdf)), CFG))
    assert out["unread_pages"] == [2] and out["denominator"]["pages_not_read"] == 1
    assert out["state"] == "incomplete" and "1 of 3 pages have no text layer" in out["summary"]


def test_a_fully_scanned_pdf_has_no_text_and_is_refused_with_a_reason() -> None:
    with pytest.raises(D.DraftRefused) as e:
        D.read_draft(D.DraftCheckRequest(pdf_base64=b64(make_pdf(["", ""]))), CFG)
    assert e.value.code == "no_text"


@pytest.mark.parametrize(
    ("body", "code", "status"),
    [
        ("not base64 !!!", "pdf_bad_encoding", 422),
        (b64(b"this is not a pdf at all"), "pdf_unreadable", 422),
        (b64(make_pdf(["x"] * 3)), "pdf_too_many_pages", 413),
    ],
)
def test_a_bad_pdf_is_refused_with_a_stable_code(body: str, code: str, status: int) -> None:
    cfg = DraftCheckConfig(max_pdf_pages=2)
    with pytest.raises(D.DraftRefused) as e:
        D.read_draft(D.DraftCheckRequest(pdf_base64=body), cfg)
    assert (e.value.code, e.value.status_code) == (code, status)


def test_an_oversize_pdf_or_text_is_refused_413() -> None:
    with pytest.raises(D.DraftRefused) as e:
        D.read_draft(D.DraftCheckRequest(pdf_base64=b64(make_pdf(["x"]))), DraftCheckConfig(max_pdf_bytes=100))
    assert e.value.code == "pdf_too_large" and e.value.status_code == 413
    with pytest.raises(D.DraftRefused) as e2:
        D.read_draft(D.DraftCheckRequest(text="x " * 600), DraftCheckConfig(max_chars=1_000))
    assert e2.value.code == "too_long" and e2.value.status_code == 413


def test_exactly_one_of_text_and_pdf_is_required() -> None:
    for bad in ({}, {"text": "a", "pdf_base64": "QQ=="}, {"text": "  "}, {"text": 5}, {"pdf_base64": 5}):
        with pytest.raises(ValueError):
            D.DraftCheckRequest(**bad)


def test_an_encrypted_pdf_is_refused() -> None:
    import io

    from pypdf import PdfReader, PdfWriter

    w = PdfWriter()
    for page in PdfReader(io.BytesIO(make_pdf(["secret text here"]))).pages:
        w.add_page(page)
    try:
        w.encrypt("pw", algorithm="RC4-40")
    except Exception:  # noqa: BLE001 - this pypdf build cannot write RC4: nothing to test
        pytest.skip("pypdf cannot write an encrypted file here")
    buf = io.BytesIO()
    w.write(buf)
    with pytest.raises(D.DraftRefused) as e:
        D.read_draft(D.DraftCheckRequest(pdf_base64=b64(buf.getvalue())), CFG)
    assert e.value.code == "pdf_encrypted"


def test_a_pdf_that_runs_out_of_time_leaves_the_remaining_pages_as_pages_with_no_text() -> None:
    pdf = make_pdf([f"Page {i} cites {GOOD} here." for i in range(5)])
    t = [0.0]

    def clock() -> float:
        t[0] += 1.0
        return t[0]

    text = D.pdf_text(pdf, max_pages=10, max_seconds=2.5, clock=clock)
    pages = text.split("\f")
    assert len(pages) == 5 and sum(1 for p in pages if p.strip()) < 5
    out = run(text)
    assert out["state"] == "incomplete" and out["denominator"]["pages_not_read"] >= 1


def test_the_page_numbers_follow_the_form_feeds() -> None:
    text = f"Page one holds the citation {GOOD}\f\fPage three holds (2014) 8 SCC 999\fPage four has plain prose words only"
    out = run(text)
    assert [c["page"] for c in out["citations"]] == [1, 3] and out["unread_pages"] == [2]


def test_a_draft_of_thousands_of_citations_is_checked_in_linear_time() -> None:
    import time as _time

    text = "See (2014) 8 SCC 900, held. " * 7_000  # about 196,000 characters
    t0 = _time.monotonic()
    out = D.check_draft(text, DraftCheckConfig(max_chars=200_000), lookup)["draft_check"]
    assert out["denominator"]["read"] == 7_000
    assert _time.monotonic() - t0 < 8.0  # quadratic work on this size takes minutes


@pytest.mark.parametrize(
    "unit",
    ["2014 AIR ", "(2014) 8 SCC 5 ", "2012 190 DLT 45; ", "(2O14) 8 SCC 5 ", "MANU/SC/0123/2014 ", "S-\nCC 12 AIR 2014 SC 1 "],
)
def test_the_work_grows_linearly_on_dense_text_of_every_shape(unit: str) -> None:
    """A quadratic step shows as a ratio near 16 when the text is four times longer (linear is near 4); the old code measured
    5.5 s at 400,000 characters and about 30 s at 1,000,000 on the first shape. The bound is loose on purpose."""
    import time as _time

    def took(n: int) -> float:
        text = (unit * (n // len(unit) + 1))[:n]
        best = 1e9
        for _ in range(4):  # the best of four: a loaded runner inflates single runs, never the best by much
            t0 = _time.perf_counter()
            D.check_draft(text, DraftCheckConfig(max_chars=1_000_000), lambda t: ("NOT_IN_INDEX", False))
            best = min(best, _time.perf_counter() - t0)
        return best

    small, big = took(100_000), took(400_000)
    # Quadratic code fails BOTH signals (ratio near 16 and big above 5 s); load noise on one measurement fails at most one,
    # so a pass needs either a linear ratio or an absolute time well inside the quadratic one (linear is about 0.5 s).
    assert big / max(small, 1e-4) < 9.0 or big < 1.5, (unit, small, big)
    assert big < 6.0, (unit, big)


def test_the_span_index_answers_overlap_questions_like_a_plain_scan() -> None:
    import random

    rng = random.Random(5)
    spans = [(a, a + rng.randint(1, 30)) for a in (rng.randint(0, 500) for _ in range(60))]
    idx = D._Spans(spans)
    for _ in range(2_000):
        s0 = rng.randint(0, 520)
        e0 = s0 + rng.randint(1, 25)
        assert idx.overlaps(s0, e0) == any(a < e0 and s0 < b for a, b in spans)
    assert D._Spans([]).overlaps(0, 5) is False


# ---- R1's wording rules (#516): no clearance language, names only when typed, plain words -----------------------------------


def test_all_found_reads_as_found_in_our_index_not_as_clearance() -> None:
    many = run(f"see {GOOD} and (2016) 2 SCC 512")["summary"]
    assert many.startswith("All 2 citations checked were found in our index.")
    one = run(f"see {GOOD}")["summary"]
    assert one.startswith("The 1 citation checked was found in our index.")
    assert "No problems" not in many + one and "cleared" not in many.lower()


def test_the_names_clause_counts_only_names_that_were_typed_and_not_confirmed() -> None:
    def look(t: str) -> tuple[str, bool]:
        return "IN_INDEX", False  # found, but the typed name was only partly matched: not confirmed

    typed = run(f"As held in Aldric Vennoway v. State of Zorbland, {GOOD}; and again {GOOD}.", look=look)
    assert typed["denominator"]["found"] == 2 and typed["denominator"]["found_typed_name_not_confirmed"] == 1
    assert "a case name typed beside 1 of the 2 found citations was not confirmed" in typed["summary"]
    none_typed = run(f"see {GOOD} and (2016) 2 SCC 512", look=look)
    assert none_typed["denominator"]["found_typed_name_not_confirmed"] == 0
    assert "case name" not in none_typed["summary"]
    matched = run(f"As held in Aldric Vennoway v. State of Zorbland, {GOOD}.")
    assert "case name" not in matched["summary"]  # typed and matched: nothing to say


def test_no_summary_line_uses_internal_words_or_the_state_token_as_a_label() -> None:
    drafts = [
        f"see {GOOD}", f"see {GOOD} and AIR 2014 SC 1234", "AIR 2014 SC 1234", DRAFT_HC, "no citations here at all",
        f"see {GOOD}, (2016)10SCC386 and [2014] 8 SCC 273 and MANU/SC/0123/2014",
    ]
    for text in drafts:
        low = run(text)["summary"].lower()
        for word in ("complete", "ambiguous", "deployment", "cap or lookup", "conflict", "nothing_checked", "none_recognised"):
            assert word not in low, (word, text)
    capped = run(" ".join(f"(2014) 8 SCC {900 + i}" for i in range(4)), DraftCheckConfig(max_citations=1))["summary"]
    assert "(a limit was reached or the look-up failed)" in capped
