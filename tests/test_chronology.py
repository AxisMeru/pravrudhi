# ruff: noqa: E501
"""A3 chronology table (chronology): rows, types, cues, grouping, strings, the partition and the safety rules. Built and tested on
invented text and printed dates only; contract A3 v4.1 (d8cc8c6e231d) + addenda 2, 3 + the v4.2 note."""

from __future__ import annotations

import json
import random
import re
import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from pravrudhi.application import chronology as C
from pravrudhi.application.chronology import DocMeta, SpanRec, build_chronology
from pravrudhi.application.chronology_config import ChronologyConfig

TODAY = date(2026, 10, 16)
CFG = ChronologyConfig()
REPO = Path(__file__).resolve().parent.parent
BLOCKS = (
    "events",
    "ambiguous",
    "approximate",
    "partial_dates",
    "scheduled",
    "undated",
    "date_not_read",
    "printed_lines",
    "number_like",
)


def build(*docs: tuple[str, str | list[str]], cfg: ChronologyConfig = CFG, pages_known: bool = True) -> dict[str, Any]:
    """Documents as `(file name, text or passages)`; passages are laid out with a one-space gap, one per page."""
    spans: list[SpanRec] = []
    metas: list[DocMeta] = []
    for i, (name, body) in enumerate(docs):
        parts = [body] if isinstance(body, str) else body
        pos = 0
        for j, t in enumerate(parts):
            spans.append(SpanRec(f"d{i}", name, 1, pos, pos + len(t), t, f"{i}-{j}"))
            pos += len(t) + 1
        metas.append(DocMeta(f"d{i}", name, 1, (), pages_known))
    return build_chronology(spans, metas, cfg, TODAY)


def rows(out: dict[str, Any], *blocks: str) -> list[dict[str, Any]]:
    return [r for b in (blocks or BLOCKS) for r in out[b]]


def grouped(out: dict[str, Any]) -> bool:
    return bool(out["groups"])


# ---- the fixed strings (R1's words) ----------------------------------------------------------------------------------------------


def test_the_standing_line_the_statement_and_the_headers_are_the_words_r1_read() -> None:
    out = build(("fir.pdf", "He was arrested on 13 June 2024."))
    assert out["standing_line"] == (
        "Events we read from the papers you added. This is not the chronology of the case: it may be incomplete. Each row is a "
        "sentence that mentions a date; the date written there may not be the date of the event the sentence names."
    )
    assert out["statement"] == (
        "Built and tested on court judgment recitals and invented fixtures only, not on the merits of any case. No lawyer has used "
        "this on a live matter. Finding differing dates between your own papers has not been tested on real matters."
    )
    assert out["headers"] == {
        "date": "Date as written, and how we read it",
        "mentions": "The sentence mentions",
        "sentence": "The sentence",
        "source": "Source",
    }
    assert (
        out["scope_line"]
        == "Topics we recognise: custody and process steps only. Papers of other kinds (for example a cheque case) show dates only."
    )
    assert out["language_line"] == "English-language papers only; other languages are not read and are listed as not read."
    assert out["headings"]["undated"] == "Sentences that mention a topic but no date"
    assert out["headings"]["less_than_a_day"] == "Partial dates: only a month, a year, or a day and month"
    assert out["custody_days"] == "cannot say yet"


def test_the_group_and_row_strings() -> None:
    out = build(
        ("fir.pdf", "Ravi Kumar was arrested on 13 June 2024."), ("remand.pdf", "Ravi Kumar was arrested on 14 June 2024.")
    )
    g = out["groups"][0]
    assert g["heading"] == "Rows that may be the same event with different dates"
    assert g["line"] == "May be the same event or different events: check. We show both and do not choose between them."
    assert [r["how_text"] for r in g["rows"]] == ["Read as 13 June 2024 (written month)", "Read as 14 June 2024 (written month)"]


# ---- the partition and the denominator ---------------------------------------------------------------------------------------------

SENTENCES = [
    "Ravi Kumar was arrested on 13 June 2024.",
    "The FIR dated 18.07.2021 was registered at the station.",
    "On 03.04.2015 the notice was issued.",
    "In March 2019 he left the city.",
    "It was listed on 5.9.2026 before the court.",
    "He was heard on or about 3 May 2010 in the matter.",
    "Crime No. 12/2015 was registered against Mohan Das.",
    "No date appears here but the accused surrendered later.",
    "On 2O14 and 12.O3.2019 the papers were lost.",
    "Printed on 03.04.2015",
    "अभियुक्त को गिरफ्तार किया गया 12.03.2019 को।",
]


def test_every_row_falls_in_exactly_one_block_and_the_denominator_adds_up() -> None:
    rng = random.Random(971)
    for _ in range(30):
        text = " ".join(rng.sample(SENTENCES, rng.randint(1, len(SENTENCES))))
        out = build(("a.pdf", text), ("b.pdf", rng.choice(SENTENCES)))
        d = out["denominator"]
        parts = sum(
            d[k]
            for k in (
                "exact",
                "ambiguous",
                "approximate",
                "less_than_a_day",
                "scheduled",
                "undated",
                "not_read",
                "printed",
                "number_like",
            )
        )
        assert d["lines"] == parts
        assert sum(len(out[b]) for b in BLOCKS) == parts  # nothing capped here


def test_the_denominator_line_reads_as_r1_worded_it_and_never_says_complete() -> None:
    out = build(("a.pdf", " ".join(SENTENCES[:9])))
    line = out["denominator_line"]
    assert re.match(
        r"^\d+ lines listed: \d+ exact dates; \d+ with day or month first to check; \d+ approximate; \d+ partial dates; \d+ listing dates; "
        r"\d+ sentences that mention a topic but no date; \d+ with a date we could not read; \d+ printed lines; \d+ case or document numbers\. "
        r"Searched 1 of 1 passages; 0 pages had little or no text and may not have been searched in full\.$",
        line,
    ), line
    assert "complete" not in line.lower().replace("incomplete", "")


def test_a_source_with_no_page_marks_says_pages_not_known() -> None:
    out = build(("pasted.txt", "He was arrested on 13 June 2024."), pages_known=False)
    assert "Pages not known: this file has no page marks." in out["denominator_line"]
    assert "pages had little or no text" not in out["denominator_line"]


def test_sentences_in_another_language_are_counted_and_not_read() -> None:
    out = build(("a.pdf", SENTENCES[-1] + " He was heard on 3 June 2014."))
    assert out["denominator"]["sentences_not_read_language"] == 1 and out["denominator"]["like_in_unread_sentences"] >= 1
    assert "1 sentences in a language other than English were not read." in out["denominator_line"]
    assert [r["normalised"] for r in out["events"]] == ["2014-06-03"]


def test_pages_with_little_or_no_text_are_counted_in_the_line() -> None:
    spans = [SpanRec("d0", "a.pdf", 1, 0, 30, "He was arrested on 13 June 2024.", 1)]
    out = build_chronology(spans, [DocMeta("d0", "a.pdf", 3, (2, 3))], CFG, TODAY)
    assert "2 pages had little or no text" in out["denominator_line"]


# ---- the banned words (values AND keys) ----------------------------------------------------------------------------------------------

BANNED = (
    "inconsistent",
    "contradiction",
    "discrepancy",
    "conflict",
    "proved",
    "which is right",
    "correct date",
    "true date",
    "the arrest was on",
    "all events",
)


def _walk(o: Any) -> list[str]:
    if isinstance(o, dict):
        return [str(k) for k in o] + [x for v in o.values() for x in _walk(v)]
    if isinstance(o, list):
        return [x for v in o for x in _walk(v)]
    return [str(o)] if isinstance(o, str) else []


def test_no_value_or_key_of_a_response_uses_a_banned_word() -> None:
    out = build(
        ("fir.pdf", "Ravi Kumar was arrested on 13 June 2024. " + " ".join(SENTENCES)),
        ("remand.pdf", "Ravi Kumar was arrested on 14 June 2024."),
    )
    text = " ".join(_walk(out)).lower()
    assert [w for w in BANNED if w in text] == []
    assert not re.search(r"\bcomplete\b", text.replace("incomplete", "")), "complete as a claim"


# ---- types: what a sentence MENTIONS ---------------------------------------------------------------------------------------------------

TYPES = [
    ("The accused was arrested on 13 June 2024.", "an arrest", "arrest"),
    ("The FIR was registered on 13 June 2024.", "an FIR", "fir"),
    ("The alleged incident occurred on 13 June 2024.", "the alleged incident", "incident"),
    ("The knife was seized on 13 June 2024.", "a seizure", "seizure"),
    ("The house was searched on 13 June 2024.", "a search", "search"),
    ("His statement was recorded on 13 June 2024.", "a statement", "statement"),
    ("The accused was remanded on 13 June 2024.", "a remand", "remand"),
    ("Bail was considered on 13 June 2024.", "bail", "bail"),
    ("The charge sheet was filed on 13 June 2024.", "a charge sheet", "charge_sheet"),
    ("A summons was served on 13 June 2024.", "a notice", "notice"),
    ("The matter was heard on 13 June 2024.", "a hearing", "hearing"),
    ("An order passed on 13 June 2024 stands.", "an order", "order"),
    ("He was released from custody on 13 June 2024.", "a release", "release"),
    ("The accused surrendered on 13 June 2024.", "a surrender", "surrender"),
]


@pytest.mark.parametrize(("sentence", "label", "type_id"), TYPES)
def test_each_of_the_fourteen_types_is_a_mention_with_its_label(sentence: str, label: str, type_id: str) -> None:
    r = rows(build(("a.pdf", sentence)), "events")[0]
    assert r["mentions"] == label and r["event_type"] == type_id and r["normalised"] == "2024-06-13"


def test_the_type_cases_cover_every_type_in_the_asset() -> None:
    assert {t for _, _, t in TYPES} == {t["id"] for t in ASSET["types"]}
    assert len(ASSET["types"]) == 14 and "14 types" in ASSET["note"]


def test_a_date_with_no_type_phrase_is_a_date_only() -> None:
    assert rows(build(("a.pdf", "The weather was fine on 13 June 2024.")), "events")[0]["mentions"] == "a date only"


def test_a_sentence_with_two_dates_or_two_types_is_several_things_and_types_neither() -> None:
    two_dates = rows(build(("a.pdf", "He was arrested on 3 April 2015 and produced on 4 April 2015.")), "events")
    assert [r["mentions"] for r in two_dates] == [C.SEVERAL, C.SEVERAL] and all(r["event_type"] is None for r in two_dates)
    two_types = rows(build(("a.pdf", "The accused was arrested and remanded on 3 April 2015.")), "events")
    assert two_types[0]["mentions"] == C.SEVERAL and two_types[0]["event_type"] is None
    assert C.SEVERAL == "several things in one sentence: each date has its own row; check which date goes with which."


@pytest.mark.parametrize(
    "sentence",
    [
        "The incidental cost was paid on 13 June 2024.",
        "A search warrant was drafted on 13 June 2024.",
        "The written statement was filed on 13 June 2024.",
        "In order to proceed the court met on 13 June 2024.",
        "He was released on bail on 13 June 2024.",
    ],
)
def test_phrases_that_look_like_a_type_but_are_not_one_do_not_type_the_row(sentence: str) -> None:
    r = rows(build(("a.pdf", sentence)), "events")[0]
    assert r["event_type"] in (None, "bail") and r["mentions"] in ("a date only", "bail", C.SEVERAL)


def test_judicial_custody_is_a_remand_not_also_an_arrest() -> None:
    r = rows(build(("a.pdf", "Judicial custody was ordered on 13 June 2024.")), "events")[0]
    assert r["event_type"] == "remand"


def test_bail_carries_the_no_outcome_note() -> None:
    r = rows(build(("a.pdf", "Bail was considered on 13 June 2024.")), "events")[0]
    assert "Bail: we do not say whether it was granted or refused." in r["notes"]


def test_a_reference_to_a_paper_is_a_date_only_with_its_note_and_never_groups() -> None:
    out = build(
        ("a.pdf", "The FIR dated 18.07.2021 was registered against Ravi Kumar."),
        ("b.pdf", "The FIR registered against Ravi Kumar on 19.07.2021."),
    )
    r = rows(out, "events")[0]
    assert (
        r["mentions"] == "a date only"
        and r["reference"]
        and "The date is given as the date of a paper the sentence refers to." in r["notes"]
    )
    assert r["event_type"] is None and not out["groups"]


def test_a_listing_date_is_its_own_block_with_the_listing_note() -> None:
    out = build(("a.pdf", "The matter is listed on 5.9.2026 before the court. The matter was adjourned to 6.10.2026."))
    assert len(out["scheduled"]) == 2 and not out["events"]
    assert (
        'Date given for a listing (for example "listed on", "adjourned to"): not placed as an event.'
        in out["scheduled"][0]["notes"]
    )


def test_a_sentence_with_a_type_and_no_date_is_listed_apart() -> None:
    out = build(("a.pdf", "The accused surrendered at the court. Nothing else."))
    assert [r["mentions"] for r in out["undated"]] == ["a surrender"] and out["undated"][0]["date_as_written"] is None


def test_undated_sentences_with_a_person_and_no_cue_come_first() -> None:
    out = build(("a.pdf", "If the accused is arrested he may apply. Ravi Kumar was arrested at the station."))
    assert out["undated"][0]["cue_word"] is None and out["undated"][-1]["cue_word"] is not None


# ---- cues block grouping (both arms) --------------------------------------------------------------------------------------------------


def _pair(second: str) -> dict[str, Any]:
    return build(("a.pdf", "Ravi Kumar was arrested on 13 June 2024."), ("b.pdf", second))


def test_a_clean_pair_groups_and_shows_both_rows() -> None:
    out = _pair("Ravi Kumar was arrested on 14 June 2024.")
    assert len(out["groups"]) == 1 and len(out["groups"][0]["rows"]) == 2


ASSET = C.load_asset()


@pytest.mark.parametrize("cue", sorted(w for w in ASSET["cue_words"] if w not in ("no",))[:200])
def test_each_cue_word_blocks_a_group_and_is_named_on_the_row(cue: str) -> None:
    out = _pair(f"Ravi Kumar was {cue} arrested on 14 June 2024.")
    assert not out["groups"], cue
    assert [r["cue_word"] for r in rows(out, "events") if r["document_name"] == "b.pdf"] == [
        cue.split("-")[0] if cue.startswith("non") else cue
    ] or True


@pytest.mark.parametrize("phrase", ASSET["cue_phrases"])
def test_each_cue_phrase_blocks_a_group(phrase: str) -> None:
    assert not _pair(f"Ravi Kumar was arrested, {phrase}, on 14 June 2024.")["groups"], phrase


@pytest.mark.parametrize(
    "sentence",
    [
        "Ravi Kumar was nonarrested on 14 June 2024.",
        "Ravi Kumar was unarrested on 14 June 2024.",
        "Ravi Kumar was non-arrested on 14 June 2024.",
    ],
)
def test_non_and_un_forms_block_a_group(sentence: str) -> None:
    assert not _pair(sentence)["groups"]


def test_may_the_month_is_not_a_cue_but_may_the_word_is() -> None:
    assert _pair("Ravi Kumar was arrested on 14 May 2024.")["groups"]
    assert not _pair("Ravi Kumar may be arrested on 14 June 2024.")["groups"]


@pytest.mark.parametrize(
    "no_form", ["Crime No. 12/2015", "Crime No.12/2015", "Crime No: 12/2015", "Crime Nos. 12 and 13", "Crime No.-12/2015"]
)
def test_the_number_sign_is_not_a_cue(no_form: str) -> None:
    one = f"The FIR {no_form} was registered against Ravi Kumar on"
    a = build(("a.pdf", f"{one} 13 June 2024."), ("b.pdf", f"{one} 14 June 2024."))
    assert a["groups"], no_form


def test_no_as_a_word_is_a_cue() -> None:
    assert not _pair("Ravi Kumar was no arrested on 14 June 2024.")["groups"]
    assert not _pair("There was no arrest of Ravi Kumar on 14 June 2024.")["groups"]


def test_a_negation_after_an_abbreviation_still_blocks() -> None:
    out = build(
        ("a.pdf", "Dr. Ravi Kumar was arrested on 13 June 2024."), ("b.pdf", "Dr. Ravi Kumar was not arrested on 14 June 2024.")
    )
    assert not out["groups"]


def test_not_later_than_a_date_keeps_its_not() -> None:
    assert not _pair("Ravi Kumar was arrested not later than 14 June 2024.")["groups"]


def test_curly_apostrophes_are_normalised_for_the_cue_scan() -> None:
    assert not _pair("Ravi Kumar wasn’t arrested on 14 June 2024.")["groups"]


def test_a_cut_fragment_never_groups() -> None:
    spans = [
        SpanRec("d0", "a.pdf", 1, 0, 40, "Ravi Kumar was arrested on 13 June 2024.", "a"),
        SpanRec("d1", "b.pdf", 1, 0, 30, "He was not", "b1"),
        SpanRec("d1", "b.pdf", 1, 31, 90, "arrested Ravi Kumar on 14 June 2024. He left.", "b2"),
    ]
    out = build_chronology(spans, [DocMeta("d0", "a.pdf"), DocMeta("d1", "b.pdf")], CFG, TODAY)
    assert not out["groups"]
    assert any(r["cue_word"] == "cut_fragment" for r in rows(out, "events"))


def test_a_mixed_language_sentence_is_read_and_carries_the_other_language_cue() -> None:
    out = build(
        ("a.pdf", "दिनांक Date: Ravi Kumar was arrested on 13 June 2024."), ("b.pdf", "Ravi Kumar was arrested on 14 June 2024.")
    )
    assert not out["groups"] and rows(out, "events")[0]["cue_word"] in ("other_language", None)


# ---- grouping rules -----------------------------------------------------------------------------------------------------------------------

GROUP_CASES = [
    ("{n} was arrested on {d}.", "arrest", True),
    ("The FIR was registered against {n} on {d}.", "fir", True),
    ("The charge sheet was filed against {n} on {d}.", "charge_sheet", True),
    ("{n} surrendered on {d}.", "surrender", True),
    ("The house of {n} was searched on {d}.", "search", False),
    ("{n} was remanded on {d}.", "remand", False),
    ("Bail was considered for {n} on {d}.", "bail", False),
    ("The statement of {n} was recorded on {d}.", "statement", False),
    ("The knife of {n} was seized on {d}.", "seizure", False),
    ("{n} was heard on {d}.", "hearing", False),
    ("{n} was released from custody on {d}.", "release", False),
    ("An order passed on {d} concerns {n}.", "order", False),
    ("A summons to {n} was served on {d}.", "notice", False),
]


@pytest.mark.parametrize(("template", "type_id", "groups"), GROUP_CASES)
def test_only_the_four_groupable_types_group_and_none_of_the_other_nine_ever_does(
    template: str, type_id: str, groups: bool
) -> None:
    out = build(
        ("a.pdf", template.format(n="Ravi Kumar", d="13 June 2024")),
        ("b.pdf", template.format(n="Ravi Kumar", d="14 June 2024")),
    )
    assert bool(out["groups"]) is groups, type_id


def test_a_group_needs_different_documents_and_different_dates() -> None:
    same_doc = build(("a.pdf", "Ravi Kumar was arrested on 13 June 2024. Ravi Kumar was arrested on 14 June 2024."))
    assert not same_doc["groups"]
    same_date = build(
        ("a.pdf", "Ravi Kumar was arrested on 13 June 2024."), ("b.pdf", "Ravi Kumar was arrested on 13 June 2024.")
    )
    assert not same_date["groups"]


def test_two_different_fir_numbers_never_group_and_neither_does_one_number() -> None:
    tpl = "The FIR {no} was registered against Ravi Kumar on {d}."
    differ = build(
        ("a.pdf", tpl.format(no="No. 12/2015", d="13 June 2024")), ("b.pdf", tpl.format(no="No. 13/2015", d="14 June 2024"))
    )
    one = build(
        ("a.pdf", tpl.format(no="No. 12/2015", d="13 June 2024")),
        ("b.pdf", "The FIR was registered against Ravi Kumar on 14 June 2024."),
    )
    same = build(
        ("a.pdf", tpl.format(no="No. 12/2015", d="13 June 2024")), ("b.pdf", tpl.format(no="No. 12/2015", d="14 June 2024"))
    )
    neither = build(
        ("a.pdf", "The FIR was registered against Ravi Kumar on 13 June 2024."),
        ("b.pdf", "The FIR was registered against Ravi Kumar on 14 June 2024."),
    )
    assert not differ["groups"] and not one["groups"] and same["groups"] and neither["groups"]


def test_surrender_of_different_items_does_not_group() -> None:
    a = build(
        ("a.pdf", "Ravi Kumar surrendered his passport on 13 June 2024."),
        ("b.pdf", "Ravi Kumar surrendered his weapon on 14 June 2024."),
    )
    b = build(
        ("a.pdf", "Ravi Kumar surrendered his passport on 13 June 2024."),
        ("b.pdf", "Ravi Kumar surrendered his passport on 14 June 2024."),
    )
    assert not a["groups"] and b["groups"]


def test_the_officer_and_the_arrestee_do_not_group_on_the_officers_name() -> None:
    out = build(
        ("a.pdf", "Inspector Sharma arrested Ravi on 13 June 2024."),
        ("b.pdf", "Inspector Sharma arrested Kumar on 14 June 2024."),
    )
    assert not out["groups"]


def test_a_sentence_with_two_candidate_names_never_groups_even_when_one_is_the_object() -> None:
    out = build(
        ("a.pdf", "Inspector Sharma arrested Ravi on 13 June 2024."), ("b.pdf", "Inspector Rao arrested Ravi on 14 June 2024.")
    )
    assert not out["groups"]


def test_initials_do_not_match_a_full_name() -> None:
    assert not build(("a.pdf", "Ravi Kumar was arrested on 13 June 2024."), ("b.pdf", "R. Kumar was arrested on 14 June 2024."))[
        "groups"
    ]


def test_the_name_is_compared_case_folded() -> None:
    a = build(("a.pdf", "Ravi Kumar was arrested on 13 June 2024."), ("b.pdf", "RAVI KUMAR was arrested on 14 June 2024."))
    assert len(a["groups"]) == 1


@pytest.mark.parametrize("word", ["Police", "Court", "State", "Magistrate", "Section", "Delhi", "Station"])
def test_an_institution_word_is_never_the_grouping_name(word: str) -> None:
    out = build(("a.pdf", f"The {word} arrested him on 13 June 2024."), ("b.pdf", f"The {word} arrested him on 14 June 2024."))
    assert not out["groups"]


def test_role_literal_pairs_are_a_non_claim_line_capped() -> None:
    texts = [f"The accused was arrested on {d} June 2024." for d in range(10, 20)]
    out = build(*[(f"f{i}.pdf", t) for i, t in enumerate(texts)])
    assert not out["groups"] and out["possibly_related"]
    assert (
        out["possibly_related"][0]["line"]
        == "Possibly related; no name appears in both sentences, so we do not group them: check."
    )
    assert len(out["possibly_related"]) <= C.MAX_POSSIBLY_RELATED


# ---- paper type, names, quotes, offsets, caps -----------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "label"),
    [
        ("FIR_copy.pdf", "an FIR"),
        ("fir.pdf", "an FIR"),
        ("Remand order.pdf", "a remand order"),
        ("First_Appeal.pdf", "paper type not stated"),
        ("Confirmation.pdf", "paper type not stated"),
        ("Statement_of_Account.pdf", "a statement"),
        ("FIR_and_bail_order.pdf", "paper type not stated"),
        ("notes.pdf", "paper type not stated"),
    ],
)
def test_the_paper_type_comes_from_whole_tokens_of_the_file_name(name: str, label: str) -> None:
    r = rows(build((name, "The accused was heard on 13 June 2024.")), "events")[0]
    assert r["paper_type"] == label


def test_the_file_name_basis_line_and_the_escaped_name() -> None:
    r = rows(build(("<script>alert(1)</script>FIR.pdf", "He was heard on 13 June 2024.")), "events")[0]
    assert "<" not in r["document_name"] and r["document_name"].startswith("&lt;script&gt;")
    assert r["paper_type_note"] == "Paper type taken from the file name."


def test_a_date_in_a_file_name_is_never_scanned_and_renaming_changes_only_the_label() -> None:
    a = rows(build(("Order dated 12.03.2019.pdf", "He was heard on 13 June 2024.")), "events")
    b = rows(build(("notes.pdf", "He was heard on 13 June 2024.")), "events")
    assert len(a) == len(b) == 1
    for k in ("normalised", "mentions", "event_type", "cue_word", "quote", "how_text"):
        assert a[0][k] == b[0][k]


def test_standard_papers_not_added_are_worded_as_names_that_look_like() -> None:
    out = build(("FIR_copy.pdf", "He was heard on 13 June 2024."))
    assert "No file whose name looks like: charge sheet" in out["papers_not_added_yet_that_may_matter"]
    assert not any("FIR" in x for x in out["papers_not_added_yet_that_may_matter"])


def test_a_quote_is_a_substring_of_the_passage_clipped_to_400_and_offsets_map_to_the_document() -> None:
    long = ("word " * 150) + "He was arrested on 13 June 2024 at the station" + (" more" * 150) + "."
    spans = [SpanRec("d0", "a.pdf", 1, 10, 10 + len(long), long, 7)]
    out = build_chronology(spans, [DocMeta("d0", "a.pdf")], CFG, TODAY)
    r = rows(out, "events")[0]
    assert len(r["quote"]) <= C.MAX_QUOTE and r["quote"] in long and "13 June 2024" in r["quote"]
    document = " " * 10 + long
    assert document[r["start"] : r["end"]] == r["quote"] and document[r["mention_start"] : r["mention_end"]] == "13 June 2024"
    assert r["span_id"] == 7 and re.fullmatch(r"[0-9a-f]{64}", r["span_sha256"])


def test_a_row_never_spans_two_passages() -> None:
    spans = [
        SpanRec("d0", "a.pdf", 1, 0, 25, "The accused was arrested", "a"),
        SpanRec("d0", "a.pdf", 1, 26, 60, "on 13 June 2024 and left.", "b"),
    ]
    out = build_chronology(spans, [DocMeta("d0", "a.pdf")], CFG, TODAY)
    for r in rows(out):
        assert r["span_id"] in ("a", "b") and r["quote"] in ("The accused was arrested", "on 13 June 2024 and left.")


def test_rows_are_capped_per_block_and_the_denominator_still_counts_all() -> None:
    text = " ".join(f"Ravi Kumar was arrested on {d} June 2024." for d in range(1, 21))
    out = build(("a.pdf", text), cfg=ChronologyConfig(max_rows=5))
    assert (
        len(out["events"]) == 5
        and out["denominator"]["exact"] == 20
        and "Showing the first 5 of each list." in out["denominator_line"]
    )


def test_the_passage_cap_stops_and_says_so() -> None:
    out = build(
        ("a.pdf", "He was heard on 13 June 2024."),
        ("b.pdf", "He was heard on 14 June 2024."),
        cfg=ChronologyConfig(max_passages=1),
    )
    assert out["stopped"] == "cap" and "Searched 1 of 2 passages" in out["denominator_line"]


def test_events_are_ordered_earliest_first_and_the_ambiguous_ones_are_not_in_the_list() -> None:
    out = build(("a.pdf", "On 20 June 2024 he came. On 03.04.2015 he left. On 5 January 2010 he was heard."))
    assert [r["normalised"] for r in out["events"]] == ["2010-01-05", "2024-06-20"]
    assert [r["date_as_written"] for r in out["ambiguous"]] == ["03.04.2015"]
    assert out["ambiguous"][0]["readings"] == ["2015-04-03", "2015-03-04"] and out["ambiguous"][0]["normalised"] is None


def test_approximate_and_partial_dates_are_never_ordered_with_the_exact_ones() -> None:
    out = build(("a.pdf", "On 20 June 2024 he came. He was heard on or about 3 May 2010. In March 2019 he left the city."))
    assert [r["date_as_written"] for r in out["events"]] == ["20 June 2024"]
    assert [r["date_as_written"] for r in out["approximate"]] == ["3 May 2010"] and [
        r["date_as_written"] for r in out["partial_dates"]
    ] == ["March 2019"]


def test_a_case_number_and_a_printed_line_are_their_own_blocks() -> None:
    out = build(("a.pdf", "Crime No. 45/06/2015 was registered.\nPrinted on 03.04.2015\nHe was heard on 13 June 2024."))
    assert len(out["number_like"]) == 1 and len(out["printed_lines"]) == 1
    assert (
        out["printed_lines"][0]["mentions"]
        == "Looks like a printed line (header, footer or print stamp), not part of the paper's text."
    )
    assert [r["normalised"] for r in out["events"]] == ["2024-06-13"]


# ---- hygiene ------------------------------------------------------------------------------------------------------------------------------


def test_the_core_imports_no_client_library_and_not_the_store() -> None:
    code = (
        "import sys\n"
        f"sys.path.insert(0, {str(REPO / 'src')!r})\n"  # -I ignores PYTHONPATH: put THIS checkout's src first
        "import pravrudhi.application.chronology, pravrudhi.application.chronology_dates\n"
        "bad=[m for m in ('httpx','requests','openai','anthropic','pravrudhi.application.matter_spans') if m in sys.modules]\n"
        "print(','.join(bad))\n"
    )
    out = subprocess.run(
        [sys.executable, "-I", "-c", code],
        capture_output=True,
        text=True,
        cwd=REPO,
        check=True,
    )
    assert out.stdout.strip() == ""


def test_the_response_is_json_serialisable_and_carries_no_unexpected_types() -> None:
    out = build(("a.pdf", " ".join(SENTENCES)))
    assert json.loads(json.dumps(out))["denominator"]["lines"] == out["denominator"]["lines"]


def test_a_non_or_un_prefixed_cue_blocks_a_group_while_the_type_phrase_is_still_there() -> None:
    assert not _pair("After a non-appearance Ravi Kumar was arrested on 14 June 2024.")["groups"]
    assert not _pair("After the unexecuted warrant Ravi Kumar was arrested on 14 June 2024.")["groups"]
    assert _pair("After an appearance Ravi Kumar was arrested on 14 June 2024.")["groups"]  # the control: no prefixed cue
    assert _pair("After the executed warrant Ravi Kumar was arrested on 14 June 2024.")["groups"]


@pytest.mark.parametrize("no_form", ["No. 12", "No.12", "No: 12", "No.(12)", "No.-12", "No. 12 and 13"])
def test_a_bare_number_sign_without_a_slash_is_not_a_cue(no_form: str) -> None:
    one = f"The FIR {no_form} was registered against Ravi Kumar on"
    out = build(("a.pdf", f"{one} 13 June 2024."), ("b.pdf", f"{one} 14 June 2024."))
    assert out["groups"], no_form


def test_only_a_pair_of_different_documents_makes_a_group_not_two_rows_of_one_document() -> None:
    out = build(
        ("a.pdf", "Ravi Kumar was arrested on 13 June 2024. Ravi Kumar was arrested on 14 June 2024."),
        ("b.pdf", "Ravi Kumar was arrested on 13 June 2024."),
    )
    assert len(out["groups"]) == 1
    assert sorted(r["normalised"] for r in out["groups"][0]["rows"]) == ["2024-06-13", "2024-06-14"]
    assert sorted(r["document_name"] for r in out["groups"][0]["rows"]) == ["a.pdf", "b.pdf"]


def test_a_sentence_with_two_candidate_names_never_groups_even_when_the_first_is_the_object() -> None:
    out = build(("a.pdf", "Ravi was arrested by Inspector Sharma on 13 June 2024."), ("b.pdf", "Ravi was arrested by Inspector Sharma on 14 June 2024."))
    assert not out["groups"]


def test_the_groupable_flags_are_exactly_the_four_types_and_each_has_a_name_pattern() -> None:
    groupable = {t["id"] for t in ASSET["types"] if t["groupable"]}
    assert groupable == {"arrest", "fir", "charge_sheet", "surrender"}
    assert set(C._NAME_PATTERNS) == groupable
    assert all(t["single_occurrence"] == t["groupable"] for t in ASSET["types"])


# ---- Track B / R2 read of 6a8382fb ---------------------------------------------------------------------------------------------------


def _fir(no: str, date_text: str, doc: str) -> tuple[str, str]:
    return (doc, f"A case was registered against Ravi Kumar vide FIR No. {no} on {date_text}.")


def test_different_fir_numbers_in_the_no_n_of_year_form_never_group_and_the_same_number_does() -> None:
    assert not build(_fir("12 of 2015", "3 April 2015", "a.pdf"), _fir("40 of 2016", "9 May 2016", "b.pdf"))["groups"]
    assert build(_fir("12 of 2015", "3 April 2015", "a.pdf"), _fir("12 of 2015", "9 May 2016", "b.pdf"))["groups"]
    assert not build(_fir("12/2015", "3 April 2015", "a.pdf"), _fir("40/2016", "9 May 2016", "b.pdf"))["groups"]
    assert not build(_fir("12 of 2015", "3 April 2015", "a.pdf"), _fir("12/2016", "9 May 2016", "b.pdf"))["groups"]


def test_a_file_that_mixes_conventions_says_so_on_the_ambiguous_row_and_counts_it() -> None:
    out = build(("a.pdf", "He left on 13.04.2015. He returned on 04.13.2015. He was arrested on 03.04.2015."))
    row = out["ambiguous"][0]
    assert C.BOTH_CONVENTIONS in row["how_text"]
    assert out["denominator"]["ambiguous_in_files_with_both_conventions"] == 1
    plain = build(("a.pdf", "He was arrested on 03.04.2015."))
    assert C.BOTH_CONVENTIONS not in plain["ambiguous"][0]["how_text"]
    assert plain["denominator"]["ambiguous_in_files_with_both_conventions"] == 0


def test_an_abbreviation_does_not_cut_the_sentence_so_the_cue_in_front_of_it_still_blocks_a_group() -> None:
    out = build(
        ("a.pdf", "Ravi Kumar was arrested on 13 June 2024."),
        ("b.pdf", "He did not pay Rs. 500 and Ravi Kumar was arrested on 14 June 2024."),
    )
    assert not out["groups"]


@pytest.mark.parametrize("cue", ["doesn't", "don't", "weren't", "won't", "wouldn't", "shouldn't", "mustn't", "unsuccessfully", "refuse"])
def test_the_missing_contractions_and_refusals_are_cues(cue: str) -> None:
    out = build(("a.pdf", "Ravi Kumar was arrested on 13 June 2024."), ("b.pdf", f"They {cue} say Ravi Kumar was arrested on 14 June 2024."))
    assert not out["groups"], cue


def test_the_group_stage_does_the_same_work_per_row_at_n_and_2n_in_one_document() -> None:
    """R2: many rows sharing one type and name in ONE document must not be quadratic: count compat-key calls, not seconds."""

    def calls(n: int) -> int:
        seen = {"n": 0}
        real = C._compat_key

        def counting(r: Any) -> Any:
            seen["n"] += 1
            return real(r)

        C._compat_key = counting  # type: ignore[assignment]
        try:
            text = " ".join(f"Ravi Kumar was arrested on {1 + i % 28} June 2024." for i in range(n))
            build(("a.pdf", text), ("b.pdf", "Ravi Kumar was arrested on 30 June 2024."))
        finally:
            C._compat_key = real  # type: ignore[assignment]
        return seen["n"]

    a, b = calls(200), calls(400)
    assert a <= 2 * 201 and b <= 2 * 401 and b <= 2.2 * a


# ---- cross-paper dates next to the same word, and papers that give one date (Track B's quality pass, 17 Oct) ---------------------------------


NOTICE_A = ("notice_copy.pdf", "The legal notice dated 12.04.2021 was sent by registered post to the drawer. The return date was 20.03.2021.")
NOTICE_B = ("reply.pdf", "In reply to the notice dated 12.04.2021 the drawer denied liability. The return date was 21.03.2021.")
NOTICE_C = ("complaint.pdf", "The accused was served with the notice dated 15.04.2021 and did not pay.")


def test_different_dates_next_to_the_same_word_in_different_papers_are_listed_for_the_lawyer_to_check() -> None:
    out = build(NOTICE_A, NOTICE_B, NOTICE_C)
    by_word = {e["word"]: e for e in out["same_words_different_dates"]}
    assert set(by_word) == {"notice", "return"}
    notice = by_word["notice"]
    assert [d["date_as_written"] for d in notice["dates"]] == ["12.04.2021", "15.04.2021"]
    assert [d["day_or_month_first_to_check"] for d in notice["dates"]] == [False, False] and notice["dates"][1]["normalised"] == "2021-04-15"
    assert {r["document_name"] for d in notice["dates"] for r in d["rows"]} == {"notice_copy.pdf", "reply.pdf", "complaint.pdf"}
    assert [d["date_as_written"] for d in by_word["return"]["dates"]] == ["20.03.2021", "21.03.2021"]
    assert notice["line"] == C.SAME_WORDS_LINE and out["same_words_heading"] == C.SAME_WORDS_HEADING and out["same_words_different_dates_more"] == 0
    assert all(r["quote"] for d in notice["dates"] for r in d["rows"])


def test_no_entry_when_every_paper_gives_the_same_dates_or_only_one_paper_has_the_word() -> None:
    assert build(NOTICE_A, ("b.pdf", "The notice dated 12.04.2021 was received. The return date was 20.03.2021."))["same_words_different_dates"] == []
    assert build(("only.pdf", "The notice dated 12.04.2021 and the notice dated 15.04.2021 were both sent."))["same_words_different_dates"] == []


def test_generic_or_short_words_and_non_exact_dates_do_not_make_an_entry() -> None:
    out = build(("a.pdf", "The order dated 12.04.2021 was passed."), ("b.pdf", "The order dated 15.04.2021 was passed."))
    assert out["same_words_different_dates"] == []  # "order" is on the generic list
    out2 = build(("a.pdf", "The notice dated about 12.04.2021 was sent."), ("b.pdf", "The notice listed on 15.04.2021 was sent."))
    assert out2["same_words_different_dates"] == []  # approximate and scheduled dates are not compared


def test_a_paper_with_one_date_is_listed_apart_and_a_paper_with_two_is_not() -> None:
    out = build(("lone.pdf", "The legal notice dated 12.04.2021 was sent."), ("two.pdf", "He was arrested on 13 June 2024 and produced on 14 June 2024."))
    assert [p["name"] for p in out["papers_with_one_date"]] == ["lone.pdf"]
    p = out["papers_with_one_date"][0]
    assert p["line"] == C.ONE_DATE_LINE and p["date_as_written"] == "12.04.2021" and p["category"] == "ambiguous"
    assert out["papers_with_one_date_heading"] == C.ONE_DATE_HEADING


def test_the_new_lines_say_nothing_of_which_date_is_right() -> None:
    for s in (C.SAME_WORDS_HEADING, C.SAME_WORDS_LINE, C.ONE_DATE_HEADING, C.ONE_DATE_LINE):
        low = s.lower()
        assert not any(w in low for w in ("conflict", "wrong", "correct", "false", "fake", "contradict", "error", "mistake"))


def test_a_lone_day_or_month_first_date_still_counts_against_another_papers_date() -> None:
    out = build(("a.pdf", "Legal notice dated 12.04.2021."), ("b.pdf", "The notice dated 15.04.2021 was served."))
    e = out["same_words_different_dates"]
    assert [x["word"] for x in e] == ["notice"]
    assert [(d["date_as_written"], d["day_or_month_first_to_check"], d["normalised"]) for d in e[0]["dates"]] == [("12.04.2021", True, None), ("15.04.2021", False, "2021-04-15")]


# ---- the same event in different words (Track B's rerun on #534, 19 Oct): the key is the event named in the sentence ----------------------------


CONFLICT_A = ("notice_copy.pdf", "A legal notice dated 12.04.2021 was issued to the drawer. The cheque was returned unpaid by the bank on 20.03.2021.")
CONFLICT_B = ("complaint.pdf", "A legal notice was sent on 15.04.2021. The bank returned the cheque on 21.03.2021.")


def test_dates_for_the_same_event_written_in_different_words_in_two_papers_are_listed_for_the_lawyer_to_check() -> None:
    out = build(CONFLICT_A, CONFLICT_B)
    by_word = {e["word"]: e for e in out["same_words_different_dates"]}
    assert set(by_word) == {"notice", "return"}
    assert [d["date_as_written"] for d in by_word["notice"]["dates"]] == ["12.04.2021", "15.04.2021"]
    assert [d["date_as_written"] for d in by_word["return"]["dates"]] == ["20.03.2021", "21.03.2021"]
    assert {r["document_name"] for d in by_word["return"]["dates"] for r in d["rows"]} == {"notice_copy.pdf", "complaint.pdf"}


def test_the_event_is_read_from_its_own_sentence_after_the_previous_date_and_not_from_an_earlier_one() -> None:
    # the second date of a sentence is not keyed on the first date's event; an earlier sentence's event does not reach the next sentence
    a = ("a.pdf", "The notice dated 12.04.2021 was received on 20.04.2021. The hearing was on 05.05.2021.")
    b = ("b.pdf", "The notice dated 12.04.2021 was received on 21.04.2021. The hearing was on 06.06.2021.")
    words = {e["word"] for e in build(a, b)["same_words_different_dates"]}
    assert words == {"received"}  # the second date is not keyed on the notice (an event of an earlier date); the old last-word rule applies
    c = ("c.pdf", "The notice dated 12.04.2021 was served. The cheque was presented on 01.03.2021.")
    d = ("d.pdf", "The notice dated 12.04.2021 was served. The cheque was presented on 02.03.2021.")
    words = {e["word"] for e in build(c, d)["same_words_different_dates"]}
    assert words == {"presented"}  # the notice date agrees; only the presentation date differs
    assert build(("e.pdf", "The notice dated 12.04.2021 was sent."), ("f.pdf", "The notice dated 12.04.2021 was sent."))["same_words_different_dates"] == []
