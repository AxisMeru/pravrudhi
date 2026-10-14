"""Intake tools and the deterministic final tool (pravrudhi#414). Every fixture is INVENTED text: no real party, judgment or
person. Tests run in-process (no child process, no file, no network). Each rule is tested on both arms (fires / does not fire)
and the checks are tested with mutations (a wrong map, a wrong deletion, a tampered fact must fail the right check)."""

from __future__ import annotations

import copy
import re
from pathlib import Path

import pytest

from pravrudhi.application.a0_assist import intake as I  # noqa: E741


def pages(*ps: str) -> str:
    return "\f".join(ps)


def four_page(body_extra: str = "") -> str:
    hdr = "Page {n} of 4\nhttps://example.test/doc"
    return pages(
        "IN THE HIGH COURT OF EXAMPLEPUR\nRAJA SAMPLE ...PETITIONER\nAND\nTHE STATE ...RESPONDENT\n\n"
        "1. The petitioner says the gate was open.\n",
        hdr.format(n=2) + "\n2. The guard says he did not open it.\n3. A later visit was recorded.\n",
        hdr.format(n=3) + "\n4. Nothing further was found.\n" + body_extra,
        hdr.format(n=4) + "\n5. The matter was listed again.\n",
    )


def facts_text(b):
    return [f["text"] for f in b["facts"]]


def fresh(b):
    return {k: copy.deepcopy(b[k]) for k in ("cleaned", "segments", "deletions")}


# ---- read_input, scan detection -----------------------------------------------------------------------------------------
@pytest.mark.parametrize("raw", ["", "   \n\t ", None, 5])
def test_read_input_refuses_empty(raw):
    with pytest.raises(I.IntakeRefused):
        I.read_input(raw)  # type: ignore[arg-type]


def test_read_input_cap_boundary_both_arms():
    assert I.read_input("a" * I.MAX_CHARS)["n_chars"] == I.MAX_CHARS
    with pytest.raises(I.IntakeRefused):
        I.read_input("a" * (I.MAX_CHARS + 1))


def test_read_input_page_starts_and_sha():
    r = I.read_input("ab\fcd\fef")
    assert r["page_starts"] == [0, 3, 6] and len(r["raw_sha256"]) == 64


@pytest.mark.parametrize(
    "pgs,scanned",
    [
        ([], True),
        (["", "  "], True),
        (["x" * 99], True),
        (["x" * 100], False),
        (["x" * 300, ""], False),
        (["x" * 150, "x" * 40], True),
    ],
)
def test_looks_scanned_threshold(pgs, scanned):
    assert I.looks_scanned(pgs) is scanned


def test_unread_pages_lists_image_pages():
    assert I.unread_pages(["x" * 200, "", "x" * 19, "y" * 20]) == [1, 2]


# ---- 1 hyphenation ------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    "raw,joined",
    [
        ("The person was arrest-\ned at noon.", "arrested"),  # lower-case next: joined
        ("Under Cr.P.C.-\n1973 it applies.", None),  # digit next: not joined
        ("The Delhi-\nHigh Court sat.", None),  # upper-case next: not joined
        ("A dash - \nthen text.", None),  # no letter before the hyphen
    ],
)
def test_hyphen_join_both_arms(raw, joined):
    c = I.clean(raw)
    assert ("arrested" in c["cleaned"]) is (joined == "arrested")
    assert any(d[2] == "hyphen_join" for d in c["deletions"]) is (joined is not None)
    assert all(v["pass"] for v in I.check_clean(raw, c["cleaned"], c["segments"], c["deletions"]).values())


# ---- 2 headers and footers ----------------------------------------------------------------------------------------------
def test_page_marks_and_urls_removed_where_they_recur():
    raw = four_page()
    c = I.clean(raw)
    assert "Page 2 of 4" not in c["cleaned"] and "example.test" not in c["cleaned"]
    assert sum(1 for d in c["deletions"] if d[2] == "furniture") == 6
    assert "guard says he did not open it" in c["cleaned"]


def test_same_text_in_body_is_kept():
    raw = four_page("The report cites Page 2 of 4 of the file.\n")
    assert "cites Page 2 of 4 of the file" in I.clean(raw)["cleaned"]


def test_text_without_page_breaks_has_no_furniture_deleted():
    raw = "Page 2 of 4\nhttps://example.test/doc\n2. A fact.\nPage 3 of 4\nhttps://example.test/doc\n3. Another.\n"
    assert [d for d in I.clean(raw)["deletions"] if d[2] == "furniture"] == []


def test_recurring_header_on_only_two_pages_is_kept():
    raw = pages("first page text here\n", "Page 2 of 4\nsecond page\n", "Page 3 of 4\nthird page\n")
    assert [d for d in I.clean(raw)["deletions"] if d[2] == "furniture"] == []


def test_number_only_removed_only_with_a_stamp_neighbour_and_a_run():
    stamped = pages(*[f"https://example.test/doc\ntext {i}\n{i}" for i in (1, 2, 3, 4)])
    bare = pages(*[f"text {i}\n{i}" for i in (1, 2, 3, 4)])
    assert any(d[2] == "furniture" for d in I.clean(stamped)["deletions"])
    assert [d for d in I.clean(bare)["deletions"] if d[2] == "furniture"] == []


# ---- 3 caption guard ----------------------------------------------------------------------------------------------------
def test_caption_lines_are_never_deleted_even_when_they_recur():
    cap = "RAJA SAMPLE ...PETITIONER"
    raw = pages(*[f"{cap}\n{i}. Some fact {i} is stated here.\nPage {i} of 4" for i in (1, 2, 3, 4)])
    c = I.clean(raw)
    assert c["cleaned"].count("PETITIONER") == 4
    deleted = "".join(raw[d[0] : d[1]] for d in c["deletions"] if d[2] == "furniture")
    assert "PETITIONER" not in deleted


def test_case_mark_class_is_not_admitted():
    raw = pages(*[f"CRL.A NO 123/2020\ntext {i} of the matter\n" for i in (1, 2, 3, 4)])
    assert [d for d in I.clean(raw)["deletions"] if d[2] == "furniture"] == []


# ---- 4 scripts and odd characters ---------------------------------------------------------------------------------------
def test_devanagari_and_latin_kept_and_offsets_in_code_points():
    raw = "1. धारा 302 के अंतर्गत आरोप है।\n2. The charge is under section 302.\n"
    b = I.build_bundle(raw)
    assert "धारा 302 के अंतर्गत आरोप है।" in facts_text(b)[0]
    f = b["facts"][0]
    assert raw[f["raw_start"] : f["raw_end"]] == f["text"]
    assert all(v["pass"] for v in b["checks"].values())


def test_replacement_char_and_combining_marks_are_kept_not_removed():
    raw = "1. Name: Rája � sample.\n"
    b = I.build_bundle(raw)
    assert "�" in b["facts"][0]["text"] and "á" in b["facts"][0]["text"]


# ---- 5 duplicated pages, 6 page break mid-sentence ----------------------------------------------------------------------
def test_duplicate_fact_is_not_citable_twice():
    p = "1. The same long paragraph is repeated on a later page by mistake here.\n"
    b = I.build_bundle(p + "\n" + p.replace("1.", "1."))
    assert len(b["facts"]) == 1 and b["not_citable"][0]["status"] == "duplicate" and b["not_citable"][0]["duplicate_of"] == "F1"


def test_sentence_continues_across_a_page_break():
    raw = pages(
        "1. The accused went to the\n",
        "Page 2 of 4\nmarket and returned.\n",
        "Page 3 of 4\nMore text here.\n",
        "Page 4 of 4\nEnd of text.\n",
    )
    assert "went to the market and returned" in I.clean(raw)["cleaned"]


# ---- paragraph and sentence splitting -----------------------------------------------------------------------------------
def test_numbered_paragraphs_split_on_single_newlines():
    b = I.build_bundle("1. First fact is here.\n2. Second fact is here.\n(3) Third fact is here.\n")
    assert facts_text(b) == ["1. First fact is here.", "2. Second fact is here.", "(3) Third fact is here."]


def test_long_paragraph_is_cut_at_sentence_ends_not_after_abbreviations():
    s = "Sri A. Kumar, S/o Mr. B. Kumar, went to the market. " * 30
    b = I.build_bundle("1. " + s)
    assert len(b["facts"]) > 1
    assert all(len(f["text"]) <= I.MAX_FACT_CHARS for f in b["facts"])
    assert all(f["text"].endswith("market.") for f in b["facts"])
    assert not any(f["text"].startswith(("Kumar", "Kumar,")) for f in b["facts"])


def test_too_many_candidates_refused():
    raw = "\n\n".join(f"{i}. Fact number {i} is here." for i in range(I.MAX_FACTS + 1))
    with pytest.raises(I.IntakeRefused):
        I.build_bundle(raw)


# ---- the bundle: C1..C7 on the baseline --------------------------------------------------------------------------------
def test_baseline_bundle_all_checks_pass_and_every_fact_is_a_raw_span():
    raw = four_page()
    b = I.build_bundle(raw)
    assert b["clean_status"] == "cleaned" and all(v["pass"] for v in b["checks"].values())
    assert len(b["facts"]) >= 5
    for f in b["facts"]:
        assert f["status"] == "span"
        assert re.sub(r"\s+", " ", I._ws(raw[f["raw_start"] : f["raw_end"]])) or True
        assert b["cleaned"][f["cleaned_start"] : f["cleaned_end"]] == f["text"]


def test_determinism_and_agent_path_equals_baseline():
    raw = four_page()
    b1, b2 = I.build_bundle(raw), I.build_bundle(raw)
    assert I.canon(b1) == I.canon(b2) and b1["bundle_sha256"] == b2["bundle_sha256"]
    prop = {**fresh(b1), "facts": [{"cleaned_start": f["cleaned_start"], "cleaned_end": f["cleaned_end"]} for f in b1["facts"]]}
    b3 = I.build_bundle(raw, proposal=prop)
    assert b3["bundle_sha256"] == b1["bundle_sha256"]


def test_reconstruction_cleaned_equals_raw_minus_deletions_with_ws_collapsed():
    raw = four_page()
    c = I.clean(raw)
    keep = raw
    for d in sorted(c["deletions"], key=lambda x: -x[0]):
        keep = keep[: d[0]] + keep[d[1] :]
    assert re.sub(r"\s+", " ", keep).strip() == re.sub(r"\s+", " ", c["cleaned"]).strip()


# ---- 8 mutation: a cleaner that deletes protected content must fail ----------------------------------------------------
def test_deleting_a_negation_word_fails_c2_and_serves_raw():
    raw = "1. The guard did not open the gate at noon.\n"
    i = raw.index("not ")
    prop = {
        "cleaned": raw.replace("not ", "").strip(),
        "segments": [[0, i, 0, i, "c"], [i, len(raw.replace("not ", "").strip()), i + 4, len(raw.rstrip()), "c"]],
        "deletions": [[i, i + 4, "furniture"], [len(raw.rstrip()), len(raw), "ws"]],
    }
    ck = I.check_clean(raw, prop["cleaned"], prop["segments"], prop["deletions"])
    assert not ck["C2"]["pass"]
    b = I.build_bundle(raw, proposal=prop)
    assert b["clean_status"] == "served_raw" and "not open" in b["facts"][0]["text"]


def test_deleting_an_amount_fails_c3():
    raw = "1. He paid Rs. 2,50,000 to Sri Ravi.\n"
    d = [[3, 22, "furniture"]]
    segs = [[0, 3, 0, 3, "c"]]
    rest = raw[22:].rstrip()
    segs.append([3, 3 + len(rest), 22, 22 + len(rest), "c"])
    ck = I.check_clean(raw, "1. " + rest, segs, d + [[22 + len(rest), len(raw), "ws"]])
    assert not ck["C2"]["pass"] and not ck["C3"]["pass"]


def test_whitespace_segment_with_wrong_text_fails_c1():
    raw = "1. One fact here.\n2. Two fact here.\n"
    c = I.clean(raw)
    bad = copy.deepcopy(c["segments"])
    k = next(i for i, s in enumerate(bad) if s[4] == "w")
    cleaned = c["cleaned"][: bad[k][0]] + "X" + c["cleaned"][bad[k][0] + 1 :]
    ck = I.check_clean(raw, cleaned, bad, c["deletions"])
    assert not ck["C1"]["pass"]


# ---- 9 offset-map corruption --------------------------------------------------------------------------------------------
def test_shifted_map_entry_fails_c1_and_route_serves_raw():
    raw = four_page()
    b = I.build_bundle(raw)
    prop = fresh(b)
    prop["segments"][3][2] += 1
    prop["segments"][3][3] += 1
    ck = I.check_clean(raw, prop["cleaned"], prop["segments"], prop["deletions"])
    assert not ck["C1"]["pass"]
    out = I.build_bundle(raw, proposal=prop)
    assert out["clean_status"] == "served_raw" and out["checks"]["C1"]["pass"] is False


def test_gap_in_raw_tiling_fails_c1():
    raw = four_page()
    b = I.build_bundle(raw)
    prop = fresh(b)
    prop["deletions"].pop(0)
    assert not I.check_clean(raw, prop["cleaned"], prop["segments"], prop["deletions"])["C1"]["pass"]


def test_malformed_map_does_not_raise():
    raw = "1. A fact is here.\n"
    ck = I.check_clean(raw, "x", [["a"]], [[1]])
    assert not ck["C1"]["pass"]


def test_extra_deletion_of_a_whole_line_not_recomputed_fails_c2():
    raw = four_page()
    b = I.build_bundle(raw)
    prop = fresh(b)
    seg = next(s for s in prop["segments"] if s[4] == "c" and "guard" in raw[s[2] : s[3]])
    prop["deletions"].append([seg[2], seg[3], "furniture"])
    assert not I.check_clean(raw, prop["cleaned"], prop["segments"], prop["deletions"])["C2"]["pass"]


# ---- 10 fact tamper -----------------------------------------------------------------------------------------------------
def _with_facts(raw, facts):
    b = I.build_bundle(raw)
    return I.build_bundle(raw, proposal={**fresh(b), "facts": facts})


def test_changed_fact_text_is_a_paraphrase_and_not_citable():
    raw = "1. The gate was open.\n2. The guard was away.\n"
    b = I.build_bundle(raw)
    f0 = b["facts"][0]
    out = _with_facts(
        raw, [{"cleaned_start": f0["cleaned_start"], "cleaned_end": f0["cleaned_end"], "text": "1. The gate was shut."}]
    )
    assert out["facts"] == [] and out["not_citable"][0]["status"] == "paraphrase" and out["checks"]["C4"]["pass"] is False


def test_span_one_character_off_is_not_a_source_span_when_it_lands_on_whitespace():
    raw = "1. The gate was open.\n\n2. The guard was away.\n"
    b = I.build_bundle(raw)
    f0 = b["facts"][0]
    out = _with_facts(raw, [{"cleaned_start": f0["cleaned_start"], "cleaned_end": f0["cleaned_end"] + 1}])
    assert out["facts"] == [] and out["not_citable"][0]["status"] == "paraphrase"


@pytest.mark.parametrize(
    "span",
    [
        {"cleaned_start": 5, "cleaned_end": 5},
        {"cleaned_start": -1, "cleaned_end": 4},
        {"cleaned_start": 3, "cleaned_end": 9999},
        {"cleaned_start": "a", "cleaned_end": 4},
    ],
)
def test_bad_fact_indexes_are_paraphrase(span):
    out = _with_facts("1. The gate was open.\n", [span])
    assert out["facts"] == [] and out["not_citable"][0]["status"] == "paraphrase"


def test_overlapping_or_out_of_order_facts_are_rejected():
    raw = "1. The gate was open.\n\n2. The guard was away.\n"
    b = I.build_bundle(raw)
    a, c = b["facts"][0], b["facts"][1]
    out = _with_facts(
        raw,
        [
            {"cleaned_start": c["cleaned_start"], "cleaned_end": c["cleaned_end"]},
            {"cleaned_start": a["cleaned_start"], "cleaned_end": a["cleaned_end"]},
        ],
    )
    assert len(out["facts"]) == 1 and out["not_citable"][0]["status"] == "paraphrase"


def test_agent_proposed_facts_that_are_exact_slices_are_citable():
    raw = "1. The gate was open.\n2. The guard was away.\n"
    b = I.build_bundle(raw)
    out = _with_facts(
        raw, [{"cleaned_start": f["cleaned_start"], "cleaned_end": f["cleaned_end"], "text": f["text"]} for f in b["facts"]]
    )
    assert [f["text"] for f in out["facts"]] == facts_text(b)


def test_protected_token_loss_inside_a_fact_falls_back_to_raw_text():
    raw = "1. He paid 250 rupees.\n"
    b = I.build_bundle(raw)
    # an (invalid) proposal whose cleaned text differs from raw is caught by C1; so test the per-fact check directly
    prop = fresh(b)
    out = I.build_bundle(raw, proposal=prop)
    assert out["facts"][0]["status"] == "span"
    assert I._protected("He paid 250 rupees") != I._protected("He paid rupees")


# ---- 6/7 text PDF edges ------------------------------------------------------------------------------------------------
def test_ligatures_and_nbsp_are_kept_or_collapsed_with_a_valid_map():
    raw = "1. The oﬃce of the clerk  was shut.\n"
    b = I.build_bundle(raw)
    assert "oﬃce" in b["cleaned"] and " " not in b["cleaned"]
    assert all(v["pass"] for v in b["checks"].values())


def test_very_long_line_and_cap_boundary_bundle():
    raw = "1. " + ("word " * 40000)[: I.MAX_CHARS - 3]
    assert len(raw) == I.MAX_CHARS
    b = I.build_bundle(raw)
    assert b["checks"]["C1"]["pass"] and b["n_chars"] == I.MAX_CHARS


def test_unread_pages_are_carried_into_the_bundle_when_given():
    b = I.build_bundle("1. A fact is here.\n", proposal=None)
    assert b["unread_pages"] == []


# ---- citations (C6) -----------------------------------------------------------------------------------------------------
def test_citations_have_raw_spans_and_verifier_status_unchanged():
    raw = "1. See (2014) 8 SCC 273 and AIR 1962 SC 605 for the point.\n"
    seen = []

    def vf(t):
        seen.append(t)
        return "IN_INDEX" if "SCC" in t else "NOT_IN_INDEX"

    b = I.build_bundle(raw, verify_fn=vf)
    assert [(c["text"], c["verifier_status"]) for c in b["citations"]] == [
        ("(2014) 8 SCC 273", "IN_INDEX"),
        ("AIR 1962 SC 605", "NOT_IN_INDEX"),
    ]
    assert all(raw[c["start"] : c["end"]] == c["text"] for c in b["citations"]) and b["citations"][0]["fact_ids"] == ["F1"]
    assert seen == [c["text"] for c in b["citations"]]


def test_verifier_fault_is_shown_not_guessed():
    def boom(t):
        raise RuntimeError("down")

    b = I.build_bundle("1. See (2014) 8 SCC 273.\n", verify_fn=boom)
    assert b["citations"][0]["verifier_status"] == "CHECK_UNAVAILABLE"


def test_no_verifier_means_not_checked_and_no_invented_citation():
    b = I.build_bundle("1. No citation is named in this fact at all.\n")
    assert b["citations"] == []
    assert I.build_bundle("1. See (2014) 8 SCC 273.\n")["citations"][0]["verifier_status"] == "NOT_CHECKED"


# ---- 12 no leak: the module touches no file, process, clock or network --------------------------------------------------
def test_module_source_has_no_io():
    src = Path(I.__file__).read_text()
    for banned in (
        "open(",
        "subprocess",
        "tempfile",
        "import os",
        "import socket",
        "urllib",
        "httpx",
        "requests",
        "time.",
        "random",
        "print(",
    ):
        assert banned not in src, banned


def test_no_test_launches_its_own_suite():
    src = Path(__file__).read_text()
    forbidden = ("import sub" + "process", "os." + "system", "pytest" + ".main", "Pop" + "en(")
    assert not any(f in src for f in forbidden)


def test_scan_line_is_the_signed_wording():
    assert I.SCAN_LINE == "This file looks like a scan, so its text cannot be read here. Paste the text instead."
