# ruff: noqa: E501
"""Cheque-case check logic (cheque_case.py): candidates per B3 role from REAL A3 chronology pages (invented text), ids, readings, the composed page."""

from __future__ import annotations

from datetime import date
from typing import Any

from pravrudhi.application import cheque_case as CC
from pravrudhi.application.chronology import DocMeta, SpanRec, build_chronology
from pravrudhi.application.chronology_config import ChronologyConfig

TODAY = date(2026, 10, 17)
CFG = ChronologyConfig()
PAPER = (
    "1. The cheque dated 1 February 2013 was presented to the bank on 10 February 2013 and returned unpaid for insufficient funds.\n\n"
    "2. The return memo is dated 12.02.2013. The bank's information reached the payee on 14.02.2013.\n\n"
    "3. A notice in writing was sent to the drawer on 20 February 2013 and received on 25 February 2013.\n\n"
    "4. The complaint was filed in March 2013."
)


def chron(*docs: tuple[str, str]) -> dict[str, Any]:
    spans, metas, _pos = [], [], 0
    for i, (name, body) in enumerate(docs):
        spans.append(SpanRec(f"d{i}", name, 1, 0, len(body), body, f"{i}-0"))
        metas.append(DocMeta(f"d{i}", name, 1, (), True))
    return build_chronology(spans, metas, CFG, TODAY)


def roles_of(out: dict[str, Any]) -> dict[str, list[str]]:
    return {r: [c["value"] for c in v] for r, v in out["roles"].items()}


def test_each_step_gets_the_date_written_next_to_its_words():
    out = CC.date_candidates(chron(("cheque-memo-A", PAPER)))
    got = roles_of(out)
    assert got["cheque"] == ["1 February 2013"] and got["presentation"] == ["10 February 2013"]
    assert got["memo"] == ["12.02.2013"] and got["information"] == ["14.02.2013"]
    assert got["notice"] == ["20 February 2013"] and got["receipt"] == ["25 February 2013"]


def test_candidates_carry_the_papers_name_page_source_quote_and_a_stable_id():
    a = CC.date_candidates(chron(("cheque-memo-A", PAPER)))
    b = CC.date_candidates(chron(("cheque-memo-A", PAPER)))
    c = a["roles"]["notice"][0]
    assert (c["document_name"], c["page"], c["source"]) == ("cheque-memo-A", 1, "cheque-memo-A p.1")
    assert "notice in writing was sent" in c["quote"] and len(c["quote"]) <= CC.MAX_QUOTE
    assert c["candidate_id"].startswith("cc-") and c["candidate_id"] == b["roles"]["notice"][0]["candidate_id"]
    ids = [x["candidate_id"] for v in a["roles"].values() for x in v]
    assert len(ids) == len(set(ids)) == 6


def test_a_date_that_reads_two_ways_keeps_all_readings_and_none_is_chosen():
    paper = "The notice was sent to the drawer on 03/04/2013 by registered post."
    out = CC.date_candidates(chron(("n.pdf", paper)))
    cands = [c for v in out["roles"].values() for c in v]
    assert len(cands) == 1 and cands[0]["role"] == "notice"
    c = cands[0]
    assert c["ambiguous"] is True and c["readings"] and len(c["readings"]) == 2 and c["read_as"] is None


def test_no_step_words_means_no_candidate_and_the_fixed_line():
    out = CC.date_candidates(chron(("misc.pdf", "The parties met at the office on 5 May 2012. Tea was served.")))
    assert all(not v for v in out["roles"].values()) and out["dates_without_a_step_word"] == 1
    assert out["no_candidate"]["notice"] == CC.NO_CANDIDATE


def test_approximate_and_month_only_dates_are_listed_as_not_usable_never_offered():
    paper = "The cheque was presented on or about 10 February 2013. The notice was sent in March 2013."
    out = CC.date_candidates(chron(("p.pdf", paper)))
    assert all(not v for v in out["roles"].values())
    assert {x["value"] for x in out["not_usable"]} >= {"March 2013"} and out["not_usable_line"] == CC.NOT_USABLE_LINE


def test_a_bare_received_is_the_notices_receipt_only_when_the_sentence_is_about_a_notice():
    notice = CC.date_candidates(chron(("a.pdf", "The notice was received by the drawer on 25 February 2013.")))
    assert roles_of(notice)["receipt"] == ["25 February 2013"]
    info = CC.date_candidates(chron(("b.pdf", "The payee received the memo from the bank on 14 February 2013.")))
    assert roles_of(info)["information"] == ["14 February 2013"] and not info["roles"]["receipt"]


def test_two_papers_give_candidates_from_each_with_their_own_names():
    out = CC.date_candidates(
        chron(
            ("one.pdf", "The cheque dated 1 February 2013 was issued."),
            ("two.pdf", "The cheque dated 5 February 2013 was presented."),
        )
    )
    names = {c["document_name"] for v in out["roles"].values() for c in v}
    assert names == {"one.pdf", "two.pdf"}


def test_at_most_five_per_role_and_the_rest_are_counted():
    body = " ".join(f"The notice was sent on {d} March 2013." for d in range(1, 9))
    out = CC.date_candidates(chron(("many.pdf", body)))
    assert len(out["roles"]["notice"]) == CC.MAX_PER_ROLE and out["not_shown_over_limit"]["notice"] == 3


def test_b3_date_object_is_unconfirmed_and_has_the_source():
    c = CC.date_candidates(chron(("cheque-memo-A", PAPER)))["roles"]["memo"][0]
    assert CC.b3_date_object(c) == {"value": "12.02.2013", "source": "cheque-memo-A p.1", "confirmed": False}


def test_compose_carries_every_not_assessed_line_and_the_pending_line_without_a_checklist():
    cands = CC.date_candidates(chron(("cheque-memo-A", PAPER)))
    agenda = {"not_assessed": ["x", "y"], "standing_line": "s"}
    page = CC.compose(cands, agenda, None, run_id="cheque-abc", matter_id="m" * 32, chronology_not_assessed=["a", "x"])
    assert page["checklist"] is None and page["checklist_pending"] == CC.CHECKLIST_PENDING and page["statement"] == CC.STATEMENT
    assert page["not_assessed"] == ["a", "x", "y"] and page["agenda"] is agenda
    full = CC.compose(cands, agenda, {"not_assessed": ["z"], "steps": []}, run_id="r", matter_id="m")
    assert full["checklist_pending"] is None and full["not_assessed"] == ["x", "y", "z"]


def test_the_module_calls_no_model_and_imports_no_network_code():
    import inspect

    src = inspect.getsource(CC)
    for bad in ("anthropic", "urllib", "requests", "httpx", "socket", "subprocess"):
        assert bad not in src


def test_a_step_word_after_the_date_is_used_only_when_none_comes_before_it_in_its_sentence():
    out = CC.date_candidates(chron(("p.pdf", "On 10 February 2013 the cheque was presented at the branch.")))
    assert roles_of(out)["presentation"] == ["10 February 2013"]


def test_the_words_of_the_previous_sentence_do_not_decide_the_next_dates_role():
    out = CC.date_candidates(
        chron(("p.pdf", "The cheque dated 1 February 2013 was issued. On 10 February 2013 the notice was sent to the drawer."))
    )
    got = roles_of(out)
    assert got["cheque"] == ["1 February 2013"] and got["notice"] == ["10 February 2013"]


def test_the_nearest_step_words_before_the_date_win_when_two_steps_are_named():
    out = CC.date_candidates(chron(("p.pdf", "The return memo and the demand notice on 12 February 2013 were noted.")))
    c = [c for v in out["roles"].values() for c in v]
    assert len(c) == 1 and c[0]["role"] == "notice"
