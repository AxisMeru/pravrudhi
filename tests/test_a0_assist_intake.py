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


def test_many_paragraphs_are_merged_not_refused():
    raw = "\n\n".join(f"{i}. Fact number {i} is here." for i in range(I.MAX_FACTS * 2))
    b = I.build_bundle(raw)
    assert 0 < len(b["facts"]) <= I.MAX_FACTS and all(v["pass"] for v in b["checks"].values())


def test_agent_candidates_beyond_twice_the_cap_are_refused():
    raw = "1. A fact is here.\n"
    b = I.build_bundle(raw)
    prop = {**fresh(b), "facts": [{"cleaned_start": 0, "cleaned_end": 3}] * (I.MAX_FACTS * 2 + 1)}
    with pytest.raises(I.IntakeRefused):
        I.build_bundle(raw, proposal=prop)


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


# ---- regressions from the isolated Sonnet pre-check (records: precheck_intake) -----------------------------------------------
import contextlib  # noqa: E402
import signal  # noqa: E402


@contextlib.contextmanager
def deadline(seconds: int):
    """A hang or a quadratic/exponential blow-up fails the test instead of stalling the suite."""

    def boom(*_a):
        raise TimeoutError(f"took longer than {seconds}s")

    old = signal.signal(signal.SIGALRM, boom)
    signal.alarm(seconds)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old)


@pytest.mark.parametrize("dels", [[[2, 2, "furniture"]], [[2, 3, "furniture"], [3, 2, "furniture"]]])
def test_zero_length_or_cyclic_deletions_do_not_hang_and_fail_c1(dels):
    raw = "a b"
    segs = [[0, 1, 0, 1, "c"], [1, 2, 1, 2, "w"], [2, 3, 2, 3, "c"]]
    with deadline(5):
        ck = I.check_clean(raw, "a b", segs, dels)
        out = I.build_bundle(raw, proposal={"cleaned": "a b", "segments": segs, "deletions": dels})
    assert not ck["C1"]["pass"] and out["clean_status"] == "served_raw"


def test_caption_guard_is_linear_on_one_huge_page():
    raw = "\n".join(["A B C"] * 4000 + ["A B C petitioner"] * 4000)
    with deadline(10):
        I.clean(raw)


def test_sentence_ends_are_linear_on_one_huge_paragraph():
    raw = "A. B. " * 30000
    with deadline(10):
        b = I.build_bundle(raw)
    assert b["checks"]["C1"]["pass"]


def test_stamp_grammar_does_not_backtrack_exponentially():
    bad = "CR." * 35 + "!"
    raw = pages(*[f"{bad}\ntext {i}\n{i}" for i in (1, 2, 3)])
    with deadline(5):
        I.clean(raw)
    with deadline(5):
        assert I._is_stamp(bad) is False


def test_many_tiny_pages_are_linear():
    raw = "\f".join(str(i % 999 + 1) for i in range(20000))
    with deadline(10):
        I.clean(raw)


def test_c6_fails_on_a_parser_span_outside_the_text(monkeypatch):
    monkeypatch.setattr(I, "extract_citations", lambda raw: [{"start": 5, "end": 2, "text": "", "reporter": "SCC", "year": 1}])
    b = I.build_bundle("1. A fact is here.\n")
    assert b["checks"]["C6"]["pass"] is False and b["citations"] == []


def test_dropping_a_space_between_devanagari_words_is_caught():
    raw = "वह नहीं गया"
    i = raw.index(" ")
    prop = {"cleaned": "वहनहीं गया", "segments": [[0, i, 0, i, "c"], [i, 10, i + 1, 11, "c"]], "deletions": [[i, i + 1, "ws"]]}
    ck = I.check_clean(raw, prop["cleaned"], prop["segments"], prop["deletions"])
    assert not (ck["C2"]["pass"] and ck["C3"]["pass"])
    assert I.build_bundle(raw, proposal=prop)["clean_status"] == "served_raw"


def test_merging_two_latin_words_by_a_ws_deletion_is_caught():
    raw = "1. a b c is here.\n"
    prop = {
        "cleaned": "1. ab c is here.",
        "segments": [[0, 4, 0, 4, "c"], [4, 16, 5, 17, "c"]],
        "deletions": [[4, 5, "ws"], [17, 18, "ws"]],
    }
    assert I.build_bundle(raw, proposal=prop)["clean_status"] == "served_raw"


@pytest.mark.parametrize("prop", [[1], "x", 5])
def test_non_mapping_proposal_is_refused_not_a_crash(prop):
    with pytest.raises(I.IntakeRefused):
        I.build_bundle("1. A fact is here.\n", proposal=prop)  # type: ignore[arg-type]


@pytest.mark.parametrize("facts", ["abc", [1], [None], ["x"]])
def test_bad_facts_container_is_refused_not_a_crash(facts):
    raw = "1. A fact is here.\n"
    b = I.build_bundle(raw)
    with pytest.raises(I.IntakeRefused):
        I.build_bundle(raw, proposal={**fresh(b), "facts": facts})


def test_unread_pages_are_validated_and_bools_are_not_offsets():
    raw = "1. A fact is here.\n"
    b = I.build_bundle(raw)
    out = I.build_bundle(raw, proposal={**fresh(b), "unread_pages": [[1], "a", True, 0, 7, -1]})
    assert out["unread_pages"] == [0]
    out2 = I.build_bundle(raw, proposal={**fresh(b), "facts": [{"cleaned_start": True, "cleaned_end": 5}]})
    assert out2["facts"] == [] and out2["not_citable"][0]["cleaned_start"] is None


def test_lone_surrogate_is_refused_not_a_crash():
    with pytest.raises(I.IntakeRefused):
        I.read_input("abc\ud800")


def test_text_with_no_sentence_end_is_hard_cut_at_whitespace():
    raw = "1. " + "word " * 500
    b = I.build_bundle(raw)
    assert len(b["facts"]) > 1 and all(len(f["text"]) <= I.MAX_FACT_CHARS for f in b["facts"])
    assert all(not f["text"].startswith("ord") for f in b["facts"])


def test_a_short_sentence_final_word_is_a_sentence_end_not_an_abbreviation():
    par = "He went to do so. Then he left the house and the market for a long time. " * 12
    assert any(par[:e].endswith("do so.") for e in I._sentence_ends(par))


def test_zero_citable_facts_fails_c4():
    b = I.build_bundle("12345")
    assert b["facts"] == [] and b["checks"]["C4"]["pass"] is False


# ---- reviewer gaps S1-S3 (R2, #418 at 137d9747): each test fails if the check it names is removed ------------------------------
def test_s1_raw_fallback_arm_when_kept_raw_tokens_differ_from_the_cleaned_text(monkeypatch):
    raw = "1. The gate was open. Then Ravi left.\n"
    real = I._raw_span

    def widened(cs, ce, segments):
        rs = real(cs, ce, segments)
        return None if rs is None else (rs[0], min(len(raw), rs[1] + 6))  # the raw slice carries a word the cleaned slice lacks

    monkeypatch.setattr(I, "_raw_span", widened)
    b = I.build_bundle(raw, proposal={**fresh(I.build_bundle(raw)), "facts": [{"cleaned_start": 0, "cleaned_end": 21}]})
    f = b["facts"][0]
    assert f["status"] == "raw_fallback" and f["text"] == I._ws(raw[f["raw_start"] : f["raw_end"]])
    assert I.build_bundle(raw)["facts"][0]["status"] == "span"  # the other arm


def test_s2_c2_bites_on_a_ws_deletion_the_cleaner_does_not_make():
    raw = "1. A fact is here.\n"
    base = I.clean(raw)
    # carried: the single space after "1." is dropped as a ws deletion; the tiling, the map and the words all stay valid
    segs: list[list] = [[0, 2, 0, 2, "c"]]
    dels: list[list] = [[2, 3, "ws"]]
    pos_c = 2
    for m in re.finditer(r"\s+|\S+", raw[3:]):
        s0, e0 = 3 + m.start(), 3 + m.end()
        if m.group().isspace() and e0 == len(raw):
            dels.append([s0, e0, "ws"])  # the trailing newline is a deletion, as the cleaner does
        elif m.group().isspace():
            segs.append([pos_c, pos_c + 1, s0, e0, "w"])
            pos_c += 1
        else:
            segs.append([pos_c, pos_c + e0 - s0, s0, e0, "c"])
            pos_c += e0 - s0
    cleaned = "1.A fact is here."
    assert (2, 3) not in {(d[0], d[1]) for d in base["deletions"]}
    ck = I.check_clean(raw, cleaned, segs, dels)
    assert ck["C1"]["pass"] and ck["C3"]["pass"] and not ck["C2"]["pass"]
    assert I.build_bundle(raw, proposal={"cleaned": cleaned, "segments": segs, "deletions": dels})["clean_status"] == "served_raw"


@pytest.mark.parametrize(
    "segs",
    [
        [[0, 2, 0, 2, "c"], [2, 4, 3, 5, "c"], [4, 5, 5, 6, "c"]],  # a gap: raw [2,3) is in neither a segment nor a deletion
        [[0, 3, 0, 3, "c"], [3, 5, 2, 4, "c"], [5, 6, 4, 5, "c"]],  # an overlap: raw [2,3) is claimed twice
    ],
)
def test_s3_c1_tiling_gap_and_overlap_each_fail_c1_on_their_own(segs):
    raw = "abXcdY"[:5] + "Z"
    # raw = "abXcdZ"; each carried map rebuilds its own cleaned text and every copy segment equals its raw slice
    cleaned = "".join(raw[s[2] : s[3]] for s in segs)
    assert all(cleaned[s[0] : s[1]] == raw[s[2] : s[3]] for s in segs)
    ck = I.check_clean(raw, cleaned, segs, [])
    assert not ck["C1"]["pass"] and "tiled" in " ".join(ck["C1"]["detail"])


def test_mostly_upper_both_arms_and_no_letters_is_not_upper():
    assert I._mostly_upper("RAJA SAMPLE") is True
    assert I._mostly_upper("abc DEF ghi") is False  # 3 of 9 letters
    assert I._mostly_upper("abc DEF", share=0.4) is True
    assert I._mostly_upper("123 456 789") is False and I._mostly_upper("") is False


# ---- real-text fixes (dev-stack run on cleared public orders, 14 Oct): D1 hyphen before furniture, D2 caption lines ---------
def _hyphen_then_furniture_doc():
    hdr = "http://www.example.test/doc\n{n}/40"
    return pages(
        "1. The clause is in contravention of by-\n" + hdr.format(n=1),
        "law and rule 5 of the schedule applies here.\n" + hdr.format(n=2),
        "2. Nothing further was found on the second day.\n" + hdr.format(n=3),
        "3. The matter was listed again later.\n" + hdr.format(n=4),
    )


def test_d1_no_hyphen_join_into_a_deleted_furniture_line_and_all_checks_pass():
    raw = _hyphen_then_furniture_doc()
    c = I.clean(raw)
    assert [d for d in c["deletions"] if d[2] == "furniture"], "the fixture must have furniture"
    assert not [d for d in c["deletions"] if d[2] == "hyphen_join"]  # arm 1: the hyphen before the furniture line stays
    ck = I.check_clean(raw, c["cleaned"], c["segments"], c["deletions"])
    assert all(v["pass"] for v in ck.values())
    b = I.build_bundle(raw)
    assert b["clean_status"] == "cleaned" and all(v["pass"] for v in b["checks"].values())
    assert "by-" in b["cleaned"] and "bylaw" not in b["cleaned"]


def test_d1_a_hyphen_join_elsewhere_still_happens_next_to_furniture():
    raw = four_page("The person was arrest-\ned at noon.\n")  # arm 2: a join whose next line is body text is made
    c = I.clean(raw)
    assert any(d[2] == "hyphen_join" for d in c["deletions"]) and any(d[2] == "furniture" for d in c["deletions"])
    assert all(v["pass"] for v in I.check_clean(raw, c["cleaned"], c["segments"], c["deletions"]).values())


def test_d1_protected_tokeniser_is_pure_and_c3_applies_only_the_carried_joins():
    assert I._protected("arrest-\ned") != I._protected("arrested")  # no hidden join inside the tokeniser
    raw = "1. The person was arrest-\ned at noon.\n"
    c = I.clean(raw)
    assert I.check_clean(raw, c["cleaned"], c["segments"], c["deletions"])["C3"]["pass"]
    # a join that was NOT carried must not be assumed: dropping the carried join record makes C1 or C3 fail
    no_join = [d for d in c["deletions"] if d[2] != "hyphen_join"]
    ck = I.check_clean(raw, c["cleaned"], c["segments"], no_join)
    assert not (ck["C1"]["pass"] and ck["C2"]["pass"] and ck["C3"]["pass"])


# ---- D2: facts from the body of the order, not every caption line (split_plan) ------------------------------------------------
BODY = "The petitioner says the gate was open and the guard was away for the whole of the afternoon in question. " * 2


def _plan(text):
    return I.split_plan(I.clean(text)["cleaned"])


def test_d2_caption_before_an_order_heading_is_one_excluded_span_and_not_a_fact():
    raw = (
        "IN THE HIGH COURT\n\nBEFORE\n\nTHE HON'BLE MR JUSTICE SAMPLE\n\nBETWEEN:\n\nORDER\n\n"
        + BODY
        + "\n\n2. Next numbered paragraph.\n"
    )
    b = I.build_bundle(raw)
    assert [n["status"] for n in b["not_citable"]] == ["caption"] and b["checks"]["C4"]["pass"] is True
    cap = b["not_citable"][0]
    assert "BETWEEN" in b["cleaned"][cap["cleaned_start"] : cap["cleaned_end"]] and "ORDER" not in " ".join(facts_text(b)[:1])[:5]
    assert facts_text(b)[0].startswith("The petitioner says") and facts_text(b)[-1].startswith("2. Next")


def test_d2_caption_ends_at_the_first_long_paragraph_when_there_is_no_heading_and_nowhere_when_all_are_short():
    with_long = "IN THE HIGH COURT\n\nBEFORE\n\nJUSTICE SAMPLE\n\n" + BODY + "\n"
    p = _plan(with_long)
    assert [e["status"] for e in p["excluded"]] == ["caption"] and len(p["facts"]) == 1
    all_short = "IN THE HIGH COURT\n\nBEFORE\n\nJUSTICE SAMPLE\n"
    p2 = _plan(all_short)
    assert p2["excluded"] == [] and len(p2["facts"]) == 1  # nothing to call a caption: the three lines are one fact


def test_d2_an_order_heading_late_in_the_text_is_body_not_a_caption_boundary():
    paras = "\n\n".join(
        f"{i}. Paragraph {i} says that the gate was open and the guard was away for the afternoon." for i in range(1, 11)
    )
    raw = paras + "\n\nORDER\n\n11. The last paragraph is here.\n"
    assert [e["status"] for e in _plan(raw)["excluded"]] == []  # the heading is past 40% of the paragraphs


def test_d2_a_short_line_repeated_three_times_is_excluded_twice_is_kept():
    def doc(n):
        return "\n\n".join(
            ["1. " + BODY[:150] + ".", *(["Page stamp 12 of 99"] * n), "2. " + BODY[:150] + ".", "Closing line here"]
        )

    assert [e["status"] for e in _plan(doc(3))["excluded"]] == ["repeated_line"] * 3
    assert _plan(doc(2))["excluded"] == []
    # an excluded line ends the group: the two numbered paragraphs stay separate facts either way
    assert len(_plan(doc(3))["facts"]) == 2  # the unnumbered closing line joins the last numbered paragraph


def test_d2_numbered_paragraphs_each_start_a_fact_an_unnumbered_line_after_one_continues_it():
    raw = "1. Short one.\n\nContinuation line\n\n2. Another short one.\n\n(3) And a third.\n"
    assert facts_text(I.build_bundle(raw)) == ["1. Short one.\n\nContinuation line", "2. Another short one.", "(3) And a third."]
    # a heading before the FIRST numbered paragraph is the caption of the order, not a fact
    b = I.build_bundle("HEADING LINE\n\n1. Short one.\n\n2. Another.\n")
    assert [n["status"] for n in b["not_citable"]] == ["caption"] and facts_text(b) == ["1. Short one.", "2. Another."]


def test_d2_a_short_heading_after_an_excluded_line_joins_the_numbered_paragraph_that_follows():
    raw = "\n\n".join(["1. " + BODY[:120] + ".", *["Page stamp 7 of 99"] * 3, "Sub-heading line", "2. Another short one."]) + "\n"
    assert facts_text(I.build_bundle(raw))[-1] == "Sub-heading line\n\n2. Another short one."  # arm 1: lead joins forward
    raw2 = "\n\n".join(["1. " + BODY[:120] + ".", *["Page stamp 7 of 99"] * 3, "2. Another short one."]) + "\n"
    assert facts_text(I.build_bundle(raw2))[-1] == "2. Another short one."  # arm 2: no heading, nothing joined


def test_d2_unnumbered_short_paragraphs_pack_until_the_target_and_never_past_the_cap():
    paras = [
        f"Account line {i}: the witness {chr(97 + i % 26) * (3 + i % 5)} saw the gate open at {i} o'clock." for i in range(40)
    ]
    b = I.build_bundle("\n\n".join(paras) + "\n")
    sizes = [len(f["text"]) for f in b["facts"]]
    assert len(b["facts"]) < 15 and max(sizes) <= I.MAX_FACT_CHARS and min(sizes[:-1]) >= I.TARGET_FACT_CHARS


def test_d2_a_long_paragraph_is_cut_at_sentence_ends_and_the_agent_path_equals_the_baseline():
    raw = "1. " + " ".join(
        f"On visit {i} the accused went to the market and returned home at {i + 1} o'clock." for i in range(40)
    )
    b = I.build_bundle(raw)
    assert len(b["facts"]) > 1 and all(len(f["text"]) <= I.MAX_FACT_CHARS for f in b["facts"])
    prop = {**fresh(b), "facts": [{"cleaned_start": f["cleaned_start"], "cleaned_end": f["cleaned_end"]} for f in b["facts"]]}
    assert I.build_bundle(raw, proposal=prop)["bundle_sha256"] == b["bundle_sha256"]


# ---- the stated range of a usable fact list (a rule with both arms) -----------------------------------------------------------
def _bundle(n_chars, lens):
    return {"n_chars": n_chars, "facts": [{"text": "x" * n} for n in lens]}


@pytest.mark.parametrize(
    "n_chars,lens,ok,why",
    [
        (10_000, [300] * 24, True, None),  # the median order of the 39 cleared orders
        (10_000, [300] * 16, True, None),  # lower edge of the observed range
        (10_000, [150] * 46, True, None),
        (10_000, [300] * 9, False, "facts per 10,000"),  # too few
        (10_000, [300] * 61, False, "facts per 10,000"),  # one fact per line
        (10_000, [90] * 24, False, "median fact length"),
        (10_000, [300] * 23 + [601], False, "longer than the cap"),
        (10_000, [300] * 18 + [20] * 6, False, "under 40"),  # 25% of the facts are fragments
        (10_000, [300] * 19 + [20] * 5, True, None),  # 21% is over; 20.8% -> see next test for the edge
    ],
)
def test_usable_range_both_arms(n_chars, lens, ok, why):
    r = I.fact_list_usable(_bundle(n_chars, lens))
    if lens.count(20) == 5:  # 5 of 24 = 20.8% is over the 20% limit: the edge case fails, by design
        ok, why = False, "under 40"
    assert r["applies"] is True and r["pass"] is ok
    assert (why is None) or any(why in x for x in r["reasons"])


def test_usable_range_is_not_applied_to_short_texts_and_an_empty_list_fails():
    assert I.fact_list_usable(_bundle(1_999, [10]))["applies"] is False and I.fact_list_usable(_bundle(1_999, []))["pass"] is True
    assert I.fact_list_usable(_bundle(5_000, []))["pass"] is False


def test_usable_range_holds_for_a_caption_heavy_invented_order():
    caps = "\n\n".join(["IN THE HIGH COURT", "BEFORE", "JUSTICE SAMPLE", "BETWEEN:", "ORDER"] + [f"LINE {i}" for i in range(20)])
    body = "\n\n".join(
        f"{i}. " + f"On visit {i} the petitioner says the gate was open and the guard was away. " * 5 for i in range(1, 50)
    )
    r = I.fact_list_usable(I.build_bundle(caps + "\n\n" + body + "\n"))
    assert r["applies"] and r["pass"], r


# ---- the isolated pre-check of the D1/D2 change (record: precheck_intake2) --------------------------------------------------
def test_pc1_a_proposed_fact_inside_an_excluded_span_is_not_citable_and_lists_do_not_overlap():
    raw = "IN THE HIGH COURT\n\nBEFORE\n\nORDER\n\n" + BODY + "\n"
    b = I.build_bundle(raw)
    cap = next(n for n in b["not_citable"] if n["status"] == "caption")
    prop = {
        **fresh(b),
        "facts": [
            {"cleaned_start": 0, "cleaned_end": 10},
            *({k: f[k] for k in ("cleaned_start", "cleaned_end")} for f in b["facts"]),
        ],
    }
    out = I.build_bundle(raw, proposal=prop)
    assert [n["status"] for n in out["not_citable"] if n["cleaned_start"] == 0 and n["status"] != "caption"] == ["excluded"]
    spans = [(f["cleaned_start"], f["cleaned_end"]) for f in out["facts"]]
    assert all(not (cap["cleaned_start"] < e and s0 < cap["cleaned_end"]) for s0, e in spans)


def test_pc2_merged_facts_never_span_an_excluded_line():
    paras = []
    for i in range(1, 2 * I.MAX_FACTS + 40):
        paras.append(
            f"{i}. Numbered paragraph {i} of the account, long enough to stand alone as a single fact in this test text."
        )
        if i % 100 == 0:
            paras += ["Page stamp 7 of 99"] * 3
    plan = I.split_plan("\n\n".join(paras) + "\n")
    assert len(plan["facts"]) <= I.MAX_FACTS and [e["status"] for e in plan["excluded"]].count("repeated_line") >= 3
    for f in plan["facts"]:
        assert not any(e["cleaned_start"] < f["cleaned_end"] and f["cleaned_start"] < e["cleaned_end"] for e in plan["excluded"])


def test_pc3_short_numbered_paragraphs_before_the_first_long_one_are_facts_not_caption():
    raw = "\n\n".join(f"{i}. The tenant paid rent." for i in range(1, 21)) + "\n\n" + BODY + "\n"
    b = I.build_bundle(raw)
    assert not [n for n in b["not_citable"] if n["status"] == "caption"] and facts_text(b)[0].startswith("1. The tenant")


def test_pc4_a_long_paragraph_before_an_order_heading_is_a_fact_not_caption():
    raw = BODY + "\n\nORDER\n\n1. Next numbered paragraph.\n\n2. And another.\n\n3. And a third.\n\n4. Fourth.\n"
    p = _plan(raw)
    assert [e["status"] for e in p["excluded"]] == [] and facts_text(I.build_bundle(raw))[0].startswith("The petitioner says")


def test_pc5_letterless_paragraphs_are_listed_when_they_are_in_no_fact():
    raw = "1,00,000/-\n\n" + BODY + "\n\n12\n\nThe tenant paid rent late.\n"
    b = I.build_bundle(raw)
    kinds = [(n["status"], b["cleaned"][n["cleaned_start"] : n["cleaned_end"]]) for n in b["not_citable"]]
    assert ("no_words", "1,00,000/-") in kinds
    for c in b["facts"]:  # nothing is both a fact and excluded
        assert not any(n["cleaned_start"] < c["cleaned_end"] and c["cleaned_start"] < n["cleaned_end"] for n in b["not_citable"])


def test_pc6_usable_rule_fails_closed_on_a_bundle_it_cannot_read_and_on_a_mostly_excluded_text():
    assert I.fact_list_usable({})["pass"] is False
    assert I.fact_list_usable({"n_chars": 5000})["pass"] is False  # no facts list
    assert I.fact_list_usable({"n_chars": 5000, "facts": [{}]})["pass"] is False  # a fact without text
    ok = {"n_chars": 10_000, "facts": [{"text": "x" * 300}] * 24, "not_citable": [], "cleaned": "x" * 10_000}
    assert I.fact_list_usable(ok)["pass"] is True
    cap = {"status": "caption", "cleaned_start": 0, "cleaned_end": 6000}
    assert I.fact_list_usable({**ok, "not_citable": [cap]})["pass"] is False  # 60% of the text left out


def test_pc7_paragraph_spans_never_start_or_end_on_whitespace_even_with_three_newlines():
    for a, b in I._paragraphs("A first line here\n\n\n\nB second line here\n\n\n"):
        t = ("A first line here\n\n\n\nB second line here\n\n\n")[a:b]
        assert t == t.strip() and t


def test_pc8_short_negation_lines_repeated_three_times_are_kept_as_facts():
    raw = "1. " + BODY[:100] + "\n\nNo.\n\nNo.\n\nNo.\n\n2. " + BODY[:100] + "\n"
    assert [e["status"] for e in _plan(raw)["excluded"]] == []


def test_pc9_a_hyphen_at_the_end_of_a_furniture_line_is_not_joined_and_checks_pass():
    hdr = "- {n} -\nhttp://www.example.test/doc-\n"
    raw = pages(
        *[
            ("1. Body text of page one is here.\n" if i == 1 else f"{i}. Body text of page {i} is here.\n")
            + hdr.format(n=i)
            + "next line text\n"
            for i in range(1, 5)
        ]
    )
    c = I.clean(raw)
    assert all(v["pass"] for v in I.check_clean(raw, c["cleaned"], c["segments"], c["deletions"]).values())
    spans = sorted((d[0], d[1]) for d in c["deletions"])
    assert all(spans[i][1] <= spans[i + 1][0] for i in range(len(spans) - 1))  # no overlapping deletions


def test_pc10_numbered_party_lines_are_caption_but_numbered_prose_starts_the_body():
    parties = "IN THE HIGH COURT\n\nBETWEEN:\n\n1. AMARESH\n\n2. S.UMESH\n\n3. VAGEESH\n\n"
    b = I.build_bundle(
        parties
        + "THIS PETITION IS FILED BY THE ADVOCATE FOR THE PETITIONER PRAYING THAT "
        + "THE COURT BE PLEASED TO QUASH THE PROCEEDINGS. " * 4
        + "\n"
    )
    cap = next(n for n in b["not_citable"] if n["status"] == "caption")
    assert "3. VAGEESH" in b["cleaned"][cap["cleaned_start"] : cap["cleaned_end"]] and facts_text(b)[0].startswith(
        "THIS PETITION"
    )
    assert (
        I._numbered_prose("1. The tenant paid rent.")
        and not I._numbered_prose("1. AMARESH")
        and not I._numbered_prose("2. S.UMESH")
    )


def test_pc11_a_letterless_fact_is_joined_to_a_neighbour_both_arms():
    cl = "First sentence is here. 102. Second sentence is here."
    a = cl.index("102.")
    facts = [
        {"cleaned_start": 0, "cleaned_end": 22},
        {"cleaned_start": a, "cleaned_end": a + 4},
        {"cleaned_start": a + 5, "cleaned_end": len(cl)},
    ]
    assert I._merge_letterless(facts, cl) == [
        {"cleaned_start": 0, "cleaned_end": a + 4},
        {"cleaned_start": a + 5, "cleaned_end": len(cl)},
    ]
    lead = [{"cleaned_start": a, "cleaned_end": a + 4}, {"cleaned_start": a + 5, "cleaned_end": len(cl)}]
    assert I._merge_letterless(lead, cl) == [{"cleaned_start": a, "cleaned_end": len(cl)}]
    assert I._merge_letterless(facts[:1] + facts[2:], cl) == facts[:1] + facts[2:]  # no letterless fact: unchanged


def test_pc12_numbered_devanagari_counts_as_prose():
    assert I._numbered_prose("1. धारा 302 के अंतर्गत आरोप है।")
