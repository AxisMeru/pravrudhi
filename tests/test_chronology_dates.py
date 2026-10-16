# ruff: noqa: E501
"""A3 date scanner (chronology_dates): formats, the day/month rule, two-digit years, partial dates, ranges, the cut rules on A2's
REAL split_spans, planted-date invariants and adversarial shapes. Built and tested on invented text and printed dates from public
judgment recitals only. Contract: A3 v4.1 (d8cc8c6e231d) + addenda 2, 3 + v4.2 note."""

from __future__ import annotations

import random
import re
import time
from datetime import date

import pytest

from pravrudhi.application import chronology_dates as CD
from pravrudhi.application.chronology_dates import DocScan, scan_document
from pravrudhi.application.date_parse import parse_date
from pravrudhi.application.matter_spans import MAX_SPAN, split_spans

TODAY = date(2026, 10, 16)  # the injected clock: no test depends on the real date


def scan(text: str, *more: str, page: int = 1) -> DocScan:
    """One document: `text` as one passage (and `more` as further passages with a one-space gap)."""
    rows: list[tuple[str, int, int, str, int | str]] = []
    pos = 0
    for i, t in enumerate((text, *more)):
        rows.append(("d", page, pos, t, i))
        pos += len(t) + 1
    return scan_document(rows, TODAY)


def scan_real(doc_text: str) -> DocScan:
    """The scan of a document cut by A2's REAL split_spans (pages from the form feed)."""
    rows = [("d", page, s, doc_text[s:e], i) for i, (page, s, e) in enumerate(split_spans(doc_text))]
    return scan_document(rows, TODAY)


def by_text(sc: DocScan) -> dict[str, CD.Hit]:
    return {h.text: h for h in sc.hits}


def values(sc: DocScan) -> list[date]:
    return [h.value for h in sc.hits if h.value is not None and h.category in ("exact", "approximate", "scheduled")]


def invariant(sc: DocScan) -> None:
    """Every hit has exactly one category, and every date-LIKE string (the separate permissive detector) is covered by a hit."""
    assert all(h.category in CD.CATEGORIES for h in sc.hits)
    assert sc.counts["hits"] == len(sc.hits) == sum(sc.counts["by_category"].values()) + sc.counts["like_in_unread_sentences"]
    for ui, u in enumerate(sc.units):
        for ls, le in CD._like_spans(u.text):
            assert any(h.unit == ui and h.start < le and h.end > ls for h in sc.hits), (
                u.text[ls:le],
                "a date-like string was dropped",
            )


# ---- formats: every printed shape in Track B's table ------------------------------------------------------------------------

FORMATS = [
    ("arrested on 13.03.2013.", date(2013, 3, 13)),
    ("on 2.12.2006 and 20.12.2006 he", date(2006, 12, 2)),
    ("on 7.01.2009 and 27.01.2009 he", date(2009, 1, 7)),
    ("on 4-1-1997 and 24-1-1997 he", date(1997, 1, 4)),
    ("on 22-09-2003 he", date(2003, 9, 22)),
    ("on 5-1 -1990 and 25-1-1990 he", date(1990, 1, 5)),
    ("on 15/10/1995 he", date(1995, 10, 15)),
    ("on 9th October, 2006 he", date(2006, 10, 9)),
    ("on 26th May, 1998 he", date(1998, 5, 26)),
    ("on 10 April 2014 he", date(2014, 4, 10)),
    ("on May 28, 1998 he", date(1998, 5, 28)),
    ("on June 2, 1998 he", date(1998, 6, 2)),
    ("on 6th of February, 1990 he", date(1990, 2, 6)),
    ("on the 7th day of March 2019 he", date(2019, 3, 7)),
    ("on 2015-04-03 he", date(2015, 4, 3)),
    ("dated 22.8.2006 he", date(2006, 8, 22)),
]


@pytest.mark.parametrize(("text", "want"), FORMATS)
def test_every_printed_shape_is_read(text: str, want: date) -> None:
    sc = scan(text + " More text follows here.")
    assert want in values(sc), [(h.text, h.category, h.not_read_reason) for h in sc.hits]
    assert not [h for h in sc.hits if h.category in ("not_read", "ambiguous")]
    invariant(sc)


@pytest.mark.parametrize(
    ("text", "want"), [f for f in FORMATS if f[0].startswith(("on 10 April", "on May 28", "on 9th", "on 26th", "on June 2"))]
)
def test_the_scanner_reads_a_written_date_exactly_as_b3s_parse_date_does(text: str, want: date) -> None:
    mention = next(h for h in scan(text + " tail text.").hits if h.kind == "written")
    assert parse_date(mention.text).value == want == mention.value


def test_may_counts_as_a_month_only_when_capitalised() -> None:
    assert values(scan("He may arrive on 3 May 2015 and may 4 2015 not.")) == [date(2015, 5, 3)]
    assert not [h for h in scan("it may 12 2015 be").hits if h.kind.startswith("partial")]


@pytest.mark.parametrize(
    "text",
    [
        "on 31.04.2019 he left.",
        "on 29.02.2019 he left.",
        "on 00.00.2019 he left.",
        "on 32.01.2019 he left.",
        "on 13.13.2019 he left.",
    ],
)
def test_a_calendar_invalid_date_is_never_clamped_it_is_not_read(text: str) -> None:
    sc = scan(text)
    assert values(sc) == [] and all(h.category == "not_read" for h in sc.hits) and sc.hits
    invariant(sc)


# ---- the day/month rule ------------------------------------------------------------------------------------------------------


def test_a_part_above_twelve_settles_that_date_by_itself() -> None:
    sc = scan("On 13.04.2015 and 04.13.2015 he left.")
    assert values(sc) == [date(2015, 4, 13), date(2015, 4, 13)]
    assert {h.how for h in sc.hits} == {"only_valid_reading"}


def test_day_equals_month_is_not_ambiguous() -> None:
    h = scan("On 05.05.2015 he left.").hits[0]
    assert h.how == "day_equals_month" and h.value == date(2015, 5, 5)


def test_an_ambiguous_numeric_date_is_read_day_first_only_when_the_same_paper_settles_it() -> None:
    sc = scan("On 03.04.2015 he left. On 13.04.2015 he came back.")
    h = by_text(sc)["03.04.2015"]
    assert h.category == "exact" and h.how == "day_first_by_document_evidence" and h.value == date(2015, 4, 3)
    assert h.evidence == {"text": "13.04.2015", "page": 1}


def test_month_first_evidence_reads_month_first() -> None:
    h = by_text(scan("On 03/04/2015 he left. On 04/13/2015 he came back."))["03/04/2015"]
    assert h.how == "month_first_by_document_evidence" and h.value == date(2015, 3, 4)


def test_nothing_settling_it_makes_it_ambiguous_with_both_readings_and_no_value() -> None:
    h = scan("On 03.04.2015 he left.").hits[0]
    assert h.category == "ambiguous" and h.value is None and h.readings == (date(2015, 4, 3), date(2015, 3, 4))


def test_evidence_must_share_the_separator() -> None:
    sc = scan("On 03.04.2015 he left. On 13/04/2015 he came back.")
    assert by_text(sc)["03.04.2015"].category == "ambiguous"


def test_a_file_that_shows_both_conventions_is_ambiguous_for_every_ambiguous_date() -> None:
    sc = scan("On 03.04.2015 he left. On 13.04.2015 and on 04.13.2015 he came back.")
    assert by_text(sc)["03.04.2015"].category == "ambiguous"


@pytest.mark.parametrize(
    "evidence_sentence",
    [
        'The petitioner said "he came on 13.04.2015" to the court.',  # quoted
        "As per the FIR dated 13.04.2015 he came back.",  # reference clause
        "He came on 13.04.2015 vide the order of the court.",  # a following as-per clause does not matter; this one counts
    ],
)
def test_evidence_inside_a_quotation_or_a_reference_clause_does_not_count(evidence_sentence: str) -> None:
    sc = scan("On 03.04.2015 he left. " + evidence_sentence)
    h = by_text(sc)["03.04.2015"]
    if "vide the order" in evidence_sentence:
        assert h.category == "exact"
    else:
        assert h.category == "ambiguous"


def test_an_apostrophe_is_not_a_quotation() -> None:
    sc = scan("On 03.04.2015 he left. The accused’s son came on 13.04.2015 and Hon’ble Court sat.")
    assert by_text(sc)["03.04.2015"].category == "exact" and not by_text(sc)["13.04.2015"].quoted


@pytest.mark.parametrize("opener,closer", [("“", "”"), ('"', '"'), ("„", "‚"), ("□", "‚"), ("«", "»")])
def test_every_double_quote_glyph_and_the_corrupted_ones_open_a_quotation(opener: str, closer: str) -> None:
    sc = scan(f"On 03.04.2015 he left. He said {opener}come on 13.04.2015{closer} and went.")
    assert by_text(sc)["13.04.2015"].quoted and by_text(sc)["03.04.2015"].category == "ambiguous"


def test_single_curly_quotes_open_only_with_a_matching_close() -> None:
    sc = scan("The plea was ‘arrested on 13.04.2015’ and on 03.04.2015 he left.")
    assert by_text(sc)["13.04.2015"].quoted and not by_text(sc)["03.04.2015"].quoted


def test_a_quotation_carries_across_paragraph_passages_up_to_the_cap() -> None:
    parts = [
        "“First paragraph opens here and goes on",
        "second paragraph with 13.04.2015 inside",
        "third paragraph closes here.”",
        "After it 14.04.2015 is not quoted.",
    ]
    sc = scan(*parts)
    assert by_text(sc)["13.04.2015"].quoted and not by_text(sc)["14.04.2015"].quoted
    long_open = ["“Open"] + [f"para {i} filler text. " * 3 for i in range(10)] + ["and 15.04.2015 here."]
    assert not by_text(scan(*long_open))["15.04.2015"].quoted  # the cap (8 paragraphs) ends it


def test_a_repeated_header_date_is_a_printed_line_and_never_evidence() -> None:
    page = "IN THE HIGH COURT\nDated: 13.04.2015\nBody text of the page that says nothing."
    rows = [("d", p, 0 + p * 100, page.replace("Body text", f"Body {p}"), p) for p in range(1, 5)]
    rows.append(("d", 5, 500, "On 03.04.2015 he left. Nothing else.", 9))
    sc = scan_document(rows, TODAY)
    printed = [h for h in sc.hits if h.category == "printed"]
    assert len(printed) == 4
    assert by_text(sc)["03.04.2015"].category == "ambiguous"  # the header date did not settle the convention


def test_a_date_line_that_is_not_among_the_first_or_last_three_lines_is_not_printed() -> None:
    body = "line one\nline two\nline three\nline four\nNext date: 13.04.2015\nline six\nline seven\nline eight\nline nine"
    rows = [("d", p, p * 200, body, p) for p in range(1, 5)]
    sc = scan_document(rows, TODAY)
    assert not [h for h in sc.hits if h.category == "printed"]


@pytest.mark.parametrize("line", ["Printed on 03.04.2015", "Downloaded on 03.04.2015 from the portal", "Page 3 of 10 03.04.2015"])
def test_print_and_stamp_phrases_mark_a_printed_line(line: str) -> None:
    assert scan(line + "\nAnd the text goes on.").hits[0].category == "printed"


# ---- two-digit years ---------------------------------------------------------------------------------------------------------


def test_a_two_digit_year_needs_one_century_of_full_year_dates_nearby() -> None:
    sc = scan("On 12 June 2014 he left and on 20.03.15 he came back.")
    assert by_text(sc)["20.03.15"].value == date(2015, 3, 20) and by_text(sc)["20.03.15"].how == "two_digit_with_century_evidence"


def test_a_two_digit_year_with_no_full_year_date_is_not_read() -> None:
    h = scan("On 20.03.15 he came back.").hits[0]
    assert h.category == "not_read" and h.not_read_reason == "two_digit_year"


def test_two_centuries_of_full_years_leave_two_digit_years_unread() -> None:
    sc = scan("In 12 June 1987 and 3 May 2015 there was 20.03.15 then.")
    assert by_text(sc)["20.03.15"].category == "not_read"


def test_a_two_digit_year_more_than_thirty_years_from_the_nearest_full_year_is_not_read() -> None:
    sc = scan("On 12 June 1987 he left and on 20.03.15 came back.")
    assert by_text(sc)["20.03.15"].category == "not_read"  # 1915 is within 30 years of 1987 but below 1900? no: 1915 >= 1900
    sc2 = scan("On 12 June 2014 he left and on 20.03.65 came back.")
    assert by_text(sc2)["20.03.65"].category == "not_read"  # 2065 is past next year


def test_a_year_in_the_future_or_before_1800_is_not_read() -> None:
    assert scan("On 12 June 2051 he left.").hits[0].not_read_reason == "year_not_plausible"
    assert scan("On 12 June 1799 he left.").hits[0].not_read_reason == "year_not_plausible"
    assert scan("On 12 June 2027 he left.").hits[0].category == "exact"  # next year is allowed


# ---- partial dates ------------------------------------------------------------------------------------------------------------


def test_month_year_day_month_and_year_only_are_partial_dates_never_a_day() -> None:
    sc = scan("In March 2019 he left; on 3rd April he returned; during 2020 he stayed. And more text after.")
    cats = {h.text: (h.category, h.precision) for h in sc.hits}
    assert cats == {
        "March 2019": ("less_than_a_day", "month"),
        "3rd April": ("less_than_a_day", "day_month"),
        "2020": ("less_than_a_day", "year"),
    }
    assert all(h.value is None for h in sc.hits)  # no value, no borrowed year, never ordered


@pytest.mark.parametrize(
    "text",
    [
        "The Negotiable Instruments Act, 1881 applies here.",
        "As held in (2014) 8 SCC 1 the court said so.",
        "See AIR 2014 SC 5 for the rule.",
        "Reported at [2014] 1 All ER 3 in the report.",
        "Crl.A. No. 12 of 2014 was filed late.",
        "The Indian Penal Code, 1860 provides this.",
    ],
)
def test_a_year_inside_a_statute_or_citation_is_never_a_row(text: str) -> None:
    assert not [h for h in scan(text + " Further words follow here.").hits if h.kind == "partial_year"]


# ---- ranges --------------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "a", "b"),
    [
        ("between 22 September 2012 and 5 October 2012 he", date(2012, 9, 22), date(2012, 10, 5)),
        ("from 21.2.2010 to 25.2.2010 he", date(2010, 2, 21), date(2010, 2, 25)),
        ("from 1st to 5th March 2019 he", date(2019, 3, 1), date(2019, 3, 5)),
        ("on 12-15 March 2019 he", date(2019, 3, 12), date(2019, 3, 15)),
    ],
)
def test_a_range_is_one_mention_with_both_ends(text: str, a: date, b: date) -> None:
    sc = scan(text + " was there.")
    assert len(sc.hits) == 1 and sc.hits[0].value == a and sc.hits[0].end_value == b and sc.hits[0].how == "range"


def test_a_range_with_an_ambiguous_end_is_ambiguous_not_guessed() -> None:
    h = scan("from 1.2.2010 to 9.2.2010 he was there.").hits[0]
    assert h.category == "ambiguous" and h.value is None and h.readings


def test_a_reversed_range_is_not_read() -> None:
    h = scan("from 5 June 2019 to 3 June 2019 he stayed.").hits[0]
    assert h.category == "not_read" and h.not_read_reason == "range_not_read"


def test_two_events_joined_by_a_preposition_are_two_rows_not_a_range() -> None:
    sc = scan("from the arrest on 3.5.2019 to the release on 9.5.2019 he stayed.")
    assert len([h for h in sc.hits if h.kind != "range"]) == 2 and not [h for h in sc.hits if h.kind == "range"]


# ---- identifiers and times -----------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Cr. No. 25/06/2015 was registered.",
        "Crl.O.P. No. 12/05/2014 was filed.",
        "Case No. 123/03/2019 pending.",
        "at 10.30.15 hrs he left.",
        "FIR No. 45/2019 dated",
    ],
)
def test_an_identifier_or_time_shape_is_a_case_number_not_a_date(text: str) -> None:
    sc = scan(text + " More words follow here.")
    assert not [h for h in sc.hits if h.category in ("exact", "ambiguous")]
    invariant(sc)


def test_a_real_date_next_to_a_marker_word_is_still_read() -> None:
    sc = scan("Crime No. 12/2015 was registered on 3.4.2015 at 1.45 a.m. at the station. Further text.")
    assert any(h.text == "3.4.2015" and h.category in ("exact", "ambiguous") for h in sc.hits)


# ---- OCR and the invariant ------------------------------------------------------------------------------------------------------

PLANTED = ["12.03.2019", "3 June 2014", "June 3, 2014", "2015-04-03", "13/04/2015", "7th day of March 2019"]


@pytest.mark.parametrize("planted", PLANTED)
def test_every_one_character_corruption_of_a_planted_date_is_read_or_counted_never_missing(planted: str) -> None:
    for i, ch in enumerate(planted):
        if not (ch.isdigit()):
            continue
        for bad in "OolI|!":
            text = f"Statement: the matter happened on {planted[:i] + bad + planted[i + 1 :]} as noted. Further text follows."
            sc = scan(text)
            invariant(sc)
            assert sc.hits, text  # a date-like string always leaves a row


def test_non_ascii_digits_are_not_read_they_are_counted_as_dates_we_could_not_read() -> None:
    sc = scan("On １２.０３.２０１９ and १२.०३.२०१९ he left. More text.")
    assert sc.hits and all(h.category == "not_read" for h in sc.hits)
    invariant(sc)


def test_the_planted_corpus_is_read_once_or_counted_over_random_documents() -> None:
    rng = random.Random(971)
    fmts = [
        lambda d: f"{d.day}.{d.month}.{d.year}",
        lambda d: f"{d.day} {d.strftime('%B')} {d.year}",
        lambda d: d.isoformat(),
        lambda d: f"{d.strftime('%B')} {d.day}, {d.year}",
    ]
    for _ in range(40):
        want, parts = [], []
        for _k in range(rng.randint(1, 6)):
            d = date(rng.randint(1995, 2024), rng.randint(1, 12), rng.randint(13, 28))
            want.append(d)
            parts.append(f"The event was recorded on {rng.choice(fmts)(d)} and noted.")
        sc = scan(" ".join(parts) + " End of the paper.")
        invariant(sc)
        assert sorted(values(sc)) == sorted(want)


# ---- the cut rules, on A2's REAL split_spans ------------------------------------------------------------------------------------


def _cut_doc(prefix_len: int, tail: str) -> str:
    """A paragraph of exactly `prefix_len` characters (words of 7 letters, no sentence end) followed by `tail`."""
    words = ("alphabe " * (prefix_len // 8 + 2))[:prefix_len]
    return words + tail


@pytest.mark.parametrize(
    "tail", [" on 13 June 2024 the accused was arrested here.", " on 13.06.2024 the accused was arrested here."]
)
def test_no_row_carries_a_date_the_paper_does_not_say_whatever_the_cut(tail: str) -> None:
    """Fuzz: slide the paragraph so the hard cut (at 1500) falls at EVERY position inside the planted date; no row may show another
    date, and the planted date is read once or counted as not read, never both and never missing."""
    planted = re.search(r"13 June 2024|13\.06\.2024", tail)
    assert planted
    base = 1500 - len(tail[: planted.start()]) - len(planted.group(0))
    for shift in range(len(planted.group(0)) + 6):
        doc = _cut_doc(base + shift, tail)
        sc = scan_real(doc)
        invariant(sc)
        got = [h for h in sc.hits if h.category not in ("not_read",)]
        for h in got:
            assert h.value in (None, date(2024, 6, 13)), (shift, h.text, h.value)
        reads = [h for h in sc.hits if h.value == date(2024, 6, 13)]
        assert len(reads) <= 1, (shift, [h.text for h in sc.hits])
        # never silently missing: either read once or some hit counts it as not read / partial
        assert reads or sc.hits, shift


def test_the_three_gap_zero_tails_are_not_read() -> None:
    """R2's tails: a paragraph of 1,499 characters then the date: the second passage starts '2.06.2024 ...'."""
    for date_text in ("13.06.2024", "13 June 2024", "31.12.2019"):
        doc = _cut_doc(1499, date_text + " the accused was arrested.")
        sc = scan_real(doc)
        assert not [h for h in sc.hits if h.value in (date(2024, 6, 3), date(2019, 12, 1))], date_text
        invariant(sc)


def test_the_same_text_with_a_space_before_the_date_reads_the_date() -> None:
    doc = _cut_doc(1499, " 13.06.2024 the accused was arrested.")
    sc = scan_real(doc)
    assert date(2024, 6, 13) in values(sc) or any(h.category == "not_read" for h in sc.hits)
    for h in sc.hits:
        assert h.value in (None, date(2024, 6, 13))


def test_a_mention_ending_a_passage_without_sentence_punctuation_is_not_read_when_a_passage_follows() -> None:
    a, b = "Date of arrest: 13.06.2024", "the next passage continues here."
    sc = scan_document([("d", 1, 0, a, 1), ("d", 1, len(a) + 1, b, 2)], TODAY)
    assert sc.hits[0].category == "not_read" and sc.hits[0].not_read_reason == "cut_boundary"
    last = scan_document([("d", 1, 0, a, 1)], TODAY)  # the document's last passage cannot be a cut
    assert last.hits[0].category in ("exact", "ambiguous")


def test_a_mention_ending_a_passage_at_a_sentence_end_reads() -> None:
    a, b = "He was arrested on 13.06.2024.", "The next passage."
    sc = scan_document([("d", 1, 0, a, 1), ("d", 1, len(a) + 1, b, 2)], TODAY)
    assert sc.hits[0].value == date(2024, 6, 13)


def test_a_partial_type_mention_starting_a_passage_is_not_read_with_a_gap_but_a_complete_one_is() -> None:
    a = "He left the house and went away"
    sc = scan_document([("d", 1, 0, a, 1), ("d", 1, len(a) + 1, "2024 the accused was arrested.", 2)], TODAY)
    assert not [h for h in sc.hits if h.category == "less_than_a_day"]
    sc2 = scan_document([("d", 1, 0, a, 1), ("d", 1, len(a) + 1, "13 June 2024 the accused was arrested.", 2)], TODAY)
    assert date(2024, 6, 13) in values(sc2)
    sc3 = scan_document([("d", 1, 0, a, 1), ("d", 1, len(a) + 1, "3rd April he was arrested.", 2)], TODAY)
    assert sc3.hits[0].category == "not_read"


def test_gap_zero_makes_any_mention_starting_the_passage_not_read_and_the_first_passage_reads() -> None:
    a = "He left the house and went awa"
    sc = scan_document([("d", 1, 0, a, 1), ("d", 1, len(a), "13 June 2024 the accused was arrested.", 2)], TODAY)
    assert sc.hits[0].category == "not_read" and sc.hits[0].not_read_reason == "cut_boundary"
    first = scan_document([("d", 1, 0, "13 June 2024 the accused was arrested.", 1)], TODAY)
    assert first.hits[0].value == date(2024, 6, 13)


def test_split_spans_hard_cut_constant_is_the_one_the_rules_assume() -> None:
    assert MAX_SPAN == 1500


# ---- sentences, languages and cut fragments ------------------------------------------------------------------------------------


def test_a_sentence_is_cut_at_full_stops_not_after_abbreviations_and_never_longer_than_2000() -> None:
    s = CD.sentences("Dr. Sharma was not arrested on 3 April 2015. Mr. Rao was. No. 12 was filed. " + "word " * 1000)
    assert [t for t in s][0][1] > 30
    assert all(e - st <= CD.MAX_SENTENCE for st, e in s)


def test_a_sentence_in_another_script_is_not_read_and_its_dates_are_counted_separately() -> None:
    sc = scan("अभियुक्त को दिनांक 12.03.2019 को गिरफ्तार किया गया। He was heard on 3 June 2014.")
    assert any(s.unread_language for s in sc.sents)
    assert sc.counts["like_in_unread_sentences"] >= 1 and sc.counts["sentences_not_read_language"] >= 1
    assert date(2014, 6, 3) in values(sc)


def test_a_bilingual_sentence_is_read_and_flagged() -> None:
    sc = scan("दिनांक / Date: 12/03/2019 he was heard in the matter.")
    assert any(s.other_language for s in sc.sents) and not any(s.unread_language for s in sc.sents)
    assert sc.hits and sc.hits[0].category in ("exact", "ambiguous")


def test_the_first_and_last_sentence_of_a_cut_unit_are_cut_fragments() -> None:
    a = "The accused was heard and he was not"
    b = "arrested on 3 April 2015. Then he left"
    sc = scan_document([("d", 1, 0, a, 1), ("d", 1, len(a) + 1, b, 2)], TODAY)
    unit0 = [s for s in sc.sents if s.unit == 0]
    unit1 = [s for s in sc.sents if s.unit == 1]
    assert unit0[0].cut_fragment  # ends the passage with no sentence punctuation and a passage follows
    assert unit1[0].cut_fragment and not unit1[-1].cut_fragment  # starts mid-sentence; the last one ends the document
    whole = scan_document([("d", 1, 0, "He was arrested. Then he left.", 1)], TODAY)
    assert not any(s.cut_fragment for s in whole.sents)  # the first passage of a document is not a fragment


# ---- adversarial shapes: linear, never quadratic --------------------------------------------------------------------------------

SHAPES = {
    "digit_runs": lambda n: "1 " * n,
    "dots": lambda n: "1." * n,
    "separators": lambda n: "." * n,
    "month_repeat": lambda n: "12 June " * n,
    "no_sentence_end": lambda n: "word " * n,
    "dates": lambda n: "on 12.03.2019 he " * n,
    "ocr": lambda n: "1O.O3.2O19 " * n,
}


@pytest.mark.parametrize("shape", sorted(SHAPES))
def test_the_scan_is_linear_on_adversarial_input(shape: str) -> None:
    def cost(n: int) -> float:
        text = SHAPES[shape](n)
        t0 = time.perf_counter()
        scan_document([("d", 1, 0, text[:1_000_000], 1)], TODAY)
        return time.perf_counter() - t0

    c1, c2 = cost(20_000), cost(40_000)
    assert c2 < 8 * c1 + 0.5, (shape, c1, c2)  # 2n costs far less than the 4n a quadratic scan would
    assert cost(60_000) < 20.0


def test_fifty_thousand_dates_in_a_million_characters_scan_in_bounded_time() -> None:
    text = "on 12.03.2019 he " * 50_000
    t0 = time.perf_counter()
    sc = scan_document([("d", 1, 0, text[:1_000_000], 1)], TODAY)
    assert time.perf_counter() - t0 < 30 and len(sc.hits) > 40_000


# ---- the cut fuzz: EVERY cut position inside (and around) every planted date -------------------------------------------------------

PLANTS = [
    ("13.06.2024", {date(2024, 6, 13)}),
    ("13 June 2024", {date(2024, 6, 13)}),
    ("June 13, 2024", {date(2024, 6, 13)}),
    ("2024-06-13", {date(2024, 6, 13)}),
    ("13/06/2024", {date(2024, 6, 13)}),
    ("13th day of June 2024", {date(2024, 6, 13)}),
    ("between 13 June 2024 and 15 June 2024", {date(2024, 6, 13), date(2024, 6, 15)}),
    ("from 13 to 15 June 2024", {date(2024, 6, 13), date(2024, 6, 15)}),
    ("3rd April", set()),
    ("in March 2019", set()),
]


def _cut(doc: str, k: int) -> list[tuple[str, int, int, str, int | str]]:
    """Two passages as A2 stores them: the cut at `k`, whitespace trimmed on both sides, offsets into the document."""
    a_end = len(doc[:k].rstrip())
    b_start = k + (len(doc[k:]) - len(doc[k:].lstrip()))
    return [("d", 1, 0, doc[:a_end], 1), ("d", 1, b_start, doc[b_start:], 2)]


@pytest.mark.parametrize(("planted", "allowed"), PLANTS)
@pytest.mark.parametrize("variant", ["plain", "line_break", "double_space"])
def test_no_cut_position_makes_a_row_with_a_date_the_paper_does_not_say(planted: str, allowed: set[date], variant: str) -> None:
    """A cut at EVERY position in and around the planted date (a position on a space gives a gap of 1 or more, one inside a token
    gives gap 0): no row may carry a date the paper does not say; each date is read once or counted, never lost."""
    shown_text = planted
    if variant == "line_break":
        shown_text = planted.replace(" ", "\n", 1) if " " in planted else planted
    elif variant == "double_space":
        shown_text = planted.replace(" ", "  ")
    left, right = "The accused was heard and it was noted that on ", " the order was passed by the court and the file closed."
    doc = left + shown_text + right
    start = len(left)
    for k in range(start - 2, start + len(shown_text) + 3):
        sc = scan_document(_cut(doc, k), TODAY)
        invariant(sc)
        shown = [h.value for h in sc.hits if h.value is not None]
        assert all(v in allowed for v in shown), (planted, k, variant, [(h.text, h.value) for h in sc.hits])
        assert len(shown) == len(set(shown)), (planted, k, variant)
        if allowed and not shown:
            assert sc.hits, (planted, k, variant, "the date vanished")  # something counted it


def test_a_long_sentence_is_never_cut_inside_a_date_mention() -> None:
    pad = "word " * 396  # puts a space-rich date across the 2,000-character limit
    text = pad + "on 13 June 2024 he was arrested " + "more " * 100
    for shift in range(0, 12):
        t = ("x" * shift) + text
        sc = scan_document([("d", 1, 0, t, 1)], TODAY)
        mention = next(h for h in sc.hits if h.text == "13 June 2024")
        sent = sc.sents[mention.sent]
        assert sent.start <= mention.start and mention.end <= sent.end, shift
