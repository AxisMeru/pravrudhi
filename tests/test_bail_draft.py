# ruff: noqa: E501
"""A4 bail drafting (application/bail_draft.py): every refusal, check and fallback, on a fake Completer. No network, no key, no model."""

import json
from dataclasses import dataclass, field
from types import SimpleNamespace as NS

import pytest

from pravrudhi.application import bail_draft as bd

CFG = NS(
    model="claude-sonnet-5-5",
    usd_per_mtok_in=2.0,
    usd_per_mtok_out=10.0,
    max_tokens_per_call=2500,
    max_calls_per_matter=2,
    max_input_tokens_per_call=6000,
    usd_per_matter=0.60,
)
FRAMING = NS(
    status="single",
    lines=["Framed under Section 437 of the Code of Criminal Procedure, 1973: all the dates you gave are before 1 July 2024."],
)
STRADDLE = NS(status="straddle", lines=["Dates straddle 1 July 2024: Section 437 CrPC / Section 480 BNSS (a lawyer decides)."])


@dataclass
class Case:
    matter_id: str = "M-1"
    kind: str = "regular"
    facts: list = field(
        default_factory=lambda: [
            NS(fact_id="F1", text="The applicant was arrested on 4 May 2023 in FIR [FIR number] registered at [police station]."),
            NS(fact_id="F2", text="The applicant has been in custody since 4 May 2023."),
            NS(fact_id="F3", text="The charge sheet has been filed and the investigation is complete."),
        ]
    )
    n_facts_excluded: int = 0
    authorities: list = field(default_factory=list)
    parties: dict = field(default_factory=dict)


def answer(paras, notes=None):
    return json.dumps({"grounds": [{"text": t, "facts": f} for t, f in paras], "notes_for_lawyer": notes or []})


GOOD = [
    ("The applicant states that he was arrested in FIR [FIR number] registered at [police station] [F1].", ["F1"]),
    (
        "The applicant states that he has been in custody since 4 May 2023 [F2] and that the charge sheet has been filed [F3].",
        ["F2", "F3"],
    ),
]


class Fake:
    def __init__(self, *texts, stop="end_turn", model="claude-sonnet-5-5", tin=900, tout=700):
        self.texts, self.calls, self.stop, self.model, self.tin, self.tout = list(texts), [], stop, model, tin, tout

    def __call__(self, system, user, max_tokens):
        self.calls.append((system, user, max_tokens))
        return bd.Completion(self.texts[min(len(self.calls), len(self.texts)) - 1], self.tin, self.tout, self.model, self.stop)


def run(case=None, complete=None, framing=FRAMING, cfg=CFG):
    return bd.draft(case or Case(), framing, complete, cfg, "bail-draft-abc")


def test_happy_path_one_call_engine_sections_and_counted_cost():
    f = Fake(answer(GOOD, ["Confirm the FIR date."]))
    r = run(complete=f)
    assert (r["state"], r["calls"], r["regenerated"], r["refused"]) == ("drafted", 1, False, None)
    assert r["cost_usd"] == bd.cost_usd(900, 700, CFG) == 0.0088
    ids = [s["id"] for s in r["sections"]]
    assert ids[:3] == ["heading", "facts", "grounds"] and ids[-2:] == ["prayer", "authorities"] and "notes" in ids
    assert r["sections"][0]["text"].startswith(bd.BANNER) and FRAMING.lines[0] in r["sections"][0]["text"]
    assert "Confirm the FIR date." in r["text"] and f.calls[0][2] == 2500 and "thinking" not in f.calls[0][0].lower()
    assert r["fill_ins"] == []  # both placeholders are repeated in a paragraph that cites fact F1


def test_pack_has_only_the_given_facts_and_no_statute_name():
    f = Fake(answer(GOOD))
    run(complete=f)
    user = f.calls[0][1]
    assert "[F1] The applicant was arrested" in user and "none: cite no authority" in user
    for banned in ("CrPC", "BNSS", "Section 437", "Section 480", "Criminal Procedure"):
        assert banned not in user and banned not in f.calls[0][0]


def test_straddle_framing_lines_are_printed_verbatim():
    r = run(complete=Fake(answer(GOOD)), framing=STRADDLE)
    assert r["framing_status"] == "straddle" and STRADDLE.lines[0] in r["sections"][0]["text"]


@pytest.mark.parametrize(
    "fact",
    [
        "The applicant is charged with rape.",
        "Offence under the POCSO Act.",
        "The victim is a minor girl.",
        "Charged with outraging the modesty of a woman.",
    ],
)
def test_sensitive_backstop_refuses_before_any_call(fact):
    c = Case(facts=[NS(fact_id="F1", text=fact)])
    f = Fake(answer(GOOD))
    r = run(c, f)
    assert (r["state"], r["refused"], r["calls"], r["sections"]) == ("refused", bd.SENSITIVE_REFUSAL, 0, []) and f.calls == []


def test_ordinary_child_in_a_bail_ground_is_not_refused():
    c = Case(facts=[NS(fact_id="F1", text="The applicant has a minor child at home.")])
    assert run(c, Fake(answer([("The applicant states that he has a minor child at home [F1].", ["F1"])])))["state"] == "drafted"


def test_no_completer_or_no_facts_gives_the_template_only():
    r = run(complete=None)
    assert (r["state"], r["calls"], r["grounds"]) == ("template_only", 0, [])
    assert bd.TEMPLATE_ONLY_LINE in r["text"] and "[F1]" in r["text"]
    assert [x for x in r["fill_ins"] if "[FIR number]" in x]  # no paragraph repeats the placeholder, so the lawyer is told
    assert run(Case(facts=[]), Fake(answer(GOOD)))["state"] == "template_only"


def test_unknown_kind_is_an_error():
    with pytest.raises(ValueError):
        run(Case(kind="odd"), Fake(answer(GOOD)))


def test_pack_over_the_input_cap_or_matter_cap_is_refused_before_any_call():
    big = Case(facts=[NS(fact_id="F1", text="x " * 20000)])
    f = Fake(answer(GOOD))
    r = run(big, f)
    assert r["state"] == "refused" and "token cap" in r["refused"] and f.calls == []
    r = run(complete=f, cfg=NS(**{**CFG.__dict__, "usd_per_matter": 0.001}))
    assert r["state"] == "refused" and "cap" in r["refused"] and f.calls == []
    assert run(complete=f, cfg=NS(**{**CFG.__dict__, "max_calls_per_matter": 0}))["refused"]


def test_wrong_response_model_is_loud():
    with pytest.raises(AssertionError):
        run(complete=Fake(answer(GOOD), model="claude-other"))


# ---- D1, D2 and the other paragraph checks: each fails on its own mutant --------------------------------------------------


def bad(text, facts=("F1",)):
    return [(text, list(facts))]


@pytest.mark.parametrize(
    ("text", "facts", "code"),
    [
        ("The applicant states that he was arrested in FIR [FIR no.] [F1].", ["F1"], "d1_token_invented"),
        ("No authority is supplied for the applicant's custody [F2].", ["F2"], "d2_meta"),
        ("This draft relies on the custody since 4 May 2023 [F2].", ["F2"], "d2_meta"),
        ("The applicant is entitled to bail on the custody since 4 May 2023 [F2].", ["F2"], "conclusory"),
        ("The applicant will be granted bail after custody since 4 May 2023 [F2].", ["F2"], "forbidden_words"),
        ("The applicant was arrested and held at a bank facility [F1].", ["F1"], "new_noun"),
        ("The applicant states he is in custody [F9].", ["F9"], "fact_ref_unknown"),
        ("The applicant states he is in custody.", [], "no_fact"),
        ("The applicant relies on (2014) 8 SCC 273 [F2].", ["F2"], "authority_not_supplied"),
    ],
)
def test_each_paragraph_check_fails_on_its_own_mutant(text, facts, code):
    paras = [GOOD[1], (text, facts)]
    f = Fake(answer(paras), answer(paras))
    r = run(complete=f)
    first = r["checks"][0]
    assert first["passed"] is False and any(code in x for fl in first["failed"] for x in fl["reasons"]), first


def test_blanket_opener_only_first_paragraph_may_open_with_it_is_submitted():
    ok = [("It is submitted that the applicant has been in custody since 4 May 2023 [F2].", ["F2"]), GOOD[0]]
    assert run(complete=Fake(answer(ok)))["checks"][0]["passed"] is True
    two = [ok[0], ("It is submitted that the charge sheet has been filed [F3].", ["F3"])]
    r = run(complete=Fake(answer(two), answer(two)))
    assert r["checks"][0]["failed"][0]["paragraph"] == 2


def test_new_noun_passes_when_the_fact_has_the_word():
    c = Case(facts=[NS(fact_id="F1", text="The applicant's wife is ill and the bank has frozen the account.")])
    ok = answer([("The applicant states that his wife is ill and the bank has frozen the account [F1].", ["F1"])])
    assert run(c, Fake(ok))["state"] == "drafted"


def test_a_supplied_authority_passes():
    c = Case(authorities=["(2014) 8 SCC 273"])
    ok = answer([("The applicant relies on (2014) 8 SCC 273 on custody since 4 May 2023 [F2].", ["F2"]), GOOD[0]])
    r = run(c, Fake(ok))
    assert r["state"] == "drafted" and r["unresolved_authorities"] == []


# ---- regeneration, drop to notes, fallback -------------------------------------------------------------------------------


def test_failing_paragraph_is_regenerated_once_and_the_feedback_names_it():
    first = [GOOD[0], ("No authority is supplied [F2].", ["F2"])]
    f = Fake(answer(first), answer(GOOD))
    r = run(complete=f)
    assert (r["state"], r["calls"], r["regenerated"]) == ("drafted", 2, True)
    assert "paragraph 2: d2_meta" in f.calls[1][1] and f.calls[1][1].startswith(f.calls[0][1])
    assert r["cost_usd"] == bd.cost_usd(1800, 1400, CFG) and len(r["grounds"]) == 2


def test_still_failing_after_the_regeneration_is_dropped_to_the_notes_never_edited():
    first = [GOOD[0], ("No authority is supplied [F2].", ["F2"])]
    r = run(complete=Fake(answer(first), answer(first)))
    assert r["state"] == "drafted" and r["calls"] == 2 and len(r["grounds"]) == 1
    assert any(
        "A ground was not used (paragraph 2" in n and "talked about what was or was not given" in n for n in r["notes_for_lawyer"]
    )
    assert "No authority is supplied" not in r["grounds"][0]["text"]
    assert all(p["text"] == GOOD[0][0] for p in r["grounds"])  # the kept paragraph is the model's exact words


def test_all_paragraphs_failing_twice_is_template_only():
    bad_all = [("The applicant is entitled to bail [F2].", ["F2"])]
    r = run(complete=Fake(answer(bad_all), answer(bad_all)))
    assert (r["state"], r["grounds"]) == ("template_only", []) and bd.TEMPLATE_ONLY_LINE in r["text"]


def test_not_json_is_regenerated_once_then_template_only():
    r = run(complete=Fake("Here is your draft: ...", "still not json"))
    assert (r["state"], r["calls"]) == ("template_only", 2)
    assert [c["name"] for c in r["checks"]].count("answer_is_json") == 2
    r = run(complete=Fake("```json\n" + answer(GOOD) + "\n```"))
    assert r["state"] == "drafted"  # a code fence is tolerated, nothing else is repaired


def test_max_tokens_marks_incomplete_with_no_regeneration_and_no_grounds():
    f = Fake(answer(GOOD), stop="max_tokens")
    r = run(complete=f)
    assert (r["state"], r["incomplete"], r["calls"], r["grounds"]) == ("template_only", True, 1, []) and bd.INCOMPLETE_LINE in r[
        "text"
    ]


def test_regeneration_is_skipped_when_a_second_call_would_pass_the_matter_cap():
    tight = NS(**{**CFG.__dict__, "usd_per_matter": 0.06})
    first = [GOOD[0], ("No authority is supplied [F2].", ["F2"])]
    f = Fake(answer(first), answer(GOOD), tin=2000, tout=4000)
    r = run(complete=f, cfg=tight)
    assert r["calls"] == 1 and any(c["name"] == "regeneration_skipped" for c in r["checks"]) and len(r["grounds"]) == 1


def test_one_call_cap_means_no_regeneration():
    one = NS(**{**CFG.__dict__, "max_calls_per_matter": 1})
    first = [GOOD[0], ("No authority is supplied [F2].", ["F2"])]
    r = run(complete=Fake(answer(first), answer(GOOD)), cfg=one)
    assert r["calls"] == 1 and len(r["grounds"]) == 1


# ---- D1 as a fill-ins list, gaps, exclusions, advisory words -------------------------------------------------------------


def test_placeholder_not_repeated_goes_to_fill_ins():
    paras = [
        ("The applicant states that he was arrested on 4 May 2023 [F1] and has been in custody since then [F2].", ["F1", "F2"]),
        GOOD[1],
    ]
    r = run(complete=Fake(answer(paras)))
    assert (
        r["state"] == "drafted"
        and len(r["fill_ins"]) == 2
        and "[FIR number]" in r["fill_ins"][0]
        and "[police station]" in r["fill_ins"][1]
    )
    assert bd.FILL_INS_HEADING in r["text"] and bd.FILL_INS_HEADING == "Details to fill in"


def test_uncited_fact_exclusions_and_advisory_words_are_in_the_notes():
    c = Case(n_facts_excluded=2)
    r = run(
        c,
        Fake(
            answer(
                [
                    GOOD[0],
                    (
                        "The applicant states that the investigation was concluded [F3].",
                        ["F3"],
                    ),
                ]
            )
        ),
    )
    notes = " ".join(r["notes_for_lawyer"])
    assert (
        "Fact F2 was given but the grounds do not cite it." in notes
        and "2 fact(s) were not marked as the applicant's side" in notes
    )
    assert "concluded" in notes  # advisory: a long word in no fact; it does not fail the paragraph


def test_template_only_never_calls_and_costs_nothing():
    r = run(complete=None)
    assert (r["calls"], r["tokens_in"], r["tokens_out"], r["cost_usd"]) == (0, 0, 0, 0.0)


def test_text_is_the_sections_joined_and_result_keys_are_stable():
    r = run(complete=Fake(answer(GOOD)))
    assert r["text"] == "\n\n".join(s["text"] for s in r["sections"])
    assert {
        "run_id",
        "version",
        "matter_id",
        "kind",
        "state",
        "refused",
        "sections",
        "text",
        "grounds",
        "notes_for_lawyer",
        "fill_ins",
        "checks",
        "unresolved_authorities",
        "framing_status",
        "framing_lines",
        "facts_used",
        "facts_excluded",
        "calls",
        "tokens_in",
        "tokens_out",
        "cost_usd",
        "regenerated",
        "incomplete",
    } <= set(r)
    assert (r["run_id"], r["version"]) == ("bail-draft-abc", bd.VERSION)


@pytest.mark.parametrize("word", ["likely", "ready to file", "clearly", "no doubt", "bound to", "certainly", "made out"])
def test_r1_barred_words_fail_a_paragraph_in_the_body(word):
    paras = [GOOD[0], (f"The applicant states that the custody since 4 May 2023 is {word} relevant [F2].", ["F2"])]
    r = run(complete=Fake(answer(paras), answer(paras)))
    assert any("conclusory" in x for fl in r["checks"][0]["failed"] for x in fl["reasons"]), r["checks"][0]


def test_r1_fixed_strings_are_in_place():
    assert (
        bd.SENSITIVE_REFUSAL.startswith("No draft is made for this kind of case")
        and "A lawyer drafts the application." in bd.SENSITIVE_REFUSAL
    )
    assert bd.TEMPLATE_ONLY_LINE == "The draft grounds could not be made, so this shows your facts and the notes only."
    assert bd.NOTES_HEADING == "Notes for the lawyer"


def test_notes_may_use_outcome_words_they_are_for_the_lawyer_but_the_body_may_not():
    f = Fake(answer(GOOD, ["F1 is an earlier conviction; it is likely to be raised against the Applicant."]))
    r = run(complete=f)
    assert any("likely to be raised" in n for n in r["notes_for_lawyer"] + r["more_notes"])
    assert not any(c["name"] == "notes_outcome_language" for c in r["checks"])


@pytest.mark.parametrize(
    "bad",
    [
        "The Applicant was arrested on 04.05.2023 [F1].",
        "The Applicant has been in custody for 8 months [F2].",
        "The Applicant paid Rs. 5,000 [F2].",
    ],
)
def test_a_number_or_date_not_written_like_that_in_the_facts_fails_the_paragraph(bad):
    paras = [GOOD[0], (bad, ["F1"] if "F1" in bad else ["F2"])]
    r = run(complete=Fake(answer(paras), answer(paras)))
    assert any("number_not_in_facts" in x for fl in r["checks"][0]["failed"] for x in fl["reasons"]), r["checks"][0]


def test_numbers_copied_from_the_facts_pass_even_with_commas_and_slashes():
    c = Case(
        facts=[NS(fact_id="F1", text="The Applicant is alleged to have taken Rs. 5,00,000 on 04/05/2023 under Sections 302/34.")]
    )
    ok = answer([("The Applicant is alleged to have taken Rs. 5,00,000 on 04/05/2023 under Sections 302/34 [F1].", ["F1"])])
    assert run(c, Fake(ok))["state"] == "drafted"


def test_number_check_reject_regenerate_once_then_drop_to_notes():
    first = [GOOD[0], ("The Applicant was arrested on 04.05.2023 [F1].", ["F1"])]
    r = run(complete=Fake(answer(first), answer(first)))
    assert r["calls"] == 2 and len(r["grounds"]) == 1
    assert any("paragraph 2" in n and "number, date or amount" in n for n in r["notes_for_lawyer"] + r["more_notes"])


def test_adverse_looking_facts_are_named_in_a_note_and_an_info_check_never_excluded():
    c = Case(
        facts=[
            NS(fact_id="F1", text="The Applicant is in custody since 05.02.2025."),
            NS(fact_id="F2", text="The learned Public Prosecutor opposed the bail."),
            NS(fact_id="F3", text="The Applicant was earlier convicted in 2019."),
        ]
    )
    paras = [
        ("The Applicant is in custody since 05.02.2025 [F1].", ["F1"]),
        ("The learned Public Prosecutor opposed the bail [F2].", ["F2"]),
        ("The Applicant was earlier convicted in 2019 [F3].", ["F3"]),
    ]
    r = run(c, Fake(answer(paras)))
    assert len(r["grounds"]) == 3  # nothing is excluded: the lawyer ticked them
    notes = r["notes_for_lawyer"]
    assert notes[0].startswith("Fact F2 reads like the court's") and notes[1].startswith("Fact F3 reads like")
    chk = [x for x in r["checks"] if x["name"] == "adverse_voice"][0]
    assert chk["info"] is True and chk["passed"] is True and chk["fact_ids"] == ["F2", "F3"]
    assert not any(x["name"] == "adverse_voice" for x in run(complete=Fake(answer(GOOD)))["checks"])


def test_notes_are_capped_at_five_the_rest_fold_under_more_notes_in_priority_order():
    model_notes = [f"Please confirm detail {i}." for i in range(1, 7)] + [
        "F1 and F2 give different dates; please confirm which is right."
    ]
    r = run(complete=Fake(answer(GOOD, model_notes)))
    assert len(r["notes_for_lawyer"]) == bd.NOTES_SHOWN == 5 and r["more_notes"]
    assert r["notes_for_lawyer"][0].startswith("F1 and F2 give different dates")  # a conflict note outranks the plain ones
    assert [s["id"] for s in r["sections"]].count("more_notes") == 1
    sec = next(s for s in r["sections"] if s["id"] == "more_notes")
    assert sec["text"].startswith(bd.MORE_NOTES_HEADING) and all(f"- {n}" in sec["text"] for n in r["more_notes"])
    assert r["text"] == "\n\n".join(x["text"] for x in r["sections"])


def test_no_more_notes_section_when_five_or_fewer():
    r = run(complete=Fake(answer(GOOD, ["Confirm the FIR date."])))
    assert r["more_notes"] == [] and "more_notes" not in [s["id"] for s in r["sections"]]


def test_dropped_paragraph_and_adverse_notes_outrank_model_notes():
    c = Case(
        facts=[NS(fact_id="F1", text="The Prosecution opposed the bail."), NS(fact_id="F2", text="The Applicant is in custody.")]
    )
    first = [("The Prosecution opposed the bail [F1].", ["F1"]), ("No authority is supplied [F2].", ["F2"])]
    r = run(c, Fake(answer(first, ["a", "b", "c", "d", "e"]), answer(first, ["a", "b", "c", "d", "e"])))
    assert r["notes_for_lawyer"][0].startswith("Fact F1 reads like") and r["notes_for_lawyer"][1].startswith(
        "A ground was not used (paragraph 2"
    )


def test_a_note_predicting_this_applications_result_is_left_out_but_what_the_other_side_may_argue_is_kept():
    f = Fake(
        answer(
            GOOD,
            [
                "The bail is likely to be granted on these facts.",
                "An earlier conviction is likely to be raised against the Applicant.",
            ],
        )
    )
    r = run(complete=f)
    allnotes = r["notes_for_lawyer"] + r["more_notes"]
    assert not any("likely to be granted" in n for n in allnotes) and any("likely to be raised" in n for n in allnotes)
    assert bd.NOTE_LEFT_OUT in allnotes and any(c["name"] == "notes_prediction" for c in r["checks"])


def test_numbers_of_a_supplied_authority_do_not_fail_the_number_check():
    c = Case(authorities=["(2014) 8 SCC 273"])
    ok = answer([("The Applicant relies on (2014) 8 SCC 273 on custody since 4 May 2023 [F2].", ["F2"]), GOOD[0]])
    r = run(c, Fake(ok))
    assert r["state"] == "drafted" and r["checks"][0]["passed"] is True


def test_a_phrase_copied_from_the_lawyers_own_fact_is_not_an_outcome_claim():
    c = Case(facts=[NS(fact_id="F1", text="The trial is likely to take some time and the Applicant is in custody.")])
    ok = answer([("The Applicant states that the trial is likely to take some time [F1].", ["F1"])])
    r = run(c, Fake(ok))
    assert r["state"] == "drafted" and r["checks"][0]["passed"] is True
    bad = answer([("The Applicant states that the bail is likely to be granted [F1].", ["F1"])])
    r2 = run(c, Fake(bad, bad))
    assert any("conclusory" in x for fl in r2["checks"][0]["failed"] for x in fl["reasons"])


def test_a_dropped_ground_is_named_in_the_lawyers_words_with_a_plain_reason():
    c = Case(
        facts=[
            NS(fact_id="F1", text="The trial is likely to take some time."),
            NS(fact_id="F2", text="The Applicant is in custody."),
        ]
    )
    first = [
        ("The Applicant states that the bail is likely to be granted [F1].", ["F1"]),
        ("The Applicant is in custody [F2].", ["F2"]),
    ]
    r = run(c, Fake(answer(first), answer(first)))
    note = next(n for n in r["notes_for_lawyer"] + r["more_notes"] if n.startswith("A ground was not used"))
    assert (
        "F1: The trial is likely to take some time." in note
        and "outcome or strength wording" in note
        and "conclusory" not in note
    )


def test_an_invented_linking_sentence_fails_the_paragraph_but_a_restating_one_passes():
    c = Case(facts=[NS(fact_id="F1", text="The Applicant has been in custody since 03.08.2023 in FIR [FIR number].")])
    bad = answer(
        [
            (
                "The Applicant has been in custody since 03.08.2023 [F1]. He has now approached this Court for relief under the applicable bail provision.",
                ["F1"],
            )
        ]
    )
    r = run(c, Fake(bad, bad))
    assert any("unsupported_sentence" in x for fl in r["checks"][0]["failed"] for x in fl["reasons"])
    ok = answer([("The Applicant has been in custody since 03.08.2023 in FIR [FIR number] [F1].", ["F1"])])
    assert run(c, Fake(ok))["state"] == "drafted"


def test_the_closed_boilerplate_phrases_do_not_count_as_unsupported():
    c = Case(facts=[NS(fact_id="F1", text="The Applicant has been in custody since 03.08.2023.")])
    ok = answer(
        [("The Applicant states that he has been in custody since 03.08.2023 under the applicable bail provision [F1].", ["F1"])]
    )
    assert run(c, Fake(ok))["state"] == "drafted"


def test_a_note_saying_the_bail_provision_is_not_identified_is_left_out_because_the_heading_names_it():
    f = Fake(answer(GOOD, ["The applicable bail provision is not identified; please insert it.", "Confirm the FIR date."]))
    r = run(complete=f)
    allnotes = r["notes_for_lawyer"] + r["more_notes"]
    assert not any("bail provision" in n for n in allnotes) and "Confirm the FIR date." in allnotes
    assert any(c["name"] == "notes_provision_contradiction" for c in r["checks"])


def test_repeated_subject_fails_the_paragraph():
    paras = [GOOD[0], ("The Applicant states that the Applicant has been in custody since 4 May 2023 [F2].", ["F2"])]
    r = run(complete=Fake(answer(paras), answer(paras)))
    assert any("repeated_subject" in x for fl in r["checks"][0]["failed"] for x in fl["reasons"])


def test_the_prayer_is_labelled_to_be_completed_by_counsel():
    r = run(complete=Fake(answer(GOOD)))
    assert "To be completed by counsel" in next(x["text"] for x in r["sections"] if x["id"] == "prayer")


def test_the_word_prosecution_inside_the_lawyers_own_ground_is_not_flagged_as_adverse():
    c = Case(facts=[NS(fact_id="F1", text="There is no likelihood of the Applicant tampering with the prosecution evidence.")])
    r = run(
        c,
        Fake(
            answer(
                [
                    (
                        "The Applicant states that there is no likelihood of his tampering with the prosecution evidence [F1].",
                        ["F1"],
                    )
                ]
            )
        ),
    )
    assert not any(x["name"] == "adverse_voice" for x in r["checks"])
    c2 = Case(facts=[NS(fact_id="F1", text="The prosecution has opposed the bail.")])
    r2 = run(c2, Fake(answer([("The prosecution has opposed the bail [F1].", ["F1"])])))
    assert any(x["name"] == "adverse_voice" for x in r2["checks"])


# ---- sensitive-section backstop on the ticked facts (Track C's table, read lazily) ----------------------------------------------


@pytest.mark.parametrize(
    ("text", "want"),
    [
        (
            "arrested for offences under Sections 370, 370-A and 34 of the Indian Penal Code.",
            [
                "Section 370 of the Indian Penal Code",
                "Section 370-A of the Indian Penal Code",
                "Section 34 of the Indian Penal Code",
            ],
        ),
        ("charged under Section 376(2)(n) of the IPC.", ["Section 376 of the IPC"]),
        ("offences under Sections 8/20 of the NDPS Act.", []),
        ("under Section 138 of the Negotiable Instruments Act, 1881.", []),
        ("under Section 34(2) of the Chhattisgarh Excise Act.", []),
        ("under Section 439 and the facts given.", ["Section 439 and the facts given"]),
        ("an offence under Section 4 of the POCSO Act.", ["Section 4 of the POCSO Act"]),
        ("no section is mentioned here at all.", []),
    ],
)
def test_section_strings_read_the_act_from_the_words_and_skip_other_acts(text, want):
    assert bd.section_strings(text) == want


def _stub_table(sections):
    return any(("370" in x or "375" in x or "pocso" in x.lower()) for x in sections)


def test_a_sensitive_section_named_in_a_ticked_fact_refuses_before_any_call(monkeypatch):
    monkeypatch.setattr(bd, "_sensitive_checker", lambda: _stub_table)
    c = Case(
        facts=[
            NS(
                fact_id="F1",
                text="The Applicant was arrested for offences under Sections 370, 370-A and 34 of the Indian Penal Code.",
            )
        ]
    )
    f = Fake(answer(GOOD))
    r = run(c, f)
    assert (r["state"], r["refused"], r["calls"]) == ("refused", bd.SENSITIVE_REFUSAL, 0) and f.calls == []
    assert any(x["name"] == "sensitive_section_backstop" for x in r["checks"])


def test_a_non_sensitive_section_or_another_act_is_drafted(monkeypatch):
    monkeypatch.setattr(bd, "_sensitive_checker", lambda: _stub_table)
    c = Case(
        facts=[NS(fact_id="F1", text="The Applicant is charged under Sections 8/20 of the NDPS Act and Section 420 of the IPC.")]
    )
    ok = answer([("The Applicant is charged under Sections 8/20 of the NDPS Act and Section 420 of the IPC [F1].", ["F1"])])
    assert run(c, Fake(ok))["state"] == "drafted"


def test_without_the_table_a_named_section_is_referred_not_drafted(monkeypatch, caplog):
    monkeypatch.setattr(bd, "_sensitive_checker", lambda: None)
    c = Case(facts=[NS(fact_id="F1", text="The Applicant was arrested under Section 370 of the Indian Penal Code.")])
    ok = answer([("The Applicant was arrested under Section 370 of the Indian Penal Code [F1].", ["F1"])])
    f = Fake(ok)
    with caplog.at_level("ERROR"):
        r = run(c, f)
    assert (r["state"], r["refused"], r["calls"]) == ("refused", bd.SENSITIVE_REFUSAL, 0) and f.calls == []
    assert any(x["name"] == "sensitive_section_table_missing" and x["passed"] is False for x in r["checks"])
    assert "referred to a lawyer" in caplog.text


def test_a_checker_that_raises_is_a_refusal_like_a_hit(monkeypatch):
    def boom(_sections):
        raise RuntimeError("table unreadable")

    monkeypatch.setattr(bd, "_sensitive_checker", lambda: boom)
    c = Case(facts=[NS(fact_id="F1", text="The Applicant was arrested under Section 420 of the Indian Penal Code.")])
    f = Fake(answer([("The Applicant was arrested [F1].", ["F1"])]))
    r = run(c, f)
    assert (r["state"], r["refused"], r["calls"]) == ("refused", bd.SENSITIVE_REFUSAL, 0) and f.calls == []
    assert any(x["name"] == "sensitive_section_check_error" and x["passed"] is False for x in r["checks"])


def test_no_section_named_needs_no_table(monkeypatch):
    monkeypatch.setattr(bd, "_sensitive_checker", lambda: None)
    c = Case(facts=[NS(fact_id="F1", text="The Applicant has no antecedents.")])
    ok = answer([("The Applicant has no antecedents [F1].", ["F1"])])
    assert run(c, Fake(ok))["state"] == "drafted"


@pytest.mark.parametrize("bad", ["The Applicant is the complainant in this case [F2].", "The complainant is the Applicant [F2]."])
def test_role_flip_fails_the_paragraph(bad):
    c = Case(facts=[NS(fact_id="F2", text="The Applicant and the complainant are neighbours.")])
    paras = [(bad, ["F2"])]
    r = run(c, Fake(answer(paras), answer(paras)))
    assert any("role_flip" in x for fl in r["checks"][0]["failed"] for x in fl["reasons"])


def test_the_real_table_is_found_when_the_route_layer_is_present():
    """Documents the seam: with Track C's bail_draft_input in the tree the checker resolves; without it (this branch alone) it is None."""
    chk = bd._sensitive_checker()
    assert chk is None or callable(chk)


_MUST_REFER = ["sexually assaulted", "15 year old", "juvenile", "taken from her lawful guardian", "sexual abuse of the girl", "below 18 years", "gangrape",
               "trafficked for prostitution", "forced into prostitution", "bonded labour", "sold the girl", "under the age of 18", "unnatural offences",
               "obscene photographs", "child of 12", "kidnapped a minor", "forced himself", "non-consensual intercourse", "eleven-year-old",
               "minor girl aged 14", "The applicant is 17 years old", "the accused was 16 years of age", "the complainant's minor daughter was with her", "the victim's 9-year-old son", "prosecutrix", "FIR alleges rape", "molesting", "outraging modesty", "gang-rape", "victim is a minor", "the girl was 16", "a girl of 17", "aged about 15 years", "she was only 13", "the child was beaten", "indecent act", "brothel",
               "sex worker", "bought a girl", "a schoolgirl", "an infant", "acid attack"]  # fmt: skip
_MUST_NOT = ["minor injuries", "a minority stake in the firm", "my 12-year-old son", "the tenant paid the rent", "a 40-year-old shopkeeper", "the applicant is 40 years old", "aged 52", "the applicant's 12-year-old daughter", "he is 18 years old", "the shop was bought last year", "he was 12 days late", "she was 40", "the child was ill", "the girl of the house"]


def test_the_fact_wording_backstop_refers_every_sensitive_wording_and_none_of_the_near_misses():
    for t in _MUST_REFER:
        assert bd.SENSITIVE_RE.search(f"The applicant states: {t}.") is not None, t
    for t in _MUST_NOT:
        assert bd.SENSITIVE_RE.search(f"The applicant states: {t}.") is None, t
