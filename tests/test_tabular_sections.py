# ruff: noqa: E501
"""S2 scanners (sections; courts and police stations): the closed grammar, one case per rule. Invented sentences only."""

from __future__ import annotations

import pytest

from pravrudhi.application import tabular_sections as TS


def secs(text: str) -> list[tuple[str, str | None, str, str | None]]:
    out = []
    for f in TS.scan_sections(text):
        assert text[f.start : f.end] == f.text  # a form is always the passage's own substring
        out.append((f.text, f.act, f.status, f.reason))
    return out


def places(text: str) -> list[tuple[str, str, str | None]]:
    out = []
    for f in TS.scan_places(text):
        assert text[f.start : f.end] == f.text
        out.append((f.text, f.status, f.reason))
    return out


def test_a_list_before_the_act_attaches_every_number_to_that_act_the_trailing_34_included() -> None:
    assert secs("under sections 323, 324 and 34 IPC") == [
        ("323", "IPC", "read", None),
        ("324", "IPC", "read", None),
        ("34", "IPC", "read", None),
    ]
    assert secs("under 323, 324 and 34 IPC") == [
        ("323", "IPC", "read", None),
        ("324", "IPC", "read", None),
        ("34", "IPC", "read", None),
    ]


@pytest.mark.parametrize("written", ["498A", "304B", "41A", "138-A", "482(2)", "154(1)(a)"])
def test_letter_suffixes_and_subsections_belong_to_the_number_and_are_read_whole(written: str) -> None:
    assert secs(f"under Section {written} IPC") == [(written, "IPC", "read", None)]


@pytest.mark.parametrize(
    "text",
    [
        "section 420/34 IPC",
        "section 379-382 IPC",
        "302 r/w 34 IPC",
        "under 420/34 IPC",
        "section 302 read with 34 IPC",
        "379 to 382 IPC",
    ],
)
def test_ranges_slashes_and_r_w_are_not_read_and_never_expanded(text: str) -> None:
    got = secs(text)
    assert len(got) == 1 and got[0][2:] == ("not_read", "form_not_supported") and got[0][1] is None


def test_each_listed_act_form_is_read_with_its_act_id() -> None:
    cases = {
        "Section 420 of the Indian Penal Code": "IPC", "Section 420 I.P.C.": "IPC", "s. 318 BNS": "BNS", "Section 318 of the Bharatiya Nyaya Sanhita": "BNS",
        "u/s 482 Cr.P.C.": "CrPC", "u/s 482 Cr. P. C.": "CrPC", "section 482 of the Code of Criminal Procedure": "CrPC", "Section 528 BNSS": "BNSS",
        "Section 65 of the Indian Evidence Act": "Indian Evidence Act", "section 45 of the Evidence Act": "Indian Evidence Act", "Section 63 BSA": "BSA",
        "Section 138 N.I. Act": "NI Act", "Section 138 of the Negotiable Instruments Act": "NI Act", "Sec. 138 NI Act": "NI Act",
    }  # fmt: skip
    for text, act in cases.items():
        got = secs(text)
        assert len(got) == 1 and got[0][1:] == (act, "read", None), (text, got)


def test_the_act_first_form_is_read_and_a_number_is_never_read_twice() -> None:
    assert secs("IPC, section 420 and BNS, Section 318(4).") == [("420", "IPC", "read", None), ("318(4)", "BNS", "read", None)]


def test_ipc_and_bns_are_both_shown_with_their_acts_and_never_mapped() -> None:
    got = secs("section 420 IPC and section 318 BNS")
    assert got == [("420", "IPC", "read", None), ("318", "BNS", "read", None)] and len({a for _, a, _, _ in got}) == 2


@pytest.mark.parametrize("act", ["the Act", "the said Act", "the Code"])
def test_a_bare_act_is_not_read_with_act_not_named(act: str) -> None:
    assert secs(f"section 3 of {act}") == [("3", None, "not_read", "act_not_named")]
    assert secs("section 4") == [("4", None, "not_read", "act_not_named")]


@pytest.mark.parametrize(
    "text",
    [
        "section 20 of the NDPS Act",
        "Section 6 POCSO Act",
        "section 3 of the Arms Act",
        "Section 12 of the Prevention of Corruption Act",
        "s. 7 SC/ST Act",
    ],
)
def test_a_section_of_an_act_not_on_the_list_is_not_read_never_dropped(text: str) -> None:
    got = secs(text)
    assert len(got) == 1 and got[0][1:] == (None, "not_read", "act_not_on_list")


def test_section_4_never_matches_inside_420_40_4a_or_498a_and_the_cue_is_never_inside_a_word() -> None:
    assert secs("Section 420 IPC") == [("420", "IPC", "read", None)]
    assert secs("Section 4A IPC") == [("4A", "IPC", "read", None)]
    assert secs("a bus 40 us in transit; Mr. S. Kumar") == []
    assert secs("alias section 4 IPC")[0][0] == "4"


def test_an_act_named_just_before_the_numbers_does_not_lend_the_act_that_follows() -> None:
    assert secs("BNS 318(4) and 5 BNSS") == []  # nothing is read rather than a number under the wrong Act


@pytest.mark.parametrize("text", ["धारा 302", "कलम 420 IPC"])
def test_non_english_cue_words_flag_the_passage(text: str) -> None:
    assert TS.non_english(text) is True
    assert TS.non_english("Section 420 IPC was cited.") is False


def test_devanagari_digits_are_not_read_as_section_numbers() -> None:
    assert secs("section ४२० IPC") == []


def test_a_date_looking_string_is_still_recognised_here_the_caller_excludes_what_a_date_claimed() -> None:
    assert secs("s. 12.03.2020 in the matter")[0][0] == "12"  # the engine drops it as claimed_by_date


# ---- places -------------------------------------------------------------------------------------------------------------------------


def test_courts_are_matched_as_printed_and_a_magistrate_shows_the_title_words_only() -> None:
    assert places("the Supreme Court of India and the High Court of Judicature at Madras and the High Court of Delhi") == [
        ("High Court of Judicature at Madras", "read", None), ("High Court of Delhi", "read", None), ("Supreme Court of India", "read", None),
    ] or {p[0] for p in places("the Supreme Court of India and the High Court of Judicature at Madras and the High Court of Delhi")} == {
        "Supreme Court of India", "High Court of Judicature at Madras", "High Court of Delhi"}  # fmt: skip
    got = places("before the Chief Judicial Magistrate Shri Ramesh Kumar and the Sessions Court")
    assert ("Chief Judicial Magistrate", "read", None) in got and ("Sessions Court", "read", None) in got
    assert not any("Ramesh" in t for t, _, _ in got)  # the words after the title are never in the cell


def test_a_high_court_not_on_the_closed_list_is_not_matched() -> None:
    assert places("the High Court of Atlantis") == []


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("registered at P.S. Karol Bagh, New Delhi", ("P.S. Karol Bagh", "read", None)),
        ("at Police Station Kotwali dated 3 May", ("Police Station Kotwali", "read", None)),
        ("PS Ambedkar Nagar under section 4", ("PS Ambedkar Nagar", "read", None)),
    ],
)
def test_a_station_name_is_word_for_word_up_to_its_terminator(text: str, expected: tuple[str, str, str | None]) -> None:
    assert expected in places(text)


@pytest.mark.parametrize(
    "text",
    [
        "P.S. Kotwali Smt. Devi under section 4",  # a title word before the terminator
        "Police Station, Inspector Sharma",  # a title word first
        "P.S. Kodungallur Town Circle Inspector Office",  # no comma, a rank word
        "P.S. one two three four five six seven eight",  # no terminator within six words
        "at P.S.",  # nothing after the cue
    ],
)
def test_a_station_name_with_no_clear_end_is_not_read_never_a_clean_looking_truncation(text: str) -> None:
    got = [p for p in places(text) if p[1] == "not_read"]
    assert got and got[0][2] == "name_not_clear" and not any(p[1] == "read" for p in places(text) if "P" in p[0][:1])


def test_the_closed_cue_file_is_what_the_scanner_uses() -> None:
    c = TS.cues()
    assert {a["id"] for a in c["acts"]} == {"IPC", "BNS", "CrPC", "BNSS", "Indian Evidence Act", "BSA", "NI Act"}
    assert c["station_max_words"] == 6 and c["station_terminators"] == [",", "dated", "under"]
    for form in (f for a in c["acts"] for f in a["forms"]):
        assert secs(f"Section 5 {form}")[0][1:] == (next(a["id"] for a in c["acts"] if form in a["forms"]), "read", None), form
