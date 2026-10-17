"""B3 s.138 checklist (wf_b3_s138_checklist): the date reader, the three pinned steps, every REFER rule, the fixed lines, the
wording scan and Track B's 42 vectors. Built and tested on recitals (printed dates from public judgments); no live lawyer use.
Both arms of every rule: one test for the case that computes and one for the case that REFERs."""

from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from pravrudhi.application import s138_checklist as B
from pravrudhi.application.date_parse import add_months, parse_date
from pravrudhi.application.s138_checklist import ChecklistRefused, ChecklistRequest, DateIn
from pravrudhi.application.s138_checklist_config import S138ChecklistConfig, parse_s138_checklist_config

CFG = S138ChecklistConfig()
TODAY = date(2026, 10, 16)  # the injected clock: no test depends on the real date
REPO = Path(__file__).resolve().parent.parent
VECTORS = json.loads((REPO / "tests" / "fixtures" / "s138_vectors.json").read_text(encoding="utf-8"))["rows"]


def d(value: str, *, confirmed: bool = True, other: bool = False, as_: Any = ..., century: int = 2000) -> dict[str, Any]:
    """One typed date. `as_` is the reading the person confirms: by default the server's own reading (what a UI echoes back)."""
    if as_ is ...:
        p = parse_date(value, century)
        as_ = p.value.isoformat() if (confirmed and p.value) else None
    return {
        "value": value,
        "source": "typed by the user",
        "confirmed": confirmed,
        "confirmed_as": as_,
        "also_in_other_text": other,
    }


def req(**kw: Any) -> ChecklistRequest:
    return ChecklistRequest.model_validate(kw)


def later(day_month_year: str) -> dict[str, Any]:
    """The rest of the chain moved to follow a presentation on `day_month_year` (the order rule needs a possible order)."""
    base = parse_date(day_month_year).value
    assert base is not None
    from datetime import timedelta as td

    def f(n: int) -> list[dict[str, Any]]:
        return [d((base + td(days=n)).strftime("%d.%m.%Y"))]

    return {"return_memos": f(2), "information_received": f(2), "notices": f(9), "notice_receipts": f(10)}


FULL: dict[str, Any] = {
    "cheque_date": d("01.02.2013"),
    "validity_months": 3,
    "presentations": [d("10.03.2013")],
    "return_memos": [d("12.03.2013")],
    "information_received": [d("12.03.2013")],
    "notices": [d("20.03.2013")],
    "notice_receipts": [d("21.03.2013")],
    "notice_mode": "registered_post",
}


def run(**over: Any) -> dict[str, Any]:
    body = {**FULL, **over}
    return B.check(req(**body), CFG, run_id="t", today=TODAY)


def step(out: dict[str, Any], name: str) -> dict[str, Any]:
    return next(s for s in out["steps"] if s["step"] == name)


def codes(s: dict[str, Any]) -> set[str]:
    return {r["code"] for r in s["refer"]}


# ---- the date reader: every printed shape in Track B's table ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "iso"),
    [
        ("13.03.2013", "2013-03-13"),
        ("2.12.2006", "2006-12-02"),
        ("7.01.2009", "2009-01-07"),
        ("4-1-1997", "1997-01-04"),
        ("22-09-2003", "2003-09-22"),
        ("5-1 -1990", "1990-01-05"),
        ("23-08- 2003", "2003-08-23"),
        ("15/10/1995", "1995-10-15"),
        ("11/6/1996", "1996-06-11"),
        ("9th October, 2006", "2006-10-09"),
        ("26th May, 1998", "1998-05-26"),
        ("10 April 2014", "2014-04-10"),
        ("22 September 2012", "2012-09-22"),
        ("May 28, 1998", "1998-05-28"),
        ("June 2, 1998", "1998-06-02"),
        ("6th of February, 1990", "1990-02-06"),
        ("dt. 20.11.2000", "2000-11-20"),
        ("dated 22.8.2006", "2006-08-22"),
        ("the date „15/10/1995‚", None),  # a sentence around a date is not a date: the caller gives ONE date
    ],
)
def test_every_printed_shape_is_read_day_first_or_not_read(text: str, iso: str | None) -> None:
    p = parse_date(text)
    assert (p.value.isoformat() if p.value else None) == iso


def test_a_quote_mark_around_a_date_is_stripped_but_prose_is_not_read() -> None:
    assert parse_date("„15/10/1995‚").value == date(1995, 10, 15)
    assert parse_date("served on the 3rd").issue == "unparsed"
    assert parse_date("").issue == "not_a_date" and parse_date("32.01.2010").issue == "unparsed"


@pytest.mark.parametrize(
    "text", ["around 9th October, 2006", "on or about 2.03.2001", "on or about 08-10-2009", "approx. 3.4.2010"]
)
def test_an_approximate_date_is_flagged_never_a_value(text: str) -> None:
    p = parse_date(text)
    assert p.issue == "approximate"


@pytest.mark.parametrize(
    "text", ["Between 22 September 2012 and 5 October 2012", "23.11.2004 and 25.11.2004", "from 1.2.2010 to 9.2.2010"]
)
def test_a_range_is_flagged_never_a_value(text: str) -> None:
    p = parse_date(text)
    assert p.issue == "range" and p.value is None


def test_a_two_digit_year_needs_a_century_that_a_full_year_date_fixes() -> None:
    assert parse_date("27.7.06").issue == "two_digit_year" and parse_date("27.7.06").value is None
    assert parse_date("27.7.06", 2000).value == date(2006, 7, 27)
    assert parse_date("1.7.98", 1900).value == date(1998, 7, 1)
    assert parse_date("29.2.03", 2000).issue == "unparsed"  # no such day


def test_the_day_first_order_is_stated_when_it_was_assumed() -> None:
    assert parse_date("03.04.2010").day_first_assumed is True
    assert parse_date("13.04.2010").day_first_assumed is False and parse_date("11.11.2010").day_first_assumed is False


@pytest.mark.parametrize(
    ("start", "months", "end"),
    [
        (date(2013, 8, 31), 6, date(2014, 2, 28)),
        (date(2011, 8, 31), 6, date(2012, 2, 29)),  # a leap year
        (date(2013, 1, 31), 1, date(2013, 2, 28)),
        (date(2013, 3, 17), 6, date(2013, 9, 17)),
        (date(2013, 11, 30), 3, date(2014, 2, 28)),
    ],
)
def test_calendar_month_arithmetic_takes_the_last_day_of_a_shorter_month(start: date, months: int, end: date) -> None:
    assert add_months(start, months) == end


# ---- the three pinned steps, computed ---------------------------------------------------------------------------------------


def test_a_clean_chain_computes_inside_for_presentation_and_notice_and_a_payment_window() -> None:
    out = run()
    assert step(out, "presentation")["status"] == "INSIDE" and step(out, "notice")["status"] == "INSIDE"
    pay = step(out, "payment")
    assert pay["status"] == "COMPUTED" and pay["counts"]["A"]["period_ends"] == "2013-04-05"
    assert pay["counts"]["B"]["period_ends"] == "2013-04-04"


def test_presentation_is_outside_when_after_the_earlier_of_six_months_and_the_validity() -> None:
    out = run(presentations=[d("10.06.2013")], **later("10.06.2013"))  # validity 3 months from 01.02.2013 ends 01.05.2013
    s = step(out, "presentation")
    assert s["status"] == "OUTSIDE" and s["counts"]["A"]["period_ends"] == "2013-05-01"
    out2 = run(
        validity_months=12, presentations=[d("10.08.2013")], **later("10.08.2013")
    )  # six months is the earlier end: 01.08.2013
    assert step(out2, "presentation")["counts"]["A"]["period_ends"] == "2013-08-01"
    assert step(out2, "presentation")["status"] == "OUTSIDE"


def test_the_notice_is_outside_after_day_thirty_by_both_counts() -> None:
    out = run(notices=[d("20.04.2013")], notice_receipts=[d("22.04.2013")])  # 39 days after 12.03.2013
    s = step(out, "notice")
    assert s["status"] == "OUTSIDE" and s["counts"]["A"]["days_after_start"] == 39
    assert "refer to a lawyer" in s["result"] and "condone" not in s["result"]


def test_day_thirty_is_refer_with_both_counts_because_the_conventions_differ() -> None:
    out = run(
        notices=[d("11.04.2013")], notice_receipts=[d("12.04.2013")]
    )  # day 30 after 12.03.2013: inside if the first day is excluded, outside if included
    s = step(out, "notice")
    assert s["status"] == "REFER" and "conventions_differ" in codes(s)
    assert s["counts"]["A"]["status"] == "inside" and s["counts"]["B"]["status"] == "outside"
    assert "both" in s["result"].lower() or "A: inside" in s["result"]


def test_every_step_text_is_the_dates_you_entered_never_a_finding() -> None:
    out = run(notices=[d("20.04.2013")], notice_receipts=[d("22.04.2013")])
    text = json.dumps(out).lower()
    assert "the dates you entered fall outside the stated period; refer to a lawyer" in text and "condone" not in text


# ---- every REFER rule, one test each ----------------------------------------------------------------------------------------


def test_refer_missing_information_received_date() -> None:
    s = step(run(information_received=[]), "notice")
    assert s["status"] == "REFER" and "information_missing" in codes(s)


def test_refer_missing_receipt_of_notice_date_and_ask_the_mode() -> None:
    s = step(run(notice_receipts=[]), "payment")
    assert s["status"] == "REFER" and "receipt_missing" in codes(s)
    assert "registered_post" in json.dumps(s) and "deemed" in json.dumps(s)  # a deemed date is never computed


def test_refer_missing_return_memo_date() -> None:
    s = step(run(return_memos=[]), "notice")
    assert s["status"] == "REFER" and "memo_missing" in codes(s)


def test_refer_several_memo_dates_for_several_cheques() -> None:
    out = run(return_memos=[d("12.03.2013"), d("13.03.2013")])
    for name in ("notice", "payment"):
        assert "more_than_one_candidate" in codes(step(out, name))
    assert "memo_several" in codes(step(out, "notice")) and "more_than_one_candidate" in codes(step(out, "notice"))
    assert step(out, "notice")["refer"][-1]["candidate_chains"]["memo"] == ["12.03.2013", "13.03.2013"]


@pytest.mark.parametrize("value", ["on or about 14.03.2013", "around 14.03.2013"])
def test_refer_an_approximate_date(value: str) -> None:
    s = step(run(information_received=[d(value)]), "notice")
    assert s["status"] == "REFER" and "information_approximate_date" in codes(s)


def test_refer_a_range_of_dates() -> None:
    s = step(run(notices=[d("Between 22 September 2012 and 5 October 2012")]), "notice")
    assert s["status"] == "REFER" and "notice_date_range" in codes(s)


def test_refer_a_missing_validity_period_and_never_default_to_six_months() -> None:
    body = {k: v for k, v in FULL.items() if k != "validity_months"}
    s = step(B.check(req(**body), CFG), "presentation")
    assert s["status"] == "REFER" and "validity_missing" in codes(s) and s["counts"] == {}


def test_refer_more_than_one_presentation_listing_every_candidate() -> None:
    out = run(presentations=[d("10.03.2013"), d("20.03.2013")])
    s = step(out, "presentation")
    assert s["status"] == "REFER" and "presentation_several" in codes(s)
    chains = next(r for r in s["refer"] if r["code"] == "more_than_one_candidate")["candidate_chains"]
    assert chains == {"presentation": ["10.03.2013", "20.03.2013"]}
    assert step(out, "notice")["status"] == "REFER" and step(out, "payment")["status"] == "REFER"  # the chain is unknown


def test_refer_more_than_one_notice_and_one_receipt() -> None:
    out = run(notices=[d("20.03.2013"), d("27.03.2013")], notice_receipts=[d("22.03.2013"), d("29.03.2013")])
    assert "notice_several" in codes(step(out, "notice")) and "receipt_several" in codes(step(out, "payment"))


def test_refer_a_two_digit_year_when_no_full_year_date_fixes_the_century() -> None:
    body = {
        "cheque_date": d("28.04.06"),
        "validity_months": 3,
        "presentations": [d("1.5.06")],
        "return_memos": [d("27.7.06")],
        "information_received": [d("3.8.06")],
        "notices": [d("14.8.06")],
        "notice_receipts": [d("20.8.06")],
    }
    out = B.check(req(**body), CFG)
    assert all("two_digit_year" in "".join(codes(step(out, n))) for n in ("presentation", "notice", "payment"))


def test_a_two_digit_year_is_read_when_the_full_year_dates_agree_on_the_century() -> None:
    out = run(notices=[d("20.03.13")])  # the other dates say 2013
    assert step(out, "notice")["status"] == "INSIDE"


def test_two_full_year_dates_in_different_centuries_do_not_fix_the_century() -> None:
    out = run(notices=[d("20.03.13")], return_memos=[d("12.03.1913")])
    assert "notice_two_digit_year" in codes(step(out, "notice"))


def test_refer_a_date_that_also_appears_in_other_text_the_user_gave() -> None:
    s = step(run(notices=[d("20.03.2013", other=True)]), "notice")
    assert s["status"] == "REFER" and "notice_date_in_other_text" in codes(s)


@pytest.mark.parametrize("value", ["15.03.2020", "01.01.2021", "31.05.2022"])
def test_refer_any_date_in_the_pandemic_era_window(value: str) -> None:
    s = step(run(information_received=[d(value)]), "notice")
    assert s["status"] == "REFER" and "information_pandemic_window" in codes(s)


@pytest.mark.parametrize("value", ["14.03.2020", "01.06.2022"])
def test_dates_just_outside_the_pandemic_window_are_not_referred_for_it(value: str) -> None:
    body = {
        "cheque_date": d("01.02.2020"),
        "validity_months": 3,
        "presentations": [d("10.03.2020")],
        "return_memos": [d(value)],
        "information_received": [d(value)],
        "notices": [d(value)],
        "notice_receipts": [d(value)],
    }
    assert "pandemic_window" not in "".join(codes(step(B.check(req(**body), CFG), "notice")))


def test_refer_an_unconfirmed_input_and_compute_nothing_from_it() -> None:
    out = run(notices=[d("20.03.2013", confirmed=False)])
    s = step(out, "notice")
    assert s["status"] == "REFER" and "notice_not_confirmed" in codes(s) and s["counts"] == {}
    assert next(i for i in out["inputs"] if i["role"] == "notice")["confirmed"] is False


def test_refer_when_the_notice_date_is_before_the_information_date() -> None:
    s = step(run(notices=[d("01.03.2013")]), "notice")
    assert s["status"] == "REFER" and "dates_in_impossible_order" in codes(s) and s["counts"] == {}


def test_refer_when_the_last_day_is_a_weekend_and_the_date_is_after_it() -> None:
    # information Fri 01.03.2013: day 30 is Sun 31.03.2013 (first day excluded) or Sat 30.03.2013 (first day included)
    out = run(
        presentations=[d("20.02.2013")],
        information_received=[d("01.03.2013")],
        return_memos=[d("01.03.2013")],
        notices=[d("03.04.2013")],
        notice_receipts=[d("04.04.2013")],
    )
    s = step(out, "notice")
    assert s["status"] == "REFER" and "last_day_is_a_weekend" in codes(s)
    ok = step(
        run(
            presentations=[d("20.02.2013")],
            information_received=[d("01.03.2013")],
            return_memos=[d("01.03.2013")],
            notices=[d("20.03.2013")],
        ),
        "notice",
    )
    assert ok["status"] == "INSIDE"  # a date comfortably before a weekend last day needs no holiday rule


def test_the_notice_mode_is_noted_when_missing_and_not_when_given() -> None:
    body = {k: v for k, v in FULL.items() if k != "notice_mode"}
    assert "How the notice was sent is not stated" in step(B.check(req(**body), CFG), "payment")["notes"]
    assert "notes" not in step(run(), "payment")


def test_the_complaint_window_is_never_computed() -> None:
    out = run()
    s = step(out, "complaint_window")
    assert s["status"] == "REFER" and "complaint_window_not_computed" in codes(s)
    assert s["counts"] == {} and "not computed" in s["result"]
    out2 = B.check(req(**{**FULL, "validity_months": None}), CFG)
    assert step(out2, "complaint_window")["status"] == "REFER"


# ---- the lines that are in EVERY response -----------------------------------------------------------------------------------


@pytest.mark.parametrize("over", [{}, {"notices": []}, {"information_received": [], "notice_receipts": [], "presentations": []}])
def test_the_not_assessed_lines_the_holiday_note_and_the_statement_are_in_every_response(over: dict[str, Any]) -> None:
    out = run(**over)
    assert out["not_assessed"] == list(B.NOT_ASSESSED) and len(out["not_assessed"]) == 8
    assert B.HOLIDAY_NOTE in out["notes"] and out["statement"] == "A checklist, not a decision"
    assert out["lawyer_confirmed"] is False and "they are referred to a lawyer" in out["description"]
    assert out["description"].startswith("Built and tested on court judgment recitals only, not on the merits of any case.")
    assert "No lawyer has used this on a live matter." in out["description"] and "REFER-only" not in out["description"]


def test_an_empty_request_is_all_refer_with_the_fixed_lines() -> None:
    out = B.check(req(), CFG)
    assert [s["status"] for s in out["steps"]] == ["REFER"] * 4 and out["refer_count"] == 4 and out["inputs"] == []


def test_a_list_longer_than_the_cap_is_refused_not_cut() -> None:
    small = S138ChecklistConfig(max_dates_per_role=2)
    with pytest.raises(ChecklistRefused) as e:
        B.check(req(notices=[d("20.03.2013")] * 3), small)
    assert e.value.code == "too_many_dates" and e.value.status_code == 413


def test_the_request_takes_no_free_text_so_no_first_date_is_ever_picked() -> None:
    with pytest.raises(ValidationError):
        req(text="The cheque dated 1.2.2013 was presented on 3.2.2013")
    with pytest.raises(ValidationError):
        ChecklistRequest.model_validate({"notices": [{"value": "1.1.2013", "source": ""}]})  # a source is required
    with pytest.raises(ValidationError):
        ChecklistRequest.model_validate({"notices": [{"value": "1.1.2013", "source": "x", "confirmed": "true"}]})
    assert DateIn.model_fields["source"].is_required()


# ---- the wording scan and the pinned text -----------------------------------------------------------------------------------

BANNED = ("barred", "premature", "invalid", "fatal", "within limitation", "time-barred", "time barred")


@pytest.mark.parametrize(
    "path",
    [
        "src/pravrudhi/application/s138_checklist.py",
        "src/pravrudhi/application/date_parse.py",
        "src/pravrudhi/application/s138_checklist_config.py",
        "src/pravrudhi/assets/s138/ni_act_s138_139.json",
    ],
)
def test_no_module_or_asset_says_barred_premature_invalid_fatal_or_within_limitation(path: str) -> None:
    text = (REPO / path).read_text(encoding="utf-8").lower()
    assert [w for w in BANNED if w in text] == []


def test_the_scan_can_fail() -> None:
    assert any(w in "the notice was premature and time barred" for w in BANNED)


def test_no_response_for_any_input_contains_a_banned_word() -> None:
    for over in ({}, {"notices": [d("20.04.2013")]}, {"notices": [d("13.04.2013")]}, {"information_received": []}):
        text = json.dumps(run(**over)).lower()
        assert [w for w in BANNED if w in text] == []


def test_the_asset_carries_the_held_text_its_sha256_and_the_three_periods() -> None:
    a = B.load_asset()
    assert hashlib.sha256(a["s138_text"].encode()).hexdigest() == a["provenance"]["s138_text_sha256"]
    assert hashlib.sha256(a["s139_text"].encode()).hexdigest() == a["provenance"]["s139_text_sha256"]
    assert a["provenance"]["s138_text_sha256"] == "e67eae9bbc65394590ee0bb00b987bcebc03132aa68059d71ca8f52f21b5c77a"
    for phrase in (
        "within a period of six months from the date on which it is drawn",
        "[within thirty days]",
        "within fifteen days of the receipt",
    ):
        assert phrase in a["s138_text"] or phrase.strip("[]") in a["s138_text"].replace("3 [", "")
    assert a["pinned_steps"]["presentation"]["months"] == 6 and a["pinned_steps"]["notice"]["days"] == 30
    assert a["pinned_steps"]["payment"]["days"] == 15 and a["lawyer_confirmed"] is False
    assert len(a["refer_only"]) == 4


def test_the_periods_the_code_uses_are_the_periods_in_the_asset() -> None:
    a = B.load_asset()
    out = run()
    assert step(out, "payment")["counts"]["A"]["period_ends"] == "2013-04-05"  # receipt 21.03 + 15 = the asset's payment days
    assert a["pinned_steps"]["payment"]["days"] == 15
    assert step(out, "notice")["counts"]["A"]["period_ends"] == "2013-04-11"  # information 12.03 + 30


# ---- the setting: fail closed -----------------------------------------------------------------------------------------------


def test_only_the_boolean_true_turns_the_checklist_on() -> None:
    assert parse_s138_checklist_config({"wf_b3_s138_checklist": True}) == S138ChecklistConfig()
    for flag in (False, None, "true", "yes", "on", 1, 0, [], {}):
        assert parse_s138_checklist_config({"wf_b3_s138_checklist": flag}) is None
    assert parse_s138_checklist_config({}) is None and parse_s138_checklist_config(None) is None
    assert parse_s138_checklist_config("wf_b3_s138_checklist: true") is None


@pytest.mark.parametrize("bad", [0, -1, 99, "3", 2.5, True, None])
def test_a_malformed_cap_with_the_flag_on_turns_it_off(bad: Any) -> None:
    assert parse_s138_checklist_config({"wf_b3_s138_checklist": True, "s138_checklist_max_concurrent": bad}) is None
    assert (
        parse_s138_checklist_config({"wf_b3_s138_checklist": True, "s138_checklist_max_dates_per_role": bad}) is None or bad == 99
    )


# ---- Track B's 42 vectors, from the PRINTED dates ---------------------------------------------------------------------------

_ROLE = {
    "cheque": ("cheque",),
    "cheque1": ("cheque",),
    "cheque2": ("cheque",),
    "cheques": ("cheque",),
    "presented": ("presentations",),
    "first_presented": ("presentations",),
    "represented": ("presentations",),
    "banker_received": ("presentations",),
    "memo": ("return_memos",),
    "memo1": ("return_memos",),
    "memo2": ("return_memos",),
    "memos": ("return_memos",),
    "bank_informed": ("information_received",),
    "information_received": ("information_received",),
    "returned": ("information_received",),
    "forwarding_letter": ("information_received",),
    "intimation": ("information_received",),
    "notice": ("notices",),
    "notice1": ("notices",),
    "notice2": ("notices",),
    "first_notice": ("notices",),
    "second_notice": ("notices",),
    "formal_notice": ("notices",),
    "notice_sent": ("notices",),
    "notice_issued": ("notices",),
    "notice_posted": ("notices",),
    "notices": ("notices",),
    "notice_received": ("notice_receipts",),
    "served": ("notice_receipts",),
    "notice_served": ("notice_receipts",),
    "deemed_served": ("notice_receipts",),
}


def vector_request(row: dict[str, Any]) -> ChecklistRequest:
    """Every printed date into the role its key names, all CONFIRMED (the user's part), no validity period (a vector never has
    one), and NO first-date picking: several cheque dates leave the cheque date empty for the user to choose."""
    body: dict[str, Any] = {"validity_months": None}
    cheques: list[str] = []
    # a two-digit year takes the century of the row's full-year dates (the user's reading, echoed back and confirmed)
    century = 1900 if any((p := parse_date(v)).value and p.value.year < 2000 for v in row["dates_as_printed"].values()) else 2000
    for key, value in row["dates_as_printed"].items():
        role = _ROLE.get(key)
        if role is None:
            continue
        if role[0] == "cheque":
            cheques.append(value)
        else:
            body.setdefault(role[0], []).append(d(value, century=century))
    if len(cheques) == 1:
        body["cheque_date"] = d(cheques[0], century=century)
    return ChecklistRequest.model_validate(body)


def test_all_42_vectors_run_from_their_printed_dates_and_never_raise() -> None:
    assert len(VECTORS) == 42 and len({r["row"] for r in VECTORS}) == 42
    for row in VECTORS:
        out = B.check(vector_request(row), CFG)
        assert len(out["steps"]) == 4 and out["not_assessed"] == list(B.NOT_ASSESSED)
        assert step(out, "complaint_window")["status"] == "REFER"  # no vector is answered on the complaint window


def test_no_vector_is_ever_given_a_computed_status_for_a_step_it_cannot_support() -> None:
    """Every vector lacks a validity period, so no vector can compute the presentation step."""
    for row in VECTORS:
        assert step(B.check(vector_request(row), CFG), "presentation")["status"] == "REFER"


def test_the_refer_rate_on_the_42_vectors_is_measured_and_pinned() -> None:
    per_step: dict[str, int] = {"presentation": 0, "notice": 0, "payment": 0}
    any_computed = 0
    for row in VECTORS:
        out = B.check(vector_request(row), CFG)
        computed = [s for s in out["steps"] if s["step"] in per_step and s["status"] != "REFER"]
        any_computed += bool(computed)
        for s in out["steps"]:
            if s["step"] in per_step and s["status"] == "REFER":
                per_step[s["step"]] += 1
    assert per_step == EXPECTED_REFER["per_step"], per_step
    assert any_computed == EXPECTED_REFER["rows_with_a_computed_step"], any_computed


EXPECTED_REFER: dict[str, Any] = {"per_step": {"presentation": 42, "notice": 41, "payment": 34}, "rows_with_a_computed_step": 9}


def _row(n: int) -> dict[str, Any]:
    return next(r for r in VECTORS if r["row"] == n)


def test_row_12_the_payment_window_ends_where_the_court_counted_it() -> None:
    assert "25-1-1997" in _row(12)["court_outcome"]  # court-stated: 15 days from service 10-1-1997, day of service excluded
    out = B.check(ChecklistRequest.model_validate({"notice_receipts": [d(_row(12)["dates_as_printed"]["served"])]}), CFG)
    assert step(out, "payment")["counts"]["A"]["period_ends"] == "1997-01-25"
    assert step(out, "payment")["counts"]["B"]["period_ends"] == "1997-01-24"  # the other convention is shown, not hidden


def test_row_15_the_payment_window_ends_where_the_court_counted_it() -> None:
    assert "6 FEBRUARY 1990" in _row(15)["court_outcome"]
    rec = _row(15)["dates_as_printed"]["notice_received"]
    out = B.check(ChecklistRequest.model_validate({"notice_receipts": [d(rec)]}), CFG)
    assert step(out, "payment")["counts"]["A"]["period_ends"] == "1990-02-06"


def test_row_1_thirty_days_from_the_second_dishonour_is_outside_by_both_counts() -> None:
    assert "NOTICE OUT OF TIME" in _row(1)["court_outcome"]
    pr = _row(1)["dates_as_printed"]
    out = B.check(
        ChecklistRequest.model_validate(
            {
                "return_memos": [d(pr["represented"])],
                "information_received": [d(pr["represented"])],
                "notices": [d(pr["notice2"])],
            }
        ),
        CFG,
    )
    s = step(out, "notice")
    assert s["status"] == "OUTSIDE" and s["counts"]["A"]["days_after_start"] == 37  # the court counted from the second dishonour


def test_row_1_as_printed_with_two_presentations_and_two_notices_is_refer_listing_every_candidate() -> None:
    out = B.check(vector_request(_row(1)), CFG)
    for name in ("notice", "payment"):
        ch = next(r for r in step(out, name)["refer"] if r["code"] == "more_than_one_candidate")["candidate_chains"]
        assert ch["notice"] == ["27.10.2008", "17.12.2008"] and ch["presentation"] == ["25.10.2008", "10.11.2008"]


def test_row_19_day_thirty_is_refer_with_both_counts_and_the_courts_count_is_one_of_them() -> None:
    assert "NOTICE IN TIME" in _row(19)["court_outcome"]  # the High Court excluded the information day
    pr = _row(19)["dates_as_printed"]
    base = {"information_received": [d(pr["information_received"])], "notices": [d(pr["notice"])]}
    assert "memo_missing" in codes(
        step(B.check(ChecklistRequest.model_validate(base), CFG), "notice")
    )  # the row has no memo date
    out = B.check(
        ChecklistRequest.model_validate({**base, "return_memos": [d(pr["information_received"])]}), CFG
    )  # supplied by this test
    s = step(out, "notice")
    assert s["status"] == "REFER" and "conventions_differ" in codes(s)
    assert s["counts"]["A"]["status"] == "inside" and s["counts"]["B"]["status"] == "outside"  # A is the High Court's count
    assert s["counts"]["A"]["days_after_start"] == 30


def test_row_29_a_notice_eleven_days_after_the_bank_information_is_inside() -> None:
    assert "NOTICE IN TIME (11 days)" in _row(29)["court_outcome"]
    pr = _row(29)["dates_as_printed"]
    out = B.check(
        ChecklistRequest.model_validate(
            {"return_memos": [d(pr["returned"])], "information_received": [d(pr["returned"])], "notices": [d(pr["notice"])]}
        ),
        CFG,
    )
    s = step(out, "notice")
    assert s["status"] == "INSIDE" and s["counts"]["A"]["days_after_start"] == 11


@pytest.mark.parametrize("n", [3, 5, 13, 16, 22, 23, 24, 25, 26])
def test_the_complaint_rows_are_never_answered_whatever_the_court_said(n: int) -> None:
    """Rows where the court stated that a complaint was in time: B3 does not compute the complaint window, in any row."""
    assert "IN TIME" in _row(n)["court_outcome"]
    out = B.check(vector_request(_row(n)), CFG)
    s = step(out, "complaint_window")
    assert s["status"] == "REFER" and s["counts"] == {} and s["result"].startswith("REFER:")
    assert "INSIDE" not in json.dumps(s) and "OUTSIDE" not in json.dumps(s)


@pytest.mark.parametrize("n", [20, 32, 33])
def test_court_recited_rows_test_the_reader_only_and_never_pass_a_recited_finding_on(n: int) -> None:
    assert _row(n)["court_outcome_label"].startswith("court-recited")
    out = B.check(vector_request(_row(n)), CFG)
    assert json.dumps(out).lower().count("recited") == 0


def test_the_asset_is_not_in_the_retrieval_corpus_folder() -> None:
    """assets/nyaya/*.json is read as statute-provision corpus by retrieval and the demo export; this record has another shape."""
    assert B.ASSET.parent.name == "s138" and B.ASSET.is_file()
    assert not (REPO / "src" / "pravrudhi" / "assets" / "nyaya" / B.ASSET.name).exists()


# ---- the optional, user-entered cheque validity ----------------------------------------------------------------------------


def test_a_validity_entered_by_the_user_is_echoed_back_as_user_entered() -> None:
    out = run(validity_months=3)
    assert out["validity"] == {"months": 3, "entered_by_user": True}
    note = step(out, "presentation")["notes"][0]
    assert "entered by you" in note and "not in the section" in note and "3 months" in note
    unspecified = B.check(req(**{k: v for k, v in FULL.items() if k != "validity_months"}), CFG)
    assert unspecified["validity"] == {"months": None, "entered_by_user": False} and "notes" not in step(
        unspecified, "presentation"
    )


def test_a_shorter_validity_gives_a_computed_presentation_step_when_nothing_else_is_missing() -> None:
    s = step(run(validity_months=3, presentations=[d("10.03.2013")]), "presentation")
    assert s["status"] == "INSIDE" and s["period_basis"] == {
        "six_months_end": "2013-08-01",
        "validity_end": "2013-05-01",
        "earlier_used": "validity",
    }
    late = later("10.06.2013")
    assert step(run(validity_months=3, presentations=[d("10.06.2013")], **late), "presentation")["status"] == "OUTSIDE"


def test_a_validity_longer_than_six_months_does_not_extend_the_period() -> None:
    s = step(run(validity_months=12, presentations=[d("10.08.2013")], **later("10.08.2013")), "presentation")
    assert s["status"] == "OUTSIDE" and s["period_basis"]["earlier_used"] == "six_months"
    assert s["counts"]["A"]["period_ends"] == "2013-08-01"  # not 2014-02-01


def test_a_validity_of_exactly_six_months_is_the_six_month_end() -> None:
    s = step(run(validity_months=6), "presentation")
    assert s["period_basis"]["earlier_used"] == "six_months" and s["counts"]["A"]["period_ends"] == "2013-08-01"


def test_without_a_validity_the_presentation_step_stays_refer_with_no_computation() -> None:
    s = step(B.check(req(**{k: v for k, v in FULL.items() if k != "validity_months"}), CFG), "presentation")
    assert s["status"] == "REFER" and "validity_missing" in codes(s) and "period_basis" not in s and s["counts"] == {}


def test_every_fixed_line_and_every_shown_text_starts_with_a_capital_letter() -> None:
    for over in ({}, {"notices": [], "information_received": []}, {"notice_mode": None}):
        out = run(**over)
        texts = [out["statement"], out["description"], *out["not_assessed"], *out["notes"]]
        for s in out["steps"]:
            texts += [s["result"], *[r["text"] for r in s["refer"]], *s.get("notes", [])]
        assert all(t[:1].isupper() for t in texts if t), [t for t in texts if t and not t[:1].isupper()]


def test_the_not_assessed_lines_are_the_eight_in_the_readers_order() -> None:
    n = B.NOT_ASSESSED
    assert len(n) == 8 and n[0].startswith("Whether the underlying debt") and n[1].startswith("Whether the cheque was drawn")
    assert n[2].startswith("The reason the bank gave") and n[3].startswith("Whether the notice demands")
    assert n[4].startswith("How the notice was served") and n[5] == "Territorial jurisdiction"
    assert "(s.141)" in n[6] and n[7].startswith("The standing")


def test_the_source_label_names_the_held_copy_and_its_capture_date() -> None:
    assert (
        run()["source"]["act"]
        == "Negotiable Instruments Act, 1881, s.138 proviso (a) to (c), India Code copy captured 24 Sep 2026"
    )
    assert B.load_asset()["provenance"]["captured"].startswith("2026-09-24")


def test_no_text_speaks_of_a_matter_store() -> None:
    out = run(notices=[d("20.03.2013", other=True)])
    text = json.dumps(out).lower()
    assert "other text you gave us" in text and "another matter" not in text


# ---- B2: the order of the dates -------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("over", "later_role", "earlier_role"),
    [
        (
            {"information_received": [d("05.03.2013")], "return_memos": [d("12.03.2013")]},
            "information from the bank",
            "return memo",
        ),
        ({"notice_receipts": [d("15.03.2013")], "notices": [d("20.03.2013")]}, "receipt of the notice", "notice"),
        ({"presentations": [d("20.03.2013")], "return_memos": [d("12.03.2013")]}, "return memo", "presentation"),
        (
            {"information_received": [d("05.03.2013")], "presentations": [d("10.03.2013")], "return_memos": []},
            "information",
            "presentation",
        ),
        ({"presentations": [d("20.01.2013")]}, "presentation", "cheque date"),
    ],
)
def test_refer_when_the_dates_are_in_an_impossible_order(over: dict[str, Any], later_role: str, earlier_role: str) -> None:
    out = run(**over)
    for name in ("presentation", "notice", "payment"):
        s = step(out, name)
        assert s["status"] == "REFER" and "dates_in_impossible_order" in codes(s) and s["counts"] == {}
    text = step(out, "notice")["result"]
    assert "not in the order the steps follow" in text and later_role in text and earlier_role in text


def test_dates_on_the_same_day_are_not_out_of_order_and_the_clean_chain_still_computes() -> None:
    out = run(return_memos=[d("10.03.2013")], presentations=[d("10.03.2013")], information_received=[d("10.03.2013")])
    assert step(out, "notice")["status"] == "INSIDE" and "dates_in_impossible_order" not in codes(step(out, "notice"))


def test_the_order_is_checked_across_a_missing_middle_date() -> None:
    out = run(return_memos=[], notices=[d("05.03.2013")])  # notice before the information date, memo not given
    assert "dates_in_impossible_order" in codes(step(out, "notice")) and "memo_missing" in codes(step(out, "notice"))


def test_an_approximate_or_unconfirmed_date_is_not_compared_for_order() -> None:
    out = run(notices=[d("on or about 05.03.2013")])
    assert "dates_in_impossible_order" not in codes(step(out, "notice")) and "notice_approximate_date" in codes(
        step(out, "notice")
    )


# ---- B3: a PERIOD that touches the pandemic window ---------------------------------------------------------------------------


def chain(cheque: str, presented: str, memo: str, info: str, notice: str, receipt: str, validity: int = 6) -> dict[str, Any]:
    return {
        "cheque_date": d(cheque),
        "validity_months": validity,
        "presentations": [d(presented)],
        "return_memos": [d(memo)],
        "information_received": [d(info)],
        "notices": [d(notice)],
        "notice_receipts": [d(receipt)],
        "notice_mode": "speed_post",
    }


def test_refer_when_the_thirty_days_run_into_the_window_though_every_typed_date_is_outside_it() -> None:
    out = B.check(req(**chain("01.01.2020", "10.01.2020", "11.01.2020", "02.03.2020", "10.06.2022", "11.06.2022")), CFG)
    assert step(out, "notice")["status"] == "REFER" and "pandemic_period" in codes(step(out, "notice"))
    assert not any(
        "pandemic_window" in i["problems"] for i in out["inputs"] if i["role"] in ("information",)
    )  # info is before it


def test_refer_when_the_fifteen_days_end_inside_the_window() -> None:
    out = B.check(req(**chain("01.01.2020", "10.01.2020", "11.01.2020", "12.01.2020", "20.01.2020", "10.03.2020")), CFG)
    assert step(out, "payment")["status"] == "REFER" and "pandemic_period" in codes(step(out, "payment"))
    assert step(out, "notice")["status"] == "INSIDE"  # the notice period (12.01 to 11.02) does not touch it


def test_refer_when_the_presentation_period_runs_into_the_window() -> None:
    out = B.check(req(**chain("01.12.2019", "10.12.2019", "11.12.2019", "12.12.2019", "20.12.2019", "21.12.2019")), CFG)
    assert "pandemic_period" in codes(step(out, "presentation"))  # six months from 01.12.2019 ends 01.06.2020


@pytest.mark.parametrize(
    ("receipt", "touches"),
    [("28.02.2020", False), ("29.02.2020", True), ("31.05.2022", True), ("01.06.2022", False), ("16.05.2022", True)],
)
def test_the_window_edges_are_touching_at_the_ends_and_not_beyond(receipt: str, touches: bool) -> None:
    # fifteen days from the receipt: 28.02.2020 -> 14.03.2020 (before the window); 29.02.2020 -> 15.03.2020 (its first day)
    start = parse_date(receipt).value
    assert start is not None
    year = start.year
    cheque = f"01.01.{year}"
    notice = f"{start.day - 1 if start.day > 1 else 1:02d}.{start.month:02d}.{year}"
    out = B.check(req(**chain(cheque, f"02.01.{year}", f"03.01.{year}", f"04.01.{year}", notice, receipt)), CFG)
    pandemic = {"pandemic_period", "receipt_pandemic_window"} & codes(step(out, "payment"))  # a typed date in it, or the period
    assert bool(pandemic) is touches
    if receipt in ("29.02.2020",):
        assert pandemic == {"pandemic_period"}  # typed outside the window, the fifteen days reach its first day


def test_a_period_wholly_before_or_after_the_window_computes() -> None:
    before = B.check(req(**chain("01.01.2019", "10.01.2019", "11.01.2019", "12.01.2019", "20.01.2019", "21.01.2019")), CFG)
    after = B.check(req(**chain("01.07.2022", "10.07.2022", "11.07.2022", "12.07.2022", "20.07.2022", "21.07.2022")), CFG)
    for out in (before, after):
        assert [step(out, n)["status"] for n in ("presentation", "notice", "payment")][1:] == ["INSIDE", "COMPUTED"]


# ---- B4: year plausibility -------------------------------------------------------------------------------------------------


def problems_of(value: str, **kw: Any) -> list[str]:
    out = B.check(req(notices=[d(value, **kw)]), CFG, today=TODAY)
    return next(i["problems"] for i in out["inputs"] if i["role"] == "notice")


def test_a_year_in_the_future_or_before_the_section_existed_is_refer() -> None:
    this = TODAY.year
    assert "year_not_plausible" in problems_of("20.01.2051")
    assert "year_not_plausible" in problems_of("01.01.1915")  # a typo for 2015: the section did not exist
    assert "year_not_plausible" in problems_of("31.12.1988")
    assert "year_not_plausible" not in problems_of("01.01.1989")  # the floor: s.138 came in with effect from 1 April 1989
    assert "year_not_plausible" in problems_of(f"01.01.{this + 2}")
    assert "year_not_plausible" not in problems_of(f"01.01.{this + 1}")  # next year is allowed


def test_the_floor_is_a_config_value_a_lawyer_may_lower() -> None:
    low = S138ChecklistConfig(min_year=1881)
    out = B.check(req(notices=[d("01.01.1915")]), low)
    assert "year_not_plausible" not in out["inputs"][0]["problems"]
    assert parse_s138_checklist_config({"wf_b3_s138_checklist": True, "s138_checklist_min_year": 1881}) == low
    for bad in (1880, 2101, "1989", True, 1989.5):
        assert parse_s138_checklist_config({"wf_b3_s138_checklist": True, "s138_checklist_min_year": bad}) is None
    assert S138ChecklistConfig().min_year == 1989


def test_the_year_string_says_when_the_section_came_in() -> None:
    out = B.check(req(notices=[d("01.01.1915")]), CFG)
    text = json.dumps(out["steps"])
    assert "Notice '01.01.1915': The year looks wrong: it is before 1989" in text
    assert "before 1989 (s.138 was inserted into the Act with effect from 1 April 1989)" in text and "Check it" in text
    low = json.dumps(B.check(req(notices=[d("20.01.2051")]), S138ChecklistConfig(min_year=1950))["steps"])
    assert "before 1950 or after" in low and "inserted into the Act" not in low


def test_a_two_digit_year_resolved_outside_the_range_is_refer() -> None:
    out = run(notices=[d("20.01.98", century=2000)])  # the other dates say 2013: 98 -> 2098
    assert "year_not_plausible" in next(i["problems"] for i in out["inputs"] if i["role"] == "notice")
    assert step(out, "notice")["status"] == "REFER"
    out2 = run(notices=[d("20.01.51", as_="2051-01-20")])
    assert "year_not_plausible" in next(i["problems"] for i in out2["inputs"] if i["role"] == "notice")


# ---- B5: confirmation must match the reading ---------------------------------------------------------------------------------


def test_an_unconfirmed_date_comes_back_with_the_reading_to_confirm() -> None:
    out = B.check(req(notices=[d("03/04/2015", confirmed=False)]), CFG)
    i = out["inputs"][0]
    assert i["read_as"] == "2015-04-03" and i["day_first_assumed"] is True
    text = json.dumps(out["steps"])
    assert "read as 2015-04-03" in text and "confirmed_as" in text


def test_a_confirmation_without_the_reading_is_refer() -> None:
    p = problems_of("03/04/2015", as_=None)
    assert "confirmation_without_reading" in p


def test_a_confirmation_of_a_different_reading_is_refer_the_month_first_case() -> None:
    p = problems_of("03/04/2015", as_="2015-03-04")  # the person meant 4 March; the server read 3 April
    assert "confirmation_does_not_match" in p


def test_a_confirmation_of_the_exact_reading_is_accepted_even_when_ambiguous() -> None:
    assert problems_of("03/04/2015", as_="2015-04-03") == []


def test_the_confirmed_reading_must_be_exact_not_nearby_or_padded() -> None:
    assert "confirmation_does_not_match" in problems_of("20.03.2013", as_="2013-03-21")
    assert problems_of("20.03.2013", as_=" 2013-03-20 ") == []  # surrounding spaces are not a different reading


def test_confirmed_false_with_a_matching_reading_is_still_not_confirmed() -> None:
    out = B.check(req(notices=[{"value": "20.03.2013", "source": "x", "confirmed": False, "confirmed_as": "2013-03-20"}]), CFG)
    assert "not_confirmed" in out["inputs"][0]["problems"]


# ---- B6: ISO dates --------------------------------------------------------------------------------------------------------


def test_iso_dates_are_read_and_are_not_ambiguous() -> None:
    p = parse_date("2015-01-20")
    assert p.value == date(2015, 1, 20) and p.issue is None and p.day_first_assumed is False
    assert parse_date("2015-04-03").value == date(2015, 4, 3) and parse_date("2015-04-03").day_first_assumed is False
    assert parse_date("2015-13-40").issue == "unparsed" and parse_date("2015-1-2").issue == "unparsed"
    assert problems_of("2013-03-20") == []


# ---- R2's three missing tests (a)-(c) ------------------------------------------------------------------------------------


def test_refer_a_saturday_last_day_for_the_presentation_period() -> None:
    """(a) cheque 01.03.2013 (Fri), validity 3 months: the A end is 01.06.2013, a Saturday; presented 03.06.2013, after it."""
    dates = ("01.03.2013", "03.06.2013", "04.06.2013", "05.06.2013", "10.06.2013", "11.06.2013")
    out = B.check(req(**chain(*dates, validity=3)), CFG)
    s = step(out, "presentation")
    assert s["status"] == "REFER" and "last_day_is_a_weekend" in codes(s)
    assert s["counts"]["A"]["period_ends"] == "2013-06-01"


def test_a_sunday_last_day_is_also_a_weekend() -> None:
    dates = ("02.03.2013", "04.06.2013", "05.06.2013", "06.06.2013", "10.06.2013", "11.06.2013")
    out = B.check(req(**chain(*dates, validity=3)), CFG)
    s = step(out, "presentation")  # A ends Sun 02.06.2013 (weekday() == 6)
    assert s["counts"]["A"]["period_ends"] == "2013-06-02" and "last_day_is_a_weekend" in codes(s)


def test_presentation_on_the_last_day_is_refer_because_the_conventions_differ() -> None:
    """(b) cheque 01.02.2013, validity 3 months, presented 01.05.2013 = the A end: inside (A), outside (B)."""
    out = run(validity_months=3, presentations=[d("01.05.2013")], **later("01.05.2013"))
    s = step(out, "presentation")
    assert s["status"] == "REFER" and "conventions_differ" in codes(s)
    assert s["counts"]["A"]["status"] == "inside" and s["counts"]["B"]["status"] == "outside"
    assert s["counts"]["B"]["period_ends"] == "2013-04-30"


def test_dates_on_the_same_day_are_inside_not_before_the_start() -> None:
    """(c) a presentation on the cheque date and a notice on the information date are inside, not 'before the start'."""
    dates = ("01.02.2013", "01.02.2013", "04.02.2013", "05.02.2013", "05.02.2013", "06.02.2013")
    same = B.check(req(**chain(*dates, validity=3)), CFG)
    assert step(same, "presentation")["status"] == "INSIDE" and step(same, "notice")["status"] == "INSIDE"
    assert "date_before_start" not in codes(step(same, "presentation")) | codes(step(same, "notice"))


def test_the_year_ceiling_follows_the_injected_clock() -> None:
    nxt = f"01.01.{TODAY.year + 1}"
    assert "year_not_plausible" not in B.check(req(notices=[d(nxt)]), CFG, today=TODAY)["inputs"][0]["problems"]
    assert "year_not_plausible" in B.check(req(notices=[d(nxt)]), CFG, today=date(2024, 6, 1))["inputs"][0]["problems"]
    real = B.check(req(notices=[d("01.01.2013")]), CFG)  # no clock given: the real date is used, a past year is fine
    assert "year_not_plausible" not in real["inputs"][0]["problems"]


def test_a_sunday_only_second_count_is_a_weekend_too() -> None:
    """R2's discriminating case: cheque 03.03.2013, validity 3 months: A ends Monday 03.06.2013, B ends SUNDAY 02.06.2013
    (A is not a weekend day, so only B's Sunday can raise the rule); presented 04.06.2013, outside both."""
    dates = ("03.03.2013", "04.06.2013", "05.06.2013", "06.06.2013", "10.06.2013", "11.06.2013")
    s = step(B.check(req(**chain(*dates, validity=3)), CFG, today=TODAY), "presentation")
    assert s["counts"]["A"]["period_ends"] == "2013-06-03" and s["counts"]["B"]["period_ends"] == "2013-06-02"
    assert s["status"] == "REFER" and "last_day_is_a_weekend" in codes(s)


# ---- the plain line that says why a pandemic-era date answers REFER (Track B's quality pass, 17 Oct) ----------------------------------


def test_a_pandemic_era_date_carries_the_one_plain_line_that_says_why() -> None:
    out = run(cheque_date=d("01.06.2021"), presentations=[d("10.06.2021")], **later("10.06.2021"))
    assert out["pandemic_note"] == B.PANDEMIC_NOTE and "15 March 2020 to 31 May 2022" in out["pandemic_note"]
    assert any("pandemic_period" in codes(s) or s["status"] == "REFER" for s in out["steps"])
    assert not any(w in out["pandemic_note"].lower() for w in ("valid", "in time", "late", "barred", "expired"))


def test_a_period_that_only_touches_the_window_also_carries_the_line() -> None:
    out = run(cheque_date=d("01.01.2020"), presentations=[d("20.02.2020")], return_memos=[d("21.02.2020")], information_received=[d("21.02.2020")], notices=[d("25.02.2020")], notice_receipts=[d("26.02.2020")])
    assert any("pandemic_period" in codes(s) for s in out["steps"]) and out["pandemic_note"] == B.PANDEMIC_NOTE


def test_dates_clear_of_the_window_carry_no_pandemic_line() -> None:
    assert run()["pandemic_note"] is None  # the shipped fixture dates are 2013
