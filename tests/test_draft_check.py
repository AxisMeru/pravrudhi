"""A1 whole-draft citation check (wf_a1_draft_citation_check): the pure parts. Invented parties and invented citations only.

Every rule has both arms. The acceptance tests are the parser-drop cases: a draft whose citations the parser cannot read must
never come back as "no problems" without saying how much was not read."""

from __future__ import annotations

import base64
from typing import Any

import pytest

from pravrudhi.application import draft_check as D
from pravrudhi.application.draft_check_config import DraftCheckConfig, parse_draft_check_config

CFG = DraftCheckConfig()
GOOD = "(2087) 4 SCC 512"  # an invented citation the fake index "has"


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
        assert out["state"] == "incomplete"
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
        ("line_break_hyphen", "as held in (2087) 4 S-\nCC 512 the court said"),
        ("ocr_digit_in_year", "as held in (2O87) 4 SCC 512 the court said"),
        ("ocr_digit_in_page", "as held in (2087) 4 SCC 5l2 the court said"),
        ("zero_width_character", "as held in (2087) 4 S​CC 512 the court said"),
        ("bracket_year", "as held in [2087] 4 SCC 512 the court said"),
        ("manu", "as held in MANU/SC/0123/2087 the court said"),
        ("cri_lj", "as held in 2087 Cri LJ 1234 the court said"),
        ("soft_hyphen", "as held in (2087) 4 S­CC 512 the court said"),
    ],
)
def test_a_citation_the_parser_cannot_read_is_listed_as_not_read_and_the_draft_is_incomplete(name: str, text: str) -> None:
    out = run(text)
    assert out["denominator"]["read"] == 0, name
    assert out["denominator"]["not_read"] == 1, name
    assert out["state"] == "incomplete" and out["not_read"][0]["reason"] == "form_not_read"
    assert "no problems" not in out["summary"].lower() or "incomplete" in out["summary"].lower()
    assert "0 problems" not in out["summary"]


def test_the_same_draft_with_a_readable_citation_is_complete_and_says_so() -> None:
    out = run(f"as held in {GOOD} the court said")
    assert out["state"] == "complete" and out["denominator"]["not_read"] == 0 and out["denominator"]["found"] == 1
    assert out["summary"] == "No problems found among the 1 citations read."


def test_one_readable_and_one_unreadable_citation_are_counted_apart_even_when_close() -> None:
    out = run(f"see {GOOD}, (2O87) 4 SCC 512 and again later")
    d = out["denominator"]
    assert (d["read"], d["not_read"], d["like_strings"]) == (1, 1, 2)
    assert out["state"] == "incomplete" and "1 string" in out["summary"]


def test_a_manu_and_a_cri_lj_next_to_a_good_citation_are_not_swallowed_by_it() -> None:
    out = run(f"see MANU/SC/0123/2087 and 2087 Cri LJ 1234 and {GOOD} together")
    assert out["denominator"]["read"] == 1 and out["denominator"]["not_read"] == 2


def test_words_that_only_resemble_a_reporter_are_not_citations() -> None:
    out = run("The air was heavy; scr and scc are lower-case words; AIR India and SCALE models were discussed at length.")
    assert out["denominator"]["like_strings"] == 0 and out["summary"] == "No citation was found in this draft."


def test_the_summary_never_reads_no_problems_without_the_unread_count() -> None:
    for text in ("see [2087] 4 SCC 512", f"see {GOOD} and MANU/SC/0123/2087"):
        s = run(text)["summary"]
        assert "incomplete" in s and ("not read" in s)


# ---- the denominator and the report -----------------------------------------------------------------------------------------


def test_every_status_is_counted_and_listed() -> None:
    text = f"{GOOD}; (2087) 4 SCC 999; (2087) 4 SCC 777; AIR 2087 SC 1234."
    out = run(text)
    d = out["denominator"]
    got = (d["found"], d["not_found"], d["conflict"], d["not_checked"], d["not_checked_reporter_not_in_index"])
    assert got == (1, 1, 1, 1, 1)
    assert [c["status"] for c in out["citations"]] == ["found", "not_found", "conflict", "not_checked"]
    assert out["summary"] == (
        "2 of the 4 citations read need attention: 1 not found in our index, 1 ambiguous, 0 with a case name that did not match."
    )
    assert out["state"] == "complete"  # every citation-like string was read; AIR is a kind the index does not hold


def test_an_air_or_scc_online_citation_is_not_checked_never_not_found() -> None:
    out = run("see AIR 2087 SC 1234 and 2087 SCC OnLine SC 99 here")
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


def test_a_repeated_citation_is_looked_up_once_and_answered_alike() -> None:
    calls: list[str] = []

    def look(t: str) -> tuple[str, bool]:
        calls.append(t)
        return "IN_INDEX", False

    out = run(f"{GOOD}; again {GOOD}; and once more {GOOD}.", look=look)
    assert len(calls) == 1 and out["denominator"]["looked_up"] == 1 and out["denominator"]["found"] == 3


def test_parallel_citations_share_a_group_and_a_quoted_citation_is_flagged() -> None:
    out = run(f'{GOOD} : AIR 2087 SC 1234 : 2087 SCC OnLine SC 99. He said "in (2086) 1 SCC 1 it was held" and stopped.')
    g = [c["group"] for c in out["citations"]]
    assert g[0] == g[1] == g[2] and g[0] is not None and g[3] is None
    assert [c["quoted"] for c in out["citations"]] == [False, False, False, True]
    assert out["denominator"]["quoted"] == 1


def test_a_wrong_year_with_a_real_page_is_not_found_not_fuzzy_matched() -> None:
    out = run("see (2088) 4 SCC 999 here")  # the fake index, like the real one, answers by the whole key
    assert out["citations"][0]["status"] == "not_found"


def test_a_no_index_deployment_says_nothing_was_checked() -> None:
    out = run(f"see {GOOD}", look=None)
    assert out["citations"][0]["status"] == "not_checked" and out["citations"][0]["reason"] == "no_index"
    assert out["state"] == "incomplete" and out["denominator"]["not_checked_no_index"] == 1


def test_a_failing_lookup_is_one_not_checked_and_the_rest_still_run() -> None:
    def look(t: str) -> tuple[str, bool]:
        if "777" in t:
            raise RuntimeError("index busy")
        return lookup(t)

    out = run(f"{GOOD}; (2087) 4 SCC 777; (2087) 4 SCC 999", look=look)
    assert [c["status"] for c in out["citations"]] == ["found", "not_checked", "not_found"]
    assert out["citations"][1]["reason"] == "lookup_failed" and out["state"] == "incomplete"


def test_the_count_cap_reports_checked_n_of_m_and_never_drops_a_citation() -> None:
    cfg = DraftCheckConfig(max_citations=2)
    text = " ".join(f"(2087) 4 SCC {900 + i}" for i in range(6))
    out = run(text, cfg)
    d = out["denominator"]
    assert d["looked_up"] == 2 and d["not_checked_cap"] == 4 and d["read"] == 6
    assert out["checked"] == {"checked": 2, "of": 6} and out["state"] == "incomplete"
    assert [c["reason"] for c in out["citations"][2:]] == ["cap_reached"] * 4


def test_the_time_cap_stops_further_lookups() -> None:
    t = [0.0]

    def clock() -> float:
        t[0] += 10.0
        return t[0]

    cfg = DraftCheckConfig(max_seconds=15)
    out = D.check_draft("(2087) 4 SCC 901 (2087) 4 SCC 902 (2087) 4 SCC 903", cfg, lookup, clock=clock)["draft_check"]
    assert out["denominator"]["not_checked_cap"] >= 1 and out["state"] == "incomplete"


def test_a_big_draft_is_bounded_and_the_counts_stay_exact() -> None:
    text = "(2087) 4 SCC 900 " * 6_000
    out = D.check_draft(text, DraftCheckConfig(max_chars=200_000), lookup)["draft_check"]
    assert out["denominator"]["read"] == 6_000 and out["listing_truncated"] is True and len(out["citations"]) == D.MAX_LISTED
    assert out["denominator"]["looked_up"] == 1


def test_the_report_names_what_it_does_not_assess() -> None:
    out = run(f"see {GOOD}")
    assert "whether a case says what the draft says it says" in out["not_assessed"]
    assert "not that the case says what the draft says" in out["wording"]
    assert "fake" not in str(out).lower() and "hallucinat" not in str(out).lower()


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
    pdf = make_pdf([f"First page cites {GOOD}", "Second page cites (2087) 4 SCC 999"])
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
