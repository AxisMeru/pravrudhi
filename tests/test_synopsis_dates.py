# ruff: noqa: E501
"""A8 synopsis and list of dates (synopsis_dates.py): the table is the engine's, the model's lines and paragraphs are checked, never edited. Fake Completer, real A3 pages, invented text."""

from __future__ import annotations

import json
from datetime import date
from types import SimpleNamespace as NS
from typing import Any

import pytest

from pravrudhi.application import synopsis_dates as sd
from pravrudhi.application.chronology import DocMeta, SpanRec, build_chronology
from pravrudhi.application.chronology_config import ChronologyConfig

CFG = NS(
    model="claude-sonnet-5-5",
    usd_per_mtok_in=2.0,
    usd_per_mtok_out=10.0,
    max_tokens_per_call=3000,
    max_calls_per_matter=2,
    max_input_tokens_per_call=8000,
    usd_per_matter=0.60,
)
PAPER = "The FIR was registered on 18 July 2021 at the police station. The petitioner was arrested on 12.06.2024 and remanded the next day. The notice was sent on 03/04/2013. The matter is listed on 5.9.2026."


def chron(*docs: tuple[str, str]) -> dict[str, Any]:
    spans, metas = [], []
    for i, (name, body) in enumerate(docs):
        spans.append(SpanRec(f"d{i}", name, 1, 0, len(body), body, f"{i}-0"))
        metas.append(DocMeta(f"d{i}", name, 1, (), True))
    return build_chronology(spans, metas, ChronologyConfig(), date(2026, 10, 17))


class Case:
    def __init__(self, facts=None, n_excl=0):
        self.matter_id = "M-1"
        self.facts = (
            facts
            if facts is not None
            else [
                NS(fact_id="F1", text="The petitioner was arrested on 12.06.2024 in FIR [FIR number]."),
                NS(fact_id="F2", text="The petitioner has been in custody since the arrest."),
            ]
        )
        self.n_facts_excluded = n_excl
        self.authorities = []
        self.parties = {}
        self.document_label = "writ petition"


class Fake:
    def __init__(self, *texts, stop="end_turn", model="claude-sonnet-5-5"):
        self.texts, self.calls, self.stop, self.model = list(texts), [], stop, model

    def __call__(self, system, user, max_tokens):
        self.calls.append((system, user, max_tokens))
        from pravrudhi.application.bail_draft import Completion

        return Completion(self.texts[min(len(self.calls), len(self.texts)) - 1], 1500, 800, self.model, self.stop)


def refs(table):
    return {t["date_as_written"]: t["ref"] for t in table}


def answer(case, c, lines, paras, notes=None):
    return json.dumps(
        {
            "event_lines": [{"ref": r, "text": t} for r, t in lines],
            "synopsis": [{"text": t, "facts": f} for t, f in paras],
            "notes_for_lawyer": notes or [],
        }
    )


def go(case=None, c=None, complete=None, **kw):
    case = case or Case()
    c = c or chron(("note-A", PAPER))
    return sd.draft(case, c, complete, CFG, "synopsis-abc", **kw), c


def good_answer(case=None, c=None):
    case = case or Case()
    c = c or chron(("note-A", PAPER))
    table = sd.table_rows(sd.select_rows(c)[0], len(case.facts) + 1)
    r = refs(table)
    return answer(
        case,
        c,
        [(r["18 July 2021"], "The FIR was registered at the police station.")],
        [
            (
                "The petitioner states that he was arrested in FIR [FIR number] [F1] and has been in custody since then [F2].",
                ["F1", "F2"],
            )
        ],
    ), table


def test_the_table_is_the_engines_date_as_written_paper_page_and_the_papers_own_sentence():
    r, c = go(complete=None)
    rows = r["list_of_dates"]["rows"]
    assert r["state"] == "table_only" and r["synopsis"]["fallback_line"] == sd.TABLE_ONLY_LINE and r["calls"] == 0
    got = {x["date_as_written"]: x for x in rows}
    assert set(got) >= {"18 July 2021", "12.06.2024", "03/04/2013"} and got["18 July 2021"]["source"] == "note-A p.1"
    assert (
        "FIR was registered on 18 July 2021" in got["18 July 2021"]["event_quote"] and got["18 July 2021"]["event_line"] is None
    )
    assert r["list_of_dates"]["columns"] == ["Date", "Event", "Source"] and r["dates_note"] == sd.DATES_NOTE


def test_dates_that_read_two_ways_keep_both_readings_and_sort_after_the_exact_ones():
    r, _ = go(complete=None)
    rows = r["list_of_dates"]["rows"]
    amb = [x for x in rows if x["ambiguous"]]
    assert {x["date_as_written"] for x in amb} == {"12.06.2024", "03/04/2013"} and all(len(x["readings"]) == 2 for x in amb)
    assert (
        not rows[0]["ambiguous"]
        and rows[-1]["ambiguous"] is True
        and [x["ambiguous"] for x in rows] == sorted(x["ambiguous"] for x in rows)
    )


def test_row_refs_continue_after_the_ticked_facts_and_untick_removes_a_row():
    c = chron(("note-A", PAPER))
    rows, total = sd.select_rows(c)
    ids = [x["id"] for x in rows]
    kept, _ = sd.select_rows(c, include_row_ids=ids[:1])
    assert len(kept) == 1 and total >= 3
    t = sd.table_rows(rows, 3)
    assert t[0]["ref"] == "F3" and t[-1]["ref"] == f"F{2 + len(t)}" and len(t) <= len(rows)


def test_happy_path_one_call_the_model_line_sits_above_the_papers_sentence():
    case = Case()
    ans, table = good_answer(case)
    f = Fake(ans)
    r, _ = go(case, complete=f)
    assert (r["state"], r["calls"], r["regenerated"]) == ("drafted", 1, False) and r["cost_usd"] == 0.011
    row = next(x for x in r["list_of_dates"]["rows"] if x["date_as_written"] == "18 July 2021")
    assert row["event_line"] == "The FIR was registered at the police station." and "18 July 2021" in row["event_quote"]
    assert r["synopsis"]["paragraphs"][0]["text"].startswith("The petitioner states that")
    assert "F3" in f.calls[0][1] and "dates as written '18 July 2021'" in f.calls[0][1]


def test_an_event_line_with_a_date_or_number_not_in_the_papers_sentence_falls_back_to_the_sentence():
    case = Case()
    c = chron(("note-A", PAPER))
    table = sd.table_rows(sd.select_rows(c)[0], 3)
    r_ = refs(table)
    bad = answer(
        case,
        c,
        [(r_["18 July 2021"], "The FIR was registered on 19 July 2021.")],
        [("The petitioner states that he was arrested [F1].", ["F1"])],
    )
    r, _ = go(case, c, Fake(bad, bad))
    row = next(x for x in r["list_of_dates"]["rows"] if x["date_as_written"] == "18 July 2021")
    assert row["event_line"] is None and r["calls"] == 2
    assert any("event line" in n for n in r["notes_for_lawyer"] + r["more_notes"])
    assert any(ch["name"].startswith("checks_attempt") and ch["failed_event_lines"] for ch in r["checks"])


def test_an_event_line_that_adds_a_new_noun_or_says_more_than_the_sentence_fails():
    q = "The FIR was registered on 18 July 2021 at the police station."
    assert sd.check_event_line("The FIR was registered at the police station.", q) == []
    assert any(
        "new_noun" in w for w in sd.check_event_line("The FIR was registered by the complainant at the police station.", q)
    )
    assert any(
        "unsupported_sentence" in w
        for w in sd.check_event_line("The investigating officer prepared a detailed report on the incident.", q)
    )
    assert any("event_line_length" in w for w in sd.check_event_line(" ".join(["word"] * 41), q))


@pytest.mark.parametrize(
    "para",
    [
        "It is submitted that the petitioner was arrested [F1].",
        "This synopsis sets out the arrest [F1].",
        "The petitioner states that the petition is frivolous [F1].",
        "The petitioner states that he was arrested on 13.06.2024 [F1].",
    ],
)
def test_synopsis_paragraph_checks_each_fail_on_their_own_mutant(para):
    case = Case()
    ans = answer(case, None, [], [(para, ["F1"])])
    r, _ = go(case, complete=Fake(ans, ans))
    assert r["calls"] == 2 and r["synopsis"]["paragraphs"] == [] and r["state"] == "table_only"
    assert any("A ground was not used" in n for n in r["notes_for_lawyer"] + r["more_notes"])


def test_the_lawyers_own_barred_word_copied_from_a_fact_is_not_blocked():
    case = Case([NS(fact_id="F1", text="The petitioner states that the complaint is false and was lodged in 2021.")])
    c = chron(("note-A", PAPER))
    ans = answer(case, c, [], [("The petitioner states that the complaint is false and was lodged in 2021 [F1].", ["F1"])])
    r, _ = go(case, c, Fake(ans))
    assert r["state"] == "drafted"


def test_a_failed_first_answer_is_regenerated_once_and_the_feedback_names_the_failures():
    case = Case()
    c = chron(("note-A", PAPER))
    bad = answer(
        case,
        c,
        [],
        [
            ("It is submitted that he was arrested [F1].", ["F1"]),
            ("The petitioner states that he has been in custody [F2].", ["F2"]),
        ],
    )
    ok, _t = good_answer(case, c)
    f = Fake(bad, ok)
    r, _ = go(case, c, f)
    assert (r["calls"], r["regenerated"], r["state"]) == (2, True, "drafted") and "paragraph 1" in f.calls[1][1]


def test_the_synopsis_is_capped_in_words_later_paragraphs_go_to_the_notes():
    case = Case(
        [
            NS(fact_id="F1", text="The petitioner was arrested on 12.06.2024."),
            NS(fact_id="F2", text="The petitioner has been in custody."),
        ]
    )
    c = chron(("note-A", PAPER))
    long1 = "The petitioner states that " + " ".join(["he"] * 100) + " was arrested on 12.06.2024 [F1]."
    long2 = "The petitioner states that " + " ".join(["he"] * 100) + " has been in custody [F2]."
    r, _ = go(case, c, Fake(answer(case, c, [], [(long1, ["F1"]), (long2, ["F2"])])))
    assert len(r["synopsis"]["paragraphs"]) == 1 and any(
        "limited to 180 words" in n for n in r["notes_for_lawyer"] + r["more_notes"]
    )


def test_sensitive_words_in_a_fact_or_a_papers_sentence_refuse_before_any_call():
    f = Fake("{}")
    r, _ = go(Case([NS(fact_id="F1", text="The petitioner is charged with rape.")]), complete=f)
    assert (
        (r["state"], r["refused"], r["calls"]) == ("refused", sd.bd.SENSITIVE_REFUSAL, 0)
        and f.calls == []
        and r["list_of_dates"]["rows"] == []
    )
    r2, _ = go(Case(), chron(("n.pdf", "The accused was charged with rape on 5 May 2020.")), complete=f)
    assert r2["state"] == "refused" and f.calls == []


def test_a_section_on_the_table_in_a_paper_refuses_when_the_table_is_present(monkeypatch):
    monkeypatch.setattr(sd.bd, "_sensitive_checker", lambda: lambda ms: any("370" in m for m in ms))
    r, _ = go(
        Case(), chron(("n.pdf", "He was charged under Section 370 of the Indian Penal Code on 5 May 2020.")), complete=Fake("{}")
    )
    assert r["state"] == "refused" and any(c["name"] == "sensitive_section_backstop" for c in r["checks"])


def test_wrong_model_is_loud_over_cap_is_refused_and_max_tokens_gives_the_table_only():
    case = Case()
    ans, _ = good_answer(case)
    with pytest.raises(AssertionError):
        go(case, complete=Fake(ans, model="other"))
    big = NS(**{**CFG.__dict__, "max_input_tokens_per_call": 50})
    r = sd.draft(case, chron(("note-A", PAPER)), Fake(ans), big, "x")
    assert r["state"] == "refused" and "token cap" in r["refused"]
    r2, _ = go(case, complete=Fake(ans, stop="max_tokens"))
    assert r2["state"] == "table_only" and r2["incomplete"] is True and r2["synopsis"]["fallback_line"] == sd.bd.INCOMPLETE_LINE


def test_notes_capped_at_five_adverse_facts_flagged_and_exclusions_reported():
    case = Case(
        [
            NS(fact_id="F1", text="The learned Public Prosecutor opposed the petition."),
            NS(fact_id="F2", text="The petitioner was arrested on 12.06.2024."),
        ],
        n_excl=2,
    )
    c = chron(("note-A", PAPER))
    ans = answer(
        case,
        c,
        [],
        [("The petitioner states that he was arrested on 12.06.2024 [F2].", ["F2"])],
        [f"Please confirm detail {i}." for i in range(1, 8)],
    )
    r, _ = go(case, c, Fake(ans))
    assert (
        len(r["notes_for_lawyer"]) == 5
        and r["more_notes"]
        and r["notes_for_lawyer"][0].startswith("Fact F1 reads like the court's")
    )
    assert any(ch["name"] == "adverse_voice" for ch in r["checks"])
    assert any("2 fact(s) were not marked" in n for n in r["notes_for_lawyer"] + r["more_notes"])


def test_more_than_sixty_rows_are_cut_with_a_line():
    body = (
        " ".join(f"The hearing was held on {d} March 2020." for d in range(1, 29))
        + " "
        + " ".join(f"The order was passed on {d} April 2020." for d in range(1, 29))
        + " "
        + " ".join(f"The notice was sent on {d} May 2020." for d in range(1, 10))
    )
    r, _ = go(complete=None, c=chron(("many.pdf", body)))
    assert len(r["list_of_dates"]["rows"]) == sd.MAX_ROWS and r["list_of_dates"]["cut_line"].startswith(
        "Showing the first 60 of "
    )


def test_no_rows_gives_the_none_line_and_the_module_calls_no_network_code():
    r, _ = go(complete=None, c=chron(("p.pdf", "Nothing with a date here.")))
    assert r["list_of_dates"]["rows"] == [] and r["list_of_dates"]["none_line"] == sd.NO_ROWS_LINE
    import inspect

    src = inspect.getsource(sd)
    for bad in ("anthropic", "urllib", "requests", "httpx", "socket", "subprocess"):
        assert bad not in src


def test_two_dates_in_one_sentence_become_one_table_row_with_both_dates_as_written():
    c = chron(
        (
            "p.pdf",
            "The petitioner was arrested on 24.12.2004 and produced before the Magistrate on 25.12.2004. The FIR was registered on 18 July 2021.",
        )
    )
    r, _ = go(complete=None, c=c)
    rows = r["list_of_dates"]["rows"]
    two = [x for x in rows if len(x["dates"]) == 2]
    assert (
        len(two) == 1
        and two[0]["date_as_written"] == "24.12.2004 and 25.12.2004"
        and len(two[0]["row_ids"]) == 2
        and two[0]["event_quote"].count("arrested") == 1
    )
    assert all(len(x["dates"]) == 1 for x in rows if x is not two[0]) and len(rows) == 2


def test_an_event_line_up_to_forty_words_passes_and_over_forty_fails():
    q = "The petitioner was arrested on 24.12.2004 and produced before the Magistrate on 25.12.2004."
    ok = "The petitioner was arrested and produced before the Magistrate " + "and the Magistrate " * 3
    assert len(ok.split()) <= 40 and not any("event_line_length" in w for w in sd.check_event_line(ok, q))
    assert any("event_line_length" in w for w in sd.check_event_line(" ".join(["arrested"] * 41), q))
