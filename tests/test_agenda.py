# ruff: noqa: E501
"""A5 slice 1: the s.138 review agenda. Pure: documents in, a page out. Invented file names only."""

from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from pravrudhi.application import agenda as G
from pravrudhi.application.agenda import AssetChanged, Doc, build_agenda

REPO = Path(__file__).resolve().parent.parent
HELD = json.loads((REPO / "src/pravrudhi/assets/s138/ni_act_s138_139.json").read_text(encoding="utf-8"))["s138_text"]


def doc(i: int, name: str, pages: int = 2, blank: tuple[int, ...] = ()) -> Doc:
    return Doc(f"{i:032x}", name, pages, blank)


def strings(node: Any, skip_statute: bool = True) -> list[str]:
    """Every emitted string, except the verbatim statute quotes (exempt: each is checked against the pinned text)."""
    out: list[str] = []
    if isinstance(node, str):
        out.append(node)
    elif isinstance(node, dict):
        for k, v in node.items():
            if skip_statute and k == "statute_words":
                continue
            out.extend(strings(v, skip_statute))
    elif isinstance(node, list):
        for v in node:
            out.extend(strings(v, skip_statute))
    return out


# ---- the pinned statute and the asset --------------------------------------------------------------------------------------------


def test_the_asset_is_pinned_to_the_held_s138_text_and_every_quote_is_in_it() -> None:
    a = G.load_asset()
    assert hashlib.sha256(HELD.encode("utf-8")).hexdigest() == a["statute"]["sha256"]
    quotes = [i["quote"] for i in a["ingredients"]] + [t["quote"] for t in a["tier_statute"] if t["quote"]]
    assert quotes and all(q in HELD for q in quotes)


def test_a_changed_statute_text_or_a_quote_that_is_not_in_it_stops_the_agenda(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    real = json.loads(G.AGENDA_ASSET.read_text(encoding="utf-8"))
    bad_sha, bad_quote = copy.deepcopy(real), copy.deepcopy(real)
    bad_sha["statute"]["sha256"] = "0" * 64
    bad_quote["ingredients"][0]["quote"] = "a sentence that is not in the section"
    try:
        for name, body in (("sha.json", bad_sha), ("quote.json", bad_quote)):
            f = tmp_path / name
            f.write_text(json.dumps(body), encoding="utf-8")
            monkeypatch.setattr(G, "AGENDA_ASSET", f)
            G.load_asset.cache_clear()
            with pytest.raises(AssetChanged):
                G.load_asset()
    finally:
        monkeypatch.undo()
        G.load_asset.cache_clear()
    assert G.load_asset()["statute"]["sha256"] == real["statute"]["sha256"]


def test_no_paper_is_mapped_to_an_ingredient() -> None:
    page = build_agenda([doc(1, "cheque.pdf")])
    for ing in page["ingredients"]:
        assert set(ing) == {"id", "proviso", "section", "statute_words"}


def test_the_ingredients_carry_their_proviso_letters_in_the_sections_own_words() -> None:
    page = build_agenda([doc(1, "cheque.pdf")])
    by = {i["id"]: i for i in page["ingredients"]}
    assert (by["presentation"]["proviso"], by["notice"]["proviso"], by["non_payment"]["proviso"]) == ("(a)", "(b)", "(c)")
    assert all(i["statute_words"] in HELD for i in page["ingredients"])


# ---- the fixed strings (R1's words, addenda 1 to 3) -------------------------------------------------------------------------------


def test_the_standing_source_status_and_not_assessed_lines_are_exact() -> None:
    p = build_agenda([doc(1, "cheque.pdf")])
    assert p["standing_line"] == (
        "A review agenda, not a finding. It lists the ingredients of the offence and the papers to look in. It says nothing about "
        "how the case will turn out. It works only from the papers you added; for a paper it cannot read, it tells you which paper to check."
    )
    assert p["source_line"] == (
        "Checked against the Negotiable Instruments Act, 1881, s.138 text held by us (India Code copy captured 24 Sep 2026). "
        "The ingredients below are the section's own words."
    )
    assert p["statement"] == "Built and tested on invented text only. No lawyer has used this on a live matter."
    assert p["not_assessed"] == [
        "Whether the papers show any ingredient.",
        "Papers that were not added or could not be read.",
        "How a court will read the facts.",
        "Anything not in the papers you added.",
    ]
    assert p["quotes_line"] == (
        "Quotes are kept as printed in the India Code copy we hold, including its numbers, square brackets and any typing slips."
    )
    assert "where_to_look" not in p["headings"] and "Where to look" not in json.dumps(p)
    assert p["statute_names"]["heading"] == "Papers the statute names for this offence"
    assert p["not_added"]["heading"] == "Papers not added yet that may matter"
    assert p["papers_read"]["heading"] == "Papers read (1)"


def test_the_complaint_row_says_s142_is_not_held_and_has_no_status() -> None:
    row = next(r for r in build_agenda([doc(1, "x.pdf")])["statute_names"]["rows"] if r["id"] == "complaint")
    assert row["label"] == "The complaint: named in s.142, which we do not hold, so we cannot list what it names."
    assert row["status"] is None and row["statute_words"] is None


def test_the_refusal_for_another_offence_is_exact() -> None:
    with pytest.raises(ValueError) as e:
        build_agenda([doc(1, "x.pdf")], offence="s420_ipc")
    assert str(e.value) == (
        "No agenda for this offence yet. For now this page covers cheque dishonour under s.138 only. "
        "For any other offence, a lawyer reads the papers."
    )


# ---- the barred words (a test per phrase) -----------------------------------------------------------------------------------------


@pytest.mark.parametrize("phrase", G.BARRED)
def test_each_barred_phrase_is_caught_in_a_sentence(phrase: str) -> None:
    assert G.barred_in(f"This is a {phrase} here.") == [phrase.lower()]
    assert G.barred_in(f"{phrase.upper()}.") == [phrase.lower()]


@pytest.mark.parametrize("fine", ["Completed forms", "a scorecard", "preproved", "settlement", "Papers read (3)"])
def test_the_barred_scan_matches_whole_words_only(fine: str) -> None:
    assert G.barred_in(fine) == []


def test_no_barred_word_is_in_any_emitted_string_or_asset_string_outside_the_statute_quotes() -> None:
    pages = [
        build_agenda([]),
        build_agenda([doc(1, "cheque.pdf"), doc(2, "notice.pdf"), doc(3, "scan.pdf", 3, (1, 2, 3))]),
        build_agenda([doc(1, "x.pdf")], {doc(1, "x.pdf").doc_id: "cheque"}),
    ]
    for page in pages:
        assert [s for s in strings(page) if G.barred_in(s)] == []
    asset = G.load_asset()
    assert [s for s in strings({k: v for k, v in asset.items() if k not in ("ingredients",)}) if G.barred_in(s)] == []
    for name in ("STANDING_LINE", "SOURCE_LINE", "DATA_STATEMENT", "OWNERSHIP_LINE", "QUOTES_LINE", "NONE_OUTSTANDING", "REFUSAL", "NO_PAPERS", "BAD_TYPE"):
        assert G.barred_in(getattr(G, name)) == [], name


# ---- papers: declared types, the file-name fallback and the two tiers -----------------------------------------------------------


def test_a_declared_type_wins_and_the_row_shows_the_basis() -> None:
    d = doc(1, "notice.pdf")
    p = build_agenda([d], {d.doc_id: "cheque"})
    rows = {r["id"]: r for r in p["statute_names"]["rows"]}
    assert rows["cheque"]["status"] == "Added" and rows["notice_in_writing"]["status"] == "Not added yet"
    assert rows["cheque"]["papers"][0]["paper_type_basis"] == "Paper type declared by you"


def test_the_file_name_is_a_stated_fallback_only() -> None:
    p = build_agenda([doc(1, "Demand Notice 2019.pdf"), doc(2, "scan_0034.pdf")])
    rows = {r["id"]: r for r in p["statute_names"]["rows"]}
    assert rows["notice_in_writing"]["status"] == "A file named like this was added (type taken from the file name)"
    assert rows["notice_in_writing"]["papers"][0]["paper_type_basis"] == "Paper type taken from the file name"
    named = {x["name"]: x for x in p["papers_read"]["papers"]}
    assert named["scan_0034.pdf"]["paper_type"] is None and named["scan_0034.pdf"]["paper_type_basis"] is None


def test_every_outstanding_row_is_listed_under_not_added_and_the_none_line_appears_only_when_none_is() -> None:
    a, b, c = doc(1, "x1.pdf"), doc(2, "x2.pdf"), doc(3, "x3.pdf")
    full = build_agenda([a, b, c], {a.doc_id: "cheque", b.doc_id: "demand_notice", c.doc_id: "bank_return_information"})
    assert full["not_added"]["rows"] == []
    assert full["not_added"]["none_line"] == (
        "No statute-named paper is outstanding among the paper types you declared. The complaint is named in s.142, which we do not hold."
    )
    part = build_agenda([a], {a.doc_id: "cheque"})
    assert [r["id"] for r in part["not_added"]["rows"]] == ["notice_in_writing", "bank_information"]
    assert part["not_added"]["none_line"] is None


def test_re_running_after_adding_a_paper_changes_the_list() -> None:
    a = doc(1, "x1.pdf")
    before = build_agenda([a], {a.doc_id: "cheque"})
    b = doc(2, "x2.pdf")
    after = build_agenda([a, b], {a.doc_id: "cheque", b.doc_id: "demand_notice"})
    assert len(before["not_added"]["rows"]) == 2 and len(after["not_added"]["rows"]) == 1


def test_a_file_added_but_not_read_is_listed_not_counted_and_never_fills_a_row() -> None:
    d = doc(1, "Cheque 12.pdf", pages=2, blank=(1, 2))
    p = build_agenda([d], {d.doc_id: "cheque"})
    assert p["papers_read"]["n"] == 0 and p["papers_read"]["heading"] == "Papers read (0)"
    assert p["statute_names"]["rows"][0]["status"] == "Not added yet"
    assert {"line": "Not added yet: a file named 'Cheque 12.pdf' was added but not read (scanned pages), so check it yourself."} in p["not_added"]["rows"]
    assert p["not_added"]["none_line"] is None


def test_a_file_with_text_on_some_pages_is_read() -> None:
    assert build_agenda([doc(1, "a.pdf", 3, (2,))])["papers_read"]["n"] == 1


def test_a_file_name_is_escaped_for_the_page() -> None:
    p = build_agenda([doc(1, 'a<b>&"c".pdf')])
    assert p["papers_read"]["papers"][0]["name"] == "a&lt;b&gt;&amp;&quot;c&quot;.pdf"


@pytest.mark.parametrize("declared", [{"f" * 32: "cheque"}, {"0" * 31 + "1": "not-a-type"}])
def test_a_declared_type_for_a_missing_paper_or_an_unlisted_type_is_refused(declared: dict[str, str]) -> None:
    with pytest.raises(ValueError):
        build_agenda([doc(1, "a.pdf")], declared)


def test_the_lawyer_tier_is_hidden_while_unconfirmed_and_shown_with_its_basis_once_confirmed(monkeypatch: pytest.MonkeyPatch) -> None:
    a = doc(1, "a.pdf")
    hidden = build_agenda([a])
    assert hidden["lawyer_listed"] is None and hidden["lawyer_confirmed"] is False
    assert "Papers a lawyer has listed" not in json.dumps(hidden)
    asset = copy.deepcopy(G.load_asset())
    asset["lawyer_confirmed"], asset["confirmed_by"], asset["confirmed_on"] = True, "invented lawyer", "2026-10-20"
    monkeypatch.setattr(G, "load_asset", lambda: asset)
    shown = build_agenda([a])
    assert shown["lawyer_listed"]["heading"] == "Papers a lawyer has listed for this offence"
    assert {r["basis"] for r in shown["lawyer_listed"]["rows"]} == {"lawyer_practice"}
    assert shown["lawyer_listed"]["confirmed_by"] == "invented lawyer"
    assert all(r["basis"] == "statute_text" for r in shown["statute_names"]["rows"])


def test_tier_one_holds_only_rows_whose_words_are_in_the_statute() -> None:
    for r in build_agenda([doc(1, "a.pdf")])["statute_names"]["rows"]:
        assert r["basis"] == "statute_text"
        assert r["statute_words"] is None or r["statute_words"] in HELD


def test_the_core_imports_no_client_library_and_not_the_store() -> None:
    code = (
        "import sys\n"
        f"sys.path.insert(0, {str(REPO / 'src')!r})\n"
        "import pravrudhi.application.agenda\n"
        "bad=[m for m in ('httpx','requests','openai','anthropic','pravrudhi.application.matter_spans') if m in sys.modules]\n"
        "print(','.join(bad))\n"
    )
    out = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, cwd=REPO, check=True)
    assert out.stdout.strip() == ""


def test_an_unread_file_blocks_the_none_outstanding_line_even_when_every_statute_paper_is_declared() -> None:
    a, b, c, d = doc(1, "x1.pdf"), doc(2, "x2.pdf"), doc(3, "x3.pdf"), doc(4, "scan.pdf", 2, (1, 2))
    p = build_agenda([a, b, c, d], {a.doc_id: "cheque", b.doc_id: "demand_notice", c.doc_id: "bank_return_information"})
    assert p["not_added"]["none_line"] is None and len(p["not_added"]["rows"]) == 1


@pytest.mark.parametrize("name", ["noticeboard.pdf", "returnable.pdf", "chqueue.pdf", "demanding.pdf", "loanword.pdf"])
def test_a_file_name_that_merely_contains_a_type_word_is_not_typed(name: str) -> None:
    assert build_agenda([doc(1, name)])["papers_read"]["papers"][0]["paper_type"] is None


def test_once_confirmed_the_lawyer_tier_rows_that_are_outstanding_are_listed_too(monkeypatch: pytest.MonkeyPatch) -> None:
    asset = copy.deepcopy(G.load_asset())
    asset["lawyer_confirmed"] = True
    monkeypatch.setattr(G, "load_asset", lambda: asset)
    a = doc(1, "x.pdf")
    p = build_agenda([a], {a.doc_id: "cheque"})
    ids = [r["id"] for r in p["not_added"]["rows"]]
    assert "notice_proof" in ids and "debt_paper" in ids and "bank_statement" in ids


def test_each_row_is_tagged_by_its_basis_not_with_a_blanket_source(monkeypatch: pytest.MonkeyPatch) -> None:
    p = build_agenda([doc(1, "a.pdf")])
    assert {r["tag"] for r in p["statute_names"]["rows"]} == {"Named in the section"}
    assert "From our list of papers" not in json.dumps(p)
    asset = copy.deepcopy(G.load_asset())
    asset["lawyer_confirmed"] = True
    monkeypatch.setattr(G, "load_asset", lambda: asset)
    q = build_agenda([doc(1, "a.pdf")])
    assert {r["tag"] for r in q["lawyer_listed"]["rows"]} == {"From a lawyer's list"}
    assert {r["tag"] for r in q["statute_names"]["rows"]} == {"Named in the section"}


def test_the_ownership_line_is_emitted_only_when_asked_for_and_names_both_pages() -> None:
    assert build_agenda([doc(1, "a.pdf")])["ownership_line"] is None
    on = build_agenda([doc(1, "a.pdf")], ownership=True)["ownership_line"]
    assert on == G.OWNERSHIP_LINE and "chronology page" in on and "cheque checklist" in on


@pytest.mark.parametrize("name", ["Notice of cheque return.pdf", "legal notice dishonour of cheque.pdf", "cheque_return_memo.pdf"])
def test_a_file_name_that_fits_two_types_gets_no_type_and_never_fills_the_cheque_row(name: str) -> None:
    p = build_agenda([doc(1, name)])
    assert p["papers_read"]["papers"][0]["paper_type"] is None
    rows = {r["id"]: r for r in p["statute_names"]["rows"]}
    assert rows["cheque"]["status"] == "Not added yet" and rows["notice_in_writing"]["status"] == "Not added yet"


@pytest.mark.parametrize(("name", "row"), [("Cheque.pdf", "cheque"), ("Demand notice.pdf", "notice_in_writing"), ("return memo.pdf", "bank_information")])
def test_a_file_name_that_fits_exactly_one_type_still_gets_it_with_the_fallback_basis(name: str, row: str) -> None:
    p = build_agenda([doc(1, name)])
    got = {r["id"]: r for r in p["statute_names"]["rows"]}[row]
    assert got["status"] == "A file named like this was added (type taken from the file name)"
    assert got["papers"][0]["paper_type_basis"] == "Paper type taken from the file name"


def test_a_declared_type_still_wins_for_a_name_that_fits_two_types() -> None:
    d = doc(1, "Notice of cheque return.pdf")
    p = build_agenda([d], {d.doc_id: "cheque"})
    assert {r["id"]: r for r in p["statute_names"]["rows"]}["cheque"]["status"] == "Added"


# ---- Track B's G1, G2, G3, G5, G8 ------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name", ["Notice_of_Motion.pdf", "Income tax return.pdf", "Office memo.pdf", "Return_of_service.pdf", "Payment receipt.pdf", "cheque_return_memo.pdf"]
)
def test_loose_or_clashing_file_names_get_no_type(name: str) -> None:
    assert build_agenda([doc(1, name)])["papers_read"]["papers"][0]["paper_type"] is None, name


@pytest.mark.parametrize(
    ("name", "ptype"),
    [("Bank_Statement.pdf", "bank_statement"), ("bank-statement-2019.pdf", "bank_statement"), ("CHEQUE no 12.pdf", "cheque"), ("Speed_Post_receipt.pdf", "notice_proof")],
)
def test_the_name_is_split_into_words_so_separators_do_not_matter(name: str, ptype: str) -> None:
    assert build_agenda([doc(1, name)])["papers_read"]["papers"][0]["paper_type"] == ptype


def test_a_name_only_row_never_reads_as_plain_added_and_never_gives_the_none_line() -> None:
    a, b, c = doc(1, "Cheque.pdf"), doc(2, "Demand notice.pdf"), doc(3, "return memo.pdf")
    p = build_agenda([a, b, c])
    assert {r["status"] for r in p["statute_names"]["rows"] if r["id"] != "complaint"} == {G.NAME_ONLY}
    assert p["not_added"]["none_line"] is None  # G3: no declaration, only guesses
    q = build_agenda([a, b, c], {a.doc_id: "cheque", b.doc_id: "demand_notice", c.doc_id: "bank_return_information"})
    assert q["not_added"]["none_line"] is not None


def test_a_paper_held_without_a_file_is_declared_held_and_is_not_outstanding() -> None:
    a = doc(1, "x.pdf")
    p = build_agenda([a], {a.doc_id: "other"}, held_without_file=["cheque", "demand_notice", "bank_return_information"])
    rows = {r["id"]: r for r in p["statute_names"]["rows"]}
    assert {rows[k]["status"] for k in ("cheque", "notice_in_writing", "bank_information")} == {"Declared by you; no file added"}
    assert p["not_added"]["rows"] == [] and p["not_added"]["none_line"] is not None
    only = build_agenda([a], held_without_file=["cheque"])
    assert {r["id"]: r for r in only["statute_names"]["rows"]}["notice_in_writing"]["status"] == "Not added yet"


@pytest.mark.parametrize("bad", [["not-a-type"], ["other"], ["cheque", "zzz"]])
def test_a_held_type_that_is_not_in_the_list_is_refused(bad: list[str]) -> None:
    with pytest.raises(ValueError):
        build_agenda([doc(1, "x.pdf")], held_without_file=bad)


def test_a_declared_type_beats_a_held_declaration_and_a_file_for_the_same_type_is_added() -> None:
    d = doc(1, "x.pdf")
    p = build_agenda([d], {d.doc_id: "cheque"}, held_without_file=["cheque"])
    assert {r["id"]: r for r in p["statute_names"]["rows"]}["cheque"]["status"] == "Added"


def test_pages_with_no_text_are_stated_on_the_paper_and_a_zero_page_file_is_not_read() -> None:
    p = build_agenda([doc(1, "a.pdf", 10, (3, 4, 5)), doc(2, "zero.pdf", 0)])
    assert p["papers_read"]["papers"][0]["pages_note"] == "3 of 10 pages had no text"
    assert p["papers_read"]["n"] == 1 and any("zero.pdf" in r.get("line", "") for r in p["not_added"]["rows"])
    assert build_agenda([doc(1, "a.pdf", 2)])["papers_read"]["papers"][0]["pages_note"] is None


@pytest.mark.parametrize("word", ["missing", "gap", "lacking", "deficient", "defect", "weak", "strong", "fatal"])
def test_the_added_barred_words_are_caught_and_the_longer_phrase_wins(word: str) -> None:
    assert G.barred_in(f"There is a {word} here.") == [word]
    assert G.barred_in("a weak case") == ["weak case"] and G.barred_in("missing evidence") == ["missing evidence"]
