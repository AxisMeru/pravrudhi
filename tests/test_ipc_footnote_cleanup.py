# ruff: noqa: E501
"""Nine IPC entries kept a stray footnote digit glued to a word or an unmatched bracket from the PDF's footnote marks; they now read as
the printed words with the marks left out (the file's stated rule), one test each. The PDF's own typos stay as printed."""

from __future__ import annotations

import json

import pytest

from pravrudhi.application import section_lookup as L

BY_ID = {d["id"]: d for d in json.loads((L.ASSET_DIR / "ipc_bodies_v1.json").read_text(encoding="utf-8"))["documents"]}

# id -> (a fragment that must now be present, the stray fragment that must be gone)
CASES = {
    "4": ("Illustration\nA, who is a citizen of In", "Illustration\n3A, who"),
    "42": ("only to a particular part of India.", "part of [ India]"),
    "121": ("Illustration.\nA joins an insurrection", "Illustration.\n7A joins"),
    "124A": ("excite disaffection towards, the Government established", "2the Government"),
    "131": ('the words "officer", "soldier", "sailor" and "airman" include', '["soldier"'),
    "193": ("A trial before a Court-martial is a judicial proceeding.", "Court-martial 1is"),
    "410": ("in respect of which criminal breach of trust", "which 1criminal"),
    "482": ("Whoever uses any false property mark", "uses 1any"),
    "483": ("Whoever counterfeits any property mark used by", "any 3property"),
}


@pytest.mark.parametrize("sid", sorted(CASES))
def test_the_entry_has_no_stray_footnote_mark(sid: str) -> None:
    present, gone = CASES[sid]
    text = BY_ID[f"IPC/Section {sid}"]["text"]
    assert present in text and gone not in text


def test_the_pdfs_own_typos_stay_as_printed() -> None:
    assert "reasonable appreension" in BY_ID["IPC/Section 364A"]["text"]
    assert BY_ID["IPC/Section 490"]["title"].startswith("[Preach of contract")


def test_the_131_notes_keep_the_sections_other_brackets() -> None:
    assert "the Indian Navy (Discipline) Act" in BY_ID["IPC/Section 131"]["text"]


def test_a_page_break_inside_a_sentence_is_one_space_in_212_213_and_481() -> None:
    for sid in ("212", "213", "481"):
        assert "\x0c" not in BY_ID[f"IPC/Section {sid}"]["text"]
    assert "for any offence, or of his not proceeding" in BY_ID["IPC/Section 213"]["text"]
    assert "is said to use a false property mark." in BY_ID["IPC/Section 481"]["text"]


def test_the_source_note_lists_the_misprints_kept_as_printed() -> None:
    note = json.loads((L.ASSET_DIR / "ipc_bodies_v1.json").read_text(encoding="utf-8"))["source"]["parse_limits"]
    for word in ("appreension", "Preach", "imprinsonment"):
        assert word in note
    import hashlib

    sentence = note.split("page refs are PDF page numbers; ", 1)[1]  # R1 1c949ed330cf44ab: not worded as a complete list
    assert hashlib.sha256(sentence.encode()).hexdigest()[:16] == "1c949ed330cf44ab" and "misprints such as" in sentence
    assert "imprinsonment" in BY_ID["IPC/Section 57"]["text"]  # the entry itself is unchanged
