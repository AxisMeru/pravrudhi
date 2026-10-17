# ruff: noqa: E501
"""A4 bail drafting, deterministic front half: request validation, the referrals (no draft, no model), the statute framing by date. Invented facts only."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from pravrudhi.application import bail_draft_input as I

FACTS = [
    {"text": "The applicant has no antecedents.", "side": "applicant", "grounds": ["antecedents"]},
    {"text": "The informant says he saw the applicant at the shop.", "side": "other"},
    {"text": "The applicant has been in custody since the arrest.", "side": "applicant", "grounds": ["custody_period"]},
]


def req(**kw: Any) -> I.BailDraftRequest:
    base: dict[str, Any] = {
        "consent": True, "kind": "bail", "forum": "trial_court", "offence_date": "2024-08-01", "fir_date": "2024-08-02", "facts": FACTS,
    }
    base.update(kw)
    return I.BailDraftRequest(**base)


def test_only_applicant_side_facts_enter_the_case_and_are_numbered_over_those_alone() -> None:
    c = I.parse_case(req(), 40, matter_id="bail-x")
    assert isinstance(c, I.BailCase)
    assert [(f.fact_id, f.text) for f in c.facts] == [("F1", "The applicant has no antecedents."), ("F2", "The applicant has been in custody since the arrest.")]
    assert c.facts_set_aside == 1 and c.n_facts_excluded == 1 and c.matter_id == "bail-x"
    assert all("informant" not in f.text for f in c.facts)


def test_the_fact_cap_applies_to_ticked_facts_only() -> None:
    many = [{"text": f"The applicant fact {i}.", "side": "applicant"} for i in range(10)] + [{"text": "x", "side": "other"}] * 5
    c = I.parse_case(req(facts=many), 4)
    assert isinstance(c, I.BailCase) and [f.fact_id for f in c.facts] == ["F1", "F2", "F3", "F4"]


@pytest.mark.parametrize("tag", list(I.SENSITIVE_TAGS))
def test_every_sensitive_tag_refers_the_matter_before_anything_else(tag: str) -> None:
    out = I.parse_case(req(tags=[tag], facts=[{"text": "x", "side": "other"}]), 40)  # the tag wins even with no applicant fact
    assert out == I.Referral("sensitive_matter", I.REFER_SENSITIVE)


def test_no_applicant_side_fact_refers_and_a_bail_request_needs_its_forum() -> None:
    assert I.parse_case(req(facts=[{"text": "x", "side": "other"}]), 40) == I.Referral("no_applicant_facts", I.REFER_NO_FACTS)
    assert I.parse_case(req(forum=None), 40) == I.Referral("forum_required", I.REFER_NO_FORUM)
    c = I.parse_case(req(kind="anticipatory_bail", forum=None), 40)  # anticipatory bail needs no forum
    assert isinstance(c, I.BailCase) and c.forum is None and c.drafting_kind == "anticipatory"


@pytest.mark.parametrize(
    ("kind", "forum", "offence", "fir", "section", "act"),
    [
        ("bail", "trial_court", "2024-06-30", "2024-06-30", "437", "CrPC"),
        ("bail", "trial_court", "2024-07-01", "2024-07-02", "480", "BNSS"),
        ("bail", "sessions_or_high_court", "2023-01-01", "2023-01-05", "439", "CrPC"),
        ("bail", "sessions_or_high_court", "2025-01-01", "2025-01-05", "483", "BNSS"),
        ("anticipatory_bail", None, "2020-05-05", "2020-05-06", "438", "CrPC"),
        ("anticipatory_bail", None, "2024-09-09", "2024-09-10", "482", "BNSS"),
    ],
)
def test_the_framing_follows_the_declared_dates_and_the_table(kind: str, forum: str | None, offence: str, fir: str, section: str, act: str) -> None:
    c = I.parse_case(req(kind=kind, forum=forum, offence_date=offence, fir_date=fir), 40)
    assert isinstance(c, I.BailCase)
    fr = I.frame(c)
    assert fr.state == "single" and fr.status == "single" and fr.chosen is not None
    assert (fr.chosen.section, fr.chosen.act_short) == (section, act) and fr.lines == [fr.chosen.line]
    assert f"Section {section}" in fr.chosen.line and fr.confirmed_by_lawyer is False


def test_dates_on_both_sides_of_the_cutoff_show_both_framings_and_choose_none() -> None:
    c = I.parse_case(req(offence_date="2024-06-01", fir_date="2024-08-01"), 40)
    assert isinstance(c, I.BailCase)
    fr = I.frame(c)
    assert fr.state == "straddle" and fr.chosen is None and [o.act_short for o in fr.options] == ["CrPC", "BNSS"] and len(fr.lines) == 2
    assert fr.deciding == {"offence_date": "2024-06-01", "fir_date": "2024-08-01"}


def test_only_the_tables_deciding_dates_decide_and_the_table_is_read_per_call(tmp_path: Any) -> None:
    c = I.parse_case(req(offence_date="2024-06-01", fir_date="2024-06-02", application_date="2025-01-01", arrest_date="2025-01-01"), 40)
    assert isinstance(c, I.BailCase) and I.frame(c).state == "single"  # arrest and application dates are not deciding in the shipped table
    t = I.load_framing()
    t["deciding_dates"] = ["offence_date", "application_date"]
    assert I.frame(c, t).state == "straddle"  # a lawyer's edit of the table changes the answer with no code change
    t2 = dict(t, confirmed_by_lawyer=True)
    assert I.frame(c, t2).confirmed_by_lawyer is True


@pytest.mark.parametrize(
    "patch",
    [
        {"offence_date": "2024-13-01"}, {"offence_date": "01-08-2024"}, {"fir_date": "not a date"}, {"kind": "regular"}, {"consent": "true"}, {"consent": 1},
        {"facts": []}, {"facts": [{"text": "", "side": "applicant"}]}, {"facts": [{"text": "x", "side": "unknown"}]}, {"facts": [{"text": "x" * 1501, "side": "applicant"}]},
        {"facts": [{"text": "x", "side": "applicant", "grounds": ["nonsense"]}]}, {"tags": ["other"]}, {"authorities": ["a"] * 21}, {"authorities": ["x" * 301]},
        {"applicant": "x" * 201}, {"extra": 1}, {"forum": "any_court"},
    ],
)
def test_a_malformed_request_is_a_validation_failure(patch: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        req(**patch)


def test_the_closed_ground_ids_match_the_literal_in_the_request_model() -> None:
    assert set(I.GROUND_IDS) == set(I.FactIn.model_json_schema()["properties"]["grounds"]["items"]["enum"])
