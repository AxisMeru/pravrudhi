# ruff: noqa: E501
"""The page-break leftovers are out of the IPC bodies: one test per shape on the script's own function (a plain regular expression on
one entry's text, everything else untouched), and the shipped file has no form feed and none of the leaked headings. Statute text
fragments are public law."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from pravrudhi.application import section_lookup as L

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import ipc_page_artifacts as A  # noqa: E402

DOCS = json.loads((L.ASSET_DIR / "ipc_bodies_v1.json").read_text(encoding="utf-8"))["documents"]
BY_ID = {d["id"].rsplit(" ", 1)[1]: d for d in DOCS}


def _clean(text: str, own: str = "9", nxt: str | None = "10") -> tuple[str, dict[str, int]]:
    counts: dict[str, int] = {}
    return A.clean(text, own, nxt, counts), counts


def test_shape_1_a_page_break_inside_a_sentence_is_one_space() -> None:
    for raw in ("would \x0cbe punishable", "the \x0c rights of the people", "would\x0cbe punishable"):
        got, c = _clean(raw)
        assert "\x0c" not in got and "  " not in got and c == {"mid_sentence": 1}
    assert _clean("would \x0cbe punishable")[0] == "would be punishable"


def test_shape_2_the_next_sections_number_at_the_top_of_the_page_is_removed() -> None:
    assert _clean("committed within India. \x0c4.", "3", "4") == ("committed within India.", {"next_number": 1})
    assert _clean("committed within India. \x0c5.", "3", "4")[0] == "committed within India. 5."  # not the next section's number: kept (a form feed becomes a space)


def test_shape_3_the_next_sections_number_and_margin_titles_at_the_end_are_removed() -> None:
    got, c = _clean('part of India.\n43.\n"Illegal".\n"Legally bound to do".', "42", "43")
    assert got == "part of India." and c == {"next_headings": 1}
    assert _clean('part of India.\n43.\n"Illegal".\nMore text.', "42", "43")[0].endswith("More text.")  # not at the end: kept


def test_shape_4_a_footnote_continuation_at_a_page_top_is_removed_in_section_25_only() -> None:
    assert _clean("but not otherwise. \x0c 1964, s. 2.", "25", "26") == ("but not otherwise.", {"footnote_continuation": 1})
    assert "1964, s. 2." in _clean("but not otherwise. \x0c 1964, s. 2.", "26", "27")[0]


def test_shape_5_a_page_number_at_a_page_top_is_removed_in_section_89_only() -> None:
    assert _clean("Provided- \x0c116\nProvisos.\nProvisos.-First", "89", "90")[0] == "Provided- Provisos.\nProvisos.-First"


def test_shape_6_the_footnote_rule_at_a_page_top_is_removed() -> None:
    got, c = _clean("or with both. \x0c" + "-" * 70 + "\nIllustrations\n(a) A takes", "403", "404")
    assert got == "or with both.\nIllustrations\n(a) A takes" and c == {"footnote_rule": 1}


def test_the_shipped_file_has_none_of_the_leftovers() -> None:
    assert not [d["id"] for d in DOCS if "\x0c" in d["text"] or "\x0c" in d["title"]]
    ids = [d["id"].rsplit(" ", 1)[1] for d in DOCS]
    import re

    leaked = [ids[i] for i, d in enumerate(DOCS[:-1]) if re.search(r"(?:^|\n)" + re.escape(ids[i + 1]) + r"\.(?:\n|$)", d["text"])]
    assert leaked == []


def test_the_entries_read_as_the_printed_sentences() -> None:
    assert BY_ID["3"]["text"].endswith("committed within India.")
    assert BY_ID["42"]["text"].endswith("a particular part of India.") and BY_ID["48"]["text"].endswith("human beings or of property.")
    assert BY_ID["25"]["text"].endswith("but not otherwise.") and "Provided- Provisos." in BY_ID["89"]["text"]
    assert "or with both.\nIllustrations\n(a) A takes property" in BY_ID["403"]["text"]
    assert "would be punishable under" in BY_ID["4"]["text"] and "the rights of the people" in BY_ID["21"]["text"]
