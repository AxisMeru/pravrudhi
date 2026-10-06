"""#428 step 2: Clef grounding backend. Canned transport only (no model, no network); synthetic facts only."""

from __future__ import annotations

import ast
import math
from pathlib import Path

import pytest

from pravrudhi.application.typed import clef_grounding as G
from pravrudhi.application.typed.clef import ClefDecodeError

STATE = "A marriage; the wife alleges cruelty by the husband and his mother."
FACTS = [
    ("F1", "The husband struck the wife on 3 May."),
    ("F2", "The mother-in-law demanded a car."),
    ("F3", "They married in 2015."),
]
SRC = Path(G.__file__).resolve().parents[2]  # src/pravrudhi


def reply_from(p: dict[str, float]):
    """A transport answering each question with logits whose softmax is exactly p[question id]."""

    def transport(record):
        out = {}
        for q in record["questions"]:
            pq = p[q]
            out[q] = {"true": math.log(pq / (1 - pq)), "false": 0.0}
        return out

    return transport


def test_records_one_noul_question_per_fact_narrative_excluded_and_text_in_instructions():
    recs = G.build_grounding_records([*FACTS, ("F_narrative", "long story")], "he beat her", state=STATE)
    assert len(recs) == 1 and list(recs[0]["questions"]) == ["F1", "F2", "F3"]
    q = recs[0]["questions"]["F1"]
    assert q["type"] == "noul" and "he beat her" in q["instructions"] and "struck the wife" in q["instructions"]
    assert recs[0]["state"] == STATE


def test_chunking_at_the_questions_limit_with_state_repeated():
    facts = [(f"F{i}", f"fact number {i}") for i in range(1, 8)]
    recs = G.build_grounding_records(facts, "c", state=STATE, max_questions=3)
    assert [len(r["questions"]) for r in recs] == [3, 3, 1] and all(r["state"] == STATE for r in recs)
    assert len(G.build_grounding_records([(f"F{i}", "x") for i in range(130)], "c", state=STATE)) == 3  # Clef's 64-question limit


def test_identical_fact_texts_still_get_distinct_question_ids():
    recs = G.build_grounding_records([("F1", "same text"), ("F2", "same text")], "c", state=STATE)
    assert list(recs[0]["questions"]) == ["F1", "F2"]


@pytest.mark.parametrize("facts", [[("F1", "a"), ("F1", "b")], [("F1", "  ")], [("", "a")]])
def test_bad_facts_are_refused(facts):
    with pytest.raises(ValueError):
        G.build_grounding_records(facts, "c", state=STATE)


def test_narrative_only_or_empty_request_is_refused_for_records_and_ungrounded_for_selection():
    with pytest.raises(ValueError, match="no askable facts"):
        G.build_grounding_records([("F_narrative", "n")], "c", state=STATE)
    with pytest.raises(G.UngroundedError):
        G.select_grounding({}, floor=0.5, margin=0.1)  # nothing citable: never a quote, never a default


@pytest.mark.parametrize("claim", ["", "   ", "x" * (G.MAX_CLAIM_CHARS + 1)])
def test_claim_must_be_non_empty_and_length_capped(claim):
    with pytest.raises(ValueError):
        G.build_grounding_records(FACTS, claim, state=STATE)


def test_empty_state_and_bad_chunk_size_refused():
    with pytest.raises(ValueError):
        G.build_grounding_records(FACTS, "c", state=" ")
    with pytest.raises(ValueError):
        G.build_grounding_records(FACTS, "c", state=STATE, max_questions=0)


def test_probabilities_are_the_softmax_of_the_two_logits_in_fact_order():
    probs = G.ground_facts(FACTS, "he beat her", state=STATE, transport=reply_from({"F1": 0.9, "F2": 0.2, "F3": 0.05}))
    assert list(probs) == ["F1", "F2", "F3"] and probs["F1"] == pytest.approx(0.9) and probs["F3"] == pytest.approx(0.05)


@pytest.mark.parametrize(
    "bad",
    [
        {"F1": {"true": 1.0, "false": 0.0}},  # F2, F3 not answered
        {
            "F1": {"true": 1.0, "false": 0.0},
            "F2": {"true": 1.0, "false": 0.0},
            "F3": {"true": 1.0, "false": 0.0},
            "F9": {"true": 1.0, "false": 0.0},
        },  # extra
        {q: {"true": float("nan"), "false": 0.0} for q in ("F1", "F2", "F3")},
        {q: {"true": True, "false": 0.0} for q in ("F1", "F2", "F3")},
        {q: {"yes": 1.0, "no": 0.0} for q in ("F1", "F2", "F3")},  # unexpected option names fail closed
        "not a mapping",
    ],
)
def test_decode_failures_raise_clef_decode_error_never_a_default(bad):
    with pytest.raises(ClefDecodeError):
        G.ground_facts(FACTS, "c", state=STATE, transport=lambda r: bad)


def test_select_grounded_returns_all_above_floor_in_fact_order_best_and_margin():
    c = G.select_grounding({"F1": 0.9, "F2": 0.2, "F3": 0.05}, floor=0.5, margin=0.3)
    assert c.ids_above_floor == ("F1",) and c.best == "F1" and c.margin == pytest.approx(0.7)


def test_no_fact_clears_the_floor_raises_ungrounded_not_a_decode_error():
    with pytest.raises(G.UngroundedError) as e:
        G.select_grounding({"F1": 0.4, "F2": 0.2}, floor=0.5, margin=0.1)
    assert not isinstance(e.value, ClefDecodeError)


def test_two_facts_above_the_floor_is_multiple_support_unless_a_single_id_is_needed():
    probs = {"F1": 0.9, "F2": 0.85}
    c = G.select_grounding(probs, floor=0.5, margin=0.2, need_single=False)  # the caller picks; several may support the claim
    assert c.ids_above_floor == ("F1", "F2") and c.best == "F1"
    with pytest.raises(G.AmbiguousGrounding):  # a quote needs ONE fact: best minus second 0.05 is below the margin 0.2
        G.select_grounding(probs, floor=0.5, margin=0.2)
    assert issubclass(G.AmbiguousGrounding, G.UngroundedError)


def test_exact_tie_raises_when_a_single_id_is_needed_and_breaks_by_fact_order_otherwise():
    with pytest.raises(G.AmbiguousGrounding):
        G.select_grounding({"F1": 0.8, "F2": 0.8}, floor=0.5, margin=0.01)
    assert G.select_grounding({"F2": 0.8, "F1": 0.8}, floor=0.5, margin=0.0, order=["F1", "F2"]).best == "F1"


def test_margin_with_one_fact_above_the_floor_is_best_minus_second_of_all_facts():
    c = G.select_grounding({"F1": 0.9, "F2": 0.45}, floor=0.5, margin=0.4)  # only F1 clears the floor; 0.9 - 0.45 = 0.45
    assert c.ids_above_floor == ("F1",) and c.margin == pytest.approx(0.45)
    assert G.select_grounding({"F1": 0.7}, floor=0.5, margin=0.9).margin == pytest.approx(0.7)  # one fact: the best itself


@pytest.mark.parametrize(
    "floor,margin", [(math.nan, 0.1), (0.5, math.nan), (-0.1, 0.1), (0.5, 1.5), (True, 0.1), (0.5, "x"), (math.inf, 0.1)]
)
def test_floor_and_margin_are_validated_finite_in_unit_interval(floor, margin):
    with pytest.raises(ValueError):
        G.select_grounding({"F1": 0.9}, floor=floor, margin=margin)


def test_floor_and_margin_have_no_defaults():
    with pytest.raises(TypeError):
        G.select_grounding({"F1": 0.9})  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        G.select_grounding({"F1": 0.9}, floor=0.5)  # type: ignore[call-arg]


def test_non_finite_probabilities_raise_decode_error():
    with pytest.raises(ClefDecodeError):
        G.select_grounding({"F1": float("nan")}, floor=0.5, margin=0.1)
    with pytest.raises(ClefDecodeError):
        G.select_grounding({"F1": 1.5}, floor=0.5, margin=0.1)


def test_accused_link_grounds_when_a_fact_names_the_accused():
    t = reply_from({"F1": 0.92, "F2": 0.1, "F3": 0.05})
    c = G.accused_link(FACTS, "the husband", "struck the wife", state=STATE, transport=t, floor=0.5, margin=0.2)
    assert c.best == "F1"
    seen = []
    G.accused_link(FACTS, "The  HUSBAND", "x", state=STATE, transport=lambda r: seen.append(r) or t(r), floor=0.5, margin=0.2)
    assert "the husband committed the act" in seen[0]["questions"]["F1"]["instructions"].lower()


def test_accused_link_raises_when_the_accused_is_named_in_no_fact_and_makes_no_call():
    calls = []

    def transport(record):
        calls.append(record)
        return {}

    with pytest.raises(G.AccusedNotInFacts):
        G.accused_link(FACTS, "the brother-in-law", "threatened her", state=STATE, transport=transport, floor=0.5, margin=0.1)
    assert calls == []  # refused before any call, not scored low


def test_accused_link_with_no_supporting_fact_is_ungrounded():
    t = reply_from({"F1": 0.2, "F2": 0.1, "F3": 0.05})
    with pytest.raises(G.UngroundedError):
        G.accused_link(FACTS, "the husband", "stole", state=STATE, transport=t, floor=0.5, margin=0.1)


def test_factory_is_off_by_default_and_needs_transport_floor_and_margin_when_on():
    t = reply_from({"F1": 0.9, "F2": 0.1, "F3": 0.1})
    assert G.build_clef_grounding(None, t) is None
    assert G.build_clef_grounding({}, t) is None
    assert G.build_clef_grounding({"clef_grounding": False, "floor": 0.5, "margin": 0.1}, t) is None
    assert (
        G.build_clef_grounding({"clef_grounding": "yes", "floor": 0.5, "margin": 0.1}, t) is None
    )  # only a real True turns it on
    with pytest.raises(ValueError, match="transport"):
        G.build_clef_grounding({"clef_grounding": True, "floor": 0.5, "margin": 0.1}, None)
    with pytest.raises(ValueError, match="floor"):
        G.build_clef_grounding({"clef_grounding": True}, t)
    with pytest.raises(ValueError):
        G.build_clef_grounding({"clef_grounding": True, "floor": math.nan, "margin": 0.1}, t)
    on = G.build_clef_grounding({"clef_grounding": True, "floor": 0.5, "margin": 0.2}, t)
    assert on is not None and on.ground(FACTS, "c", state=STATE).best == "F1"
    assert on.accused_link(FACTS, "husband", "struck", state=STATE).best == "F1"


def test_factory_level_unexpected_option_names_raise():
    on = G.build_clef_grounding(
        {"clef_grounding": True, "floor": 0.5, "margin": 0.1}, lambda r: {q: {"a": 1.0, "b": 0.0} for q in r["questions"]}
    )
    assert on is not None
    with pytest.raises(ClefDecodeError):
        on.ground(FACTS, "c", state=STATE)


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    names: set[str] = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            names |= {a.name for a in n.names}
        elif isinstance(n, ast.ImportFrom):
            names.add(n.module or "")
            names |= {f"{n.module}.{a.name}" for a in n.names}
    return names


@pytest.mark.parametrize("rel", ["application/nyaya_agent.py", "application/nyaya_judges.py", "application/typed/house_judge.py"])
def test_no_decision_path_imports_clef_grounding(rel):
    assert not any("clef_grounding" in n for n in _imports(SRC / rel))


def test_the_flag_key_is_read_only_inside_the_factory_and_no_other_src_file_mentions_it():
    mod = Path(G.__file__)
    tree = ast.parse(mod.read_text())
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "build_clef_grounding")
    uses = [n.lineno for n in ast.walk(tree) if isinstance(n, ast.Name) and n.id == "FLAG_KEY" and isinstance(n.ctx, ast.Load)]
    assert uses and all(fn.lineno <= ln <= fn.end_lineno for ln in uses)  # type: ignore[operator]
    others = [p for p in SRC.rglob("*.py") if p != mod and "clef_grounding" in p.read_text()]
    assert others == []
