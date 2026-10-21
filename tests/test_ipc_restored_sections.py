# ruff: noqa: E501
"""The ten IPC sections the old `ipc_pdf_parse` lost (a title that crossed a blank line swallowed the next section's heading; a
number with no dot, a title that ends in ';--' or has no full stop, a leading footnote mark) are in the shipped bodies file with
the PDF's own words, one test each; the entries that had swallowed their neighbour no longer carry it; and each failure mode is
reproduced on a few lines of the PDF's text layout. The statute text is public law; nothing here is invented."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from pravrudhi.application import section_lookup as L

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import ipc_pdf_parse as P  # noqa: E402

ASSET = json.loads((L.ASSET_DIR / "ipc_bodies_v1.json").read_text(encoding="utf-8"))
BY_ID = {d["id"]: d for d in ASSET["documents"]}

# id -> (title, the text's first words, the text's last words)
RESTORED = {
    "17": ('"Government"', 'The word "Government" denotes the Central Government', "the Government of a State."),
    "63": ("Amount of fine", "Where no sum is expressed to which a fine may extend", "but shall not be excessive."),
    "139": ("Persons subject to certain Acts", "No person subject to the Army Act", "for any of the offences defined in this Chapter."),
    "158": ("Being hired to take part in an unlawful assembly or riot", "Whoever is engaged, or hired, or offers", "or with both."),
    "213": ("Taking gift, etc., to screen an offender from punishment", "Whoever accepts or attempts to obtain", "or with both."),
    "227": ("Violation of condition of remission of punishment", "Whoever, having accepted any conditional remission",
            "as he has not already suffered."),
    "481": ("Using a false property mark", "Whoever marks any movable property or goods", "is said to use a false \x0cproperty mark."),
    "490": ("[Preach of contract of service during voyage or journey.", "Rep. by the Workmen's Breach of Contract (Repealing) Act", "s. 2 and Sch."),
    "479": ("Property mark", "A mark used for denoting that movable property belongs", "is called a property mark."),
    "364A": ("Kidnapping for ransom, etc", "Whoever kidnaps or abducts any person or keeps a person in detention", ""),
}  # fmt: skip


@pytest.mark.parametrize("sid", sorted(RESTORED))
def test_the_section_is_held_with_the_pdfs_words(sid: str) -> None:
    title, first, last = RESTORED[sid]
    d = BY_ID[f"IPC/Section {sid}"]
    assert d["title"] == title and d["text"].startswith(first) and d["text"].endswith(last)
    assert d["act"] == "Indian Penal Code, 1860" and d["section"] == f"Section {sid}" and isinstance(d["pdf_page"], int)


def test_a_section_that_had_swallowed_its_neighbour_no_longer_carries_it() -> None:
    assert BY_ID["IPC/Section 62"]["text"] == "Rep., by s. 4 ibid."  # it held section 63's text
    for sid in ("14", "57", "138A", "157", "212", "226", "364", "478", "480"):
        text = BY_ID[f"IPC/Section {sid}"]["text"]
        assert "Amount of fine" not in text and "Property mark" not in text.replace("Counterfeiting", "")
    assert "158." not in BY_ID["IPC/Section 157"]["text"] and "15." not in BY_ID["IPC/Section 14"]["text"].replace("1950", "")
    assert BY_ID["IPC/Section 57"]["text"].endswith("twenty years.")
    assert not BY_ID["IPC/Section 226"]["text"].startswith("Whoever")  # it held section 227's text
    assert BY_ID["IPC/Section 478"]["text"].endswith("(w. e. f. 25-11-1959).")


def test_the_unreliable_list_keeps_only_what_is_still_not_shipped() -> None:
    assert ASSET["unreliable_ids_not_shipped"] == ["IPC/Section 124", "IPC/Section 142"]
    assert not [i for i in ASSET["unreliable_ids_not_shipped"] if i in BY_ID]
    assert ASSET["source"]["sha256"] == "f25d61547212f152dda8c96b19ac05a286f3a9eb6a95490ab55ec40aa4f53197"


def test_the_source_note_names_only_the_one_pdf_and_nothing_about_other_texts() -> None:
    note = json.dumps(ASSET["source"]).lower()
    assert "mha" in note or "ministry of home affairs" in note
    for word in ("il-tur", "iltur", "mirror", "training", "dataset", "compare notes", "not reconciled"):
        assert word not in note


# ---- the parser, on a few lines of the PDF's layout ------------------------------------------------------------------------


def _parse(layout: str) -> dict[str, dict[str, str]]:
    return P.parse(layout)


def test_a_repealed_section_does_not_swallow_the_next_sections_heading_and_text() -> None:
    layout = (
        "     62. [Forfeiture of property.] Rep., by s. 4 ibid.\n\x0c63.\n\n\nAmount of fine.\n\n\n"
        "     63. Amount of fine.--Where no sum is expressed, the amount of fine is unlimited.\n\n\n"
        "64.\n\n\nSentence.\n\n\n     64. Sentence.--In every case the offender may be sentenced.\n"
    )
    got = _parse(layout)
    assert got["62"]["text"] == "Rep., by s. 4 ibid." and got["63"]["title"] == "Amount of fine"
    assert got["63"]["text"] == "Where no sum is expressed, the amount of fine is unlimited."


def test_a_title_may_end_in_a_semicolon_or_a_number_may_stand_without_its_dot() -> None:
    layout = (
        "     157. Harbouring persons.--Whoever harbours any person.\n\n\n"
        "158.\n\n\nBeing hired;\n\n\n     158. Being hired to take part in a riot;--\nWhoever is engaged, shall be punished.\n\n\n"
        "     159. Affray.--When two or more persons fight.\n"
        '\n\n     2*[17 "Government".--The word "Government"     denotes   the   Central\nGovernment or the Government of a 3****State.]\n'
    )
    got = _parse(layout)
    assert got["158"]["title"] == "Being hired to take part in a riot" and got["158"]["text"] == "Whoever is engaged, shall be punished."
    assert got["17"]["text"] == 'The word "Government" denotes the Central Government or the Government of a State.'  # glued mark gone


def test_a_section_at_the_left_margin_and_a_leading_footnote_mark_are_starts() -> None:
    layout = (
        "     212. Harbouring offender.--Whoever harbours an offender.\n\n\n"
        "213. Taking gift, etc., to screen an offender from punishment--Whoever\naccepts any gratification.\n\n\n"
        "     214. Offering gift.--Whoever gives any gratification.\n\n\n"
        "     478. [Trade Mark.] Rep. by the Trade Act, 1958.\n\n\n\n"
        "479.\n\n\nProperty mark.\n\n\n     1*479. Property mark.--A mark used for denoting property is called a property mark.\n\n\n"
        "     480. [Using a false mark.] Rep. by the Trade Act, 1958.\n"
    )
    got = _parse(layout)
    assert got["213"]["text"] == "Whoever accepts any gratification."
    assert got["479"]["title"] == "Property mark" and got["478"]["text"] == "Rep. by the Trade Act, 1958."
    assert got["212"]["text"] == "Whoever harbours an offender."
