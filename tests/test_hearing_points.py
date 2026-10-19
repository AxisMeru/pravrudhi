# ruff: noqa: E501
"""A9 hearing points (hearing_points.py): three lists from entered facts (two sides) and optional hearing-pack lines; checked, never edited. Fake Completer, invented text."""

from __future__ import annotations

import json
from types import SimpleNamespace as NS

import pytest

from pravrudhi.application import hearing_points as hp
from pravrudhi.application.bail_draft import Completion

CFG = NS(
    model="claude-sonnet-5-5",
    usd_per_mtok_in=2.0,
    usd_per_mtok_out=10.0,
    max_tokens_per_call=3000,
    max_calls_per_matter=2,
    max_input_tokens_per_call=8000,
    usd_per_matter=0.60,
)


def facts(extra=None):
    base = [
        NS(fact_id="F1", text="The applicant has been in custody since 05.02.2025.", side="applicant"),
        NS(fact_id="F2", text="The charge sheet has been filed and the investigation is complete.", side="applicant"),
        NS(fact_id="F3", text="The applicant has a fixed residence at [address].", side="applicant"),
        NS(
            fact_id="F4",
            text="The learned Public Prosecutor submitted that the applicant was earlier convicted in 2019.",
            side="other",
        ),
    ]
    return [*base, *(extra or [])]


def case(fs=None, n_excl=0):
    return NS(
        matter_id="M-1",
        facts=fs if fs is not None else facts(),
        n_facts_excluded=n_excl,
        parties={},
        grounds=[],
        hearing_date=None,
    )


class Fake:
    def __init__(self, *texts, stop="end_turn", model="claude-sonnet-5-5"):
        self.texts, self.calls, self.stop, self.model = list(texts), [], stop, model

    def __call__(self, system, user, max_tokens):
        self.calls.append((system, user, max_tokens))
        return Completion(self.texts[min(len(self.calls), len(self.texts)) - 1], 1200, 700, self.model, self.stop)


def ans(points=(), other=(), questions=(), notes=()):
    f = lambda xs: [{"text": t, "facts": fs} for t, fs in xs]  # noqa: E731
    return json.dumps(
        {
            "points_to_make": f(points),
            "other_side_may_raise": f(other),
            "questions": f(questions),
            "notes_for_lawyer": list(notes),
        }
    )


GOOD = ans(
    points=[("The applicant has been in custody since 05.02.2025 and the charge sheet has been filed [F1][F2].", ["F1", "F2"])],
    other=[("The papers show that the applicant was earlier convicted in 2019, which the other side may rely on [F4].", ["F4"])],
    questions=[("How long has the applicant been in custody, and since which date?", ["F1"])],
)


def go(c=None, complete=None, pack=None):
    return hp.draft(c or case(), pack, complete, CFG, "points-abc")


def test_happy_path_three_lists_one_call_counted_cost():
    f = Fake(GOOD)
    r = go(complete=f)
    assert (r["state"], r["calls"], r["regenerated"]) == ("drafted", 1, False) and r["cost_usd"] == 0.0094
    assert (
        len(r["points_to_make"]["items"]) == 1
        and len(r["other_side_may_raise"]["items"]) == 1
        and len(r["questions"]["items"]) == 1
    )
    assert (
        r["points_to_make"]["heading"] == hp.POINTS_HEADING
        and r["other_side_may_raise"]["heading"] == hp.OTHER_HEADING
        and r["questions"]["heading"] == hp.QUESTIONS_HEADING
    )
    u = f.calls[0][1]
    assert "FACTS, client's side:" in u and "[F4] The learned Public Prosecutor" in u


def test_gap_prompts_are_the_closed_labels_no_fact_covers_and_fixed_engine_text():
    r = go(complete=Fake(GOOD))
    labels = {g["ground"] for g in r["questions"]["gap_prompts"]}
    assert "tampering" in labels and "co_accused" in labels and "custody" not in labels and "stage" not in labels
    assert all(g["text"] == hp.GAP_PROMPTS[g["ground"]] for g in r["questions"]["gap_prompts"])


def test_no_model_or_no_client_fact_gives_facts_and_prompts_only():
    r = go(complete=None)
    assert (r["state"], r["calls"], r["fallback_line"]) == ("prompts_only", 0, hp.FALLBACK_LINE) and r["questions"]["gap_prompts"]
    r2 = go(case([NS(fact_id="F1", text="The State alleges an offence.", side="other")]), complete=Fake(GOOD))
    assert r2["state"] == "prompts_only" and r2["calls"] == 0


@pytest.mark.parametrize(
    ("kind", "item", "code"),
    [
        ("points_to_make", ("The applicant has been in custody since 05.02.2025 [F1].", ["F4"]), "wrong_side"),
        ("points_to_make", ("The applicant has been in custody since 06.02.2025 [F1].", ["F1"]), "number_not_in_facts"),
        ("points_to_make", ("The applicant is entitled to bail on the custody since 05.02.2025 [F1].", ["F1"]), "conclusory"),
        ("points_to_make", ("The applicant states that the bank held the custody since 05.02.2025 [F1].", ["F1"]), "new_noun"),
        (
            "other_side_may_raise",
            ("The papers show a risk of flight, which the other side may rely on [F1].", ["F1"]),
            "no_other_side_source",
        ),
        ("other_side_may_raise", ("The applicant was earlier convicted in 2019 [F4].", ["F4"]), "frame"),
        ("questions", ("The applicant has been in custody since 05.02.2025?", ["F1"]), "not_a_question"),
        ("questions", ("How long has the applicant been in custody", ["F1"]), "not_a_question"),
        ("questions", ("What is the date of the Supreme Court judgment on bail in custody?", ["F1"]), "legal_reference"),
    ],
)
def test_each_item_check_fails_on_its_own_mutant(kind, item, code):
    c = case()
    pcase = NS(matter_id="M-1", kind="regular", facts=c.facts, authorities=[], parties={}, n_facts_excluded=0)
    own = {"F1", "F2", "F3"}
    why = hp.check_item(kind, {"text": item[0], "facts": item[1]}, pcase, own, {"F4"}, set(), set(), 0)
    assert any(code in w for w in why), (code, why)


def test_a_failing_item_is_regenerated_once_then_dropped_to_the_notes_never_edited():
    bad = ans(points=[("The applicant has been in custody since 06.02.2025 [F1].", ["F1"])], other=[], questions=[])
    r = go(complete=Fake(bad, bad))
    assert r["calls"] == 2 and r["state"] == "prompts_only" and r["points_to_make"]["items"] == []
    assert any("points to make item was not used" in n for n in r["notes_for_lawyer"] + r["more_notes"])
    r2 = go(complete=Fake(bad, GOOD))
    assert (r2["calls"], r2["regenerated"], r2["state"]) == (2, True, "drafted")


def test_adverse_sounding_client_fact_is_flagged_and_counts_as_an_other_side_source():
    fs = facts([NS(fact_id="F5", text="The applicant was earlier convicted in 2019 in a cheating case.", side="applicant")])
    g = ans(
        other=[
            (
                "The papers show that the applicant was earlier convicted in 2019 in a cheating case, which the other side may rely on [F5].",
                ["F5"],
            )
        ]
    )
    r = go(case(fs), Fake(g))
    assert r["other_side_may_raise"]["items"] and any(c["name"] == "adverse_voice" for c in r["checks"])


def test_pack_lines_enter_as_numbered_lines_and_can_ground_an_other_side_point():
    pack = {
        "next_dates": {"rows": [{"date_as_written": "5.9.2026", "source": "note-A p.1"}]},
        "citations": {"needs_attention": [{"text": "(2099) 99 SCC 999"}], "not_checked": []},
    }
    assert hp.pack_lines(pack)[0].startswith("The papers give 5.9.2026") and "(2099) 99 SCC 999" in hp.pack_lines(pack)[1]
    f = Fake(
        ans(
            other=[
                (
                    "The papers show that the papers cite (2099) 99 SCC 999, which the index did not find, which the other side may rely on [F6].",
                    ["F6"],
                )
            ]
        )
    )
    r = go(complete=f, pack=pack)
    assert "[F5] The papers give 5.9.2026" in f.calls[0][1] and "[F6] The papers cite (2099) 99 SCC 999" in f.calls[0][1]
    assert r["other_side_may_raise"]["items"]


def test_sensitive_words_in_a_fact_or_a_line_refuse_before_any_call():
    f = Fake(GOOD)
    r = go(case([NS(fact_id="F1", text="The applicant is charged with rape.", side="applicant")]), f)
    assert (r["state"], r["refused"], r["calls"]) == ("refused", hp.bd.SENSITIVE_REFUSAL, 0) and f.calls == []


def test_a_sensitive_section_in_a_fact_refuses_when_the_table_is_present(monkeypatch):
    monkeypatch.setattr(hp.bd, "_sensitive_checker", lambda: lambda ms: any("370" in m for m in ms))
    r = go(case([NS(fact_id="F1", text="Charged under Section 370 of the Indian Penal Code.", side="applicant")]), Fake(GOOD))
    assert r["state"] == "refused" and any(c["name"] == "sensitive_section_backstop" for c in r["checks"])


def test_wrong_model_is_loud_cap_refuses_and_max_tokens_gives_prompts_only():
    with pytest.raises(AssertionError):
        go(complete=Fake(GOOD, model="other"))
    r = hp.draft(case(), None, Fake(GOOD), NS(**{**CFG.__dict__, "max_input_tokens_per_call": 50}), "x")
    assert r["state"] == "refused" and "token cap" in r["refused"]
    r2 = go(complete=Fake(GOOD, stop="max_tokens"))
    assert r2["state"] == "prompts_only" and r2["incomplete"] is True and r2["fallback_line"] == hp.bd.INCOMPLETE_LINE


def test_no_other_side_point_gives_the_none_line_and_notes_are_capped_at_five():
    g = ans(
        points=[("The applicant has a fixed residence at [address] [F3].", ["F3"])],
        notes=[f"Please confirm detail {i}." for i in range(1, 9)],
    )
    r = go(complete=Fake(g))
    assert r["other_side_may_raise"]["none_line"] == hp.NO_OTHER_LINE and len(r["notes_for_lawyer"]) == 5 and r["more_notes"]


def test_the_module_calls_no_network_code():
    import inspect

    src = inspect.getsource(hp)
    for bad in ("anthropic", "urllib", "requests", "httpx", "socket", "subprocess"):
        assert bad not in src
