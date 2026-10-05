"""The REFER-on-any-co-actor fallback. All sentences are constructed toy text."""

from __future__ import annotations

import pytest

from pravrudhi.application.nyaya_attribution import AccusedRef
from pravrudhi.application.nyaya_attribution_fallback import check_attribution_fallback, item_has_multiple_accused

REF = AccusedRef("a3", ("Accused No.3", "A3", "Pet. No.3"), (("her husband",),))
REF_MULTI = AccusedRef("a3", ("Accused No.3", "A3", "Pet. No.3"), (("Accused No.9", "A9"), ("her husband",)))


@pytest.mark.parametrize(
    "s",
    [
        "TOY: Accused No.3 beat her with a hockey stick.",
        "TOY: She was slapped by Accused No.3.",
        "TOY: Her husband, Accused No.3, beat her.",
        "TOY: Pet. No.3 abused her in filthy language.",
        "TOY: When she protested, Accused No.3 slapped her.",
    ],
)
def test_single_actor_sentences_pass(s: str) -> None:
    """Named victims ("Nila") are refused by the bare-name rule (a documented cost), so these use "she/her"."""
    r = check_attribution_fallback(s, REF)
    assert r.passed, (s, r.rule, r.actor_span)


@pytest.mark.parametrize(
    "s",
    [
        "TOY: Accused No.3 and others dragged Nila by the plait.",
        "TOY: Pet. No.3 and others beat Nila.",
        "TOY: Accused No.3 & Ors. beat Nila.",
        "TOY: Accused No.3 and his people beat Nila.",
        "TOY: Accused No.3 backed by Gopal Rao scratched Nila.",
        "TOY: Accused No.3 coupled with three unidentified men hid her medicines.",
        "TOY: Accused No.3 bolted her in, and so did her husband's nephew.",
        "TOY: Accused No.3 in collaboration with A.40 scratched her.",
        "TOY: Nila was beaten by Accused No.3 and Mr. Arvind Modi.",
        "TOY: Accused No.3 beat Nila with the help of Mrs. Radhika Jadhav.",
        "TOY: Accused No.3 beat Nila, hand in glove with the maternal uncle of the husband.",
        "TOY: Accused No.9 beat Nila.",
        "TOY: Accused No.3 beat Nila in front of her in-laws.",
        "TOY: He beat Nila.",
    ],
)
def test_co_actor_group_or_unresolved_sentences_refuse(s: str) -> None:
    r = check_attribution_fallback(s, REF)
    assert not r.passed, s


def test_abbreviation_dots_do_not_split_the_act_sentence() -> None:
    assert not check_attribution_fallback("TOY: Pet. No.3 and others demanded Rs. 5 lakhs.", REF).passed


def test_no_accused_and_errors_refuse() -> None:
    assert check_attribution_fallback("TOY: Accused No.3 beat Nila.", None).reason == "accused_not_specified"
    assert not check_attribution_fallback("", REF).passed
    assert not check_attribution_fallback(None, REF).passed


def test_item_level_rule_f1_is_applied_by_the_check_and_can_be_switched_off_for_measurement() -> None:
    r = check_attribution_fallback("TOY: Accused No.3 beat her with a stick.", REF_MULTI)
    assert not r.passed and r.rule == "F1"
    assert check_attribution_fallback("TOY: Accused No.3 beat her with a stick.", REF_MULTI, item_level=False).passed
    assert item_has_multiple_accused(REF_MULTI)
    assert not item_has_multiple_accused(REF)
    assert not item_has_multiple_accused(None)


def test_the_agent_builds_the_fallback_selector_and_the_judge_uses_it() -> None:
    from types import SimpleNamespace

    from pravrudhi.application.nyaya_agent import _build_actor_selector, _build_attribution_checker
    from pravrudhi.application.nyaya_attribution import AccusedAttributionJudge
    from pravrudhi.application.nyaya_attribution_fallback import FallbackChecker
    from pravrudhi.application.nyaya_judges import ElementJudgment, JudgeRequest

    cfg = SimpleNamespace(accused_attribution_selector="fallback")
    sel = _build_attribution_checker(cfg)  # type: ignore[arg-type]
    assert isinstance(sel, FallbackChecker) and sel.name == "fallback"
    assert _build_actor_selector(cfg) is None  # type: ignore[arg-type]

    class Inner:
        name = "inner"

        def judge(self, request: JudgeRequest) -> ElementJudgment:
            return ElementJudgment(status="established", p_established=0.9, quote="TOY: Accused No.3 and others beat Nila.", fact_id="F1")

    class Req:
        requires_actor = True
        is_denial = False
        accused = REF

    out = AccusedAttributionJudge(Inner(), checker=sel).judge(Req())  # type: ignore[arg-type]
    assert out.attribution["passed"] is False and out.attribution["variant"] == "FALLBACK"


@pytest.mark.parametrize(
    "s",
    [
        "TOY: Accused No.3 beat Nila. Her sister-in-law, Meena, held her arms.",
        "TOY: Accused No.3 beat Nila.\nLater the same night he threw her out, and Ramu helped him.",
    ],
)
def test_multi_sentence_quotes_are_never_passed(s: str) -> None:
    r = check_attribution_fallback(s, REF)
    assert not r.passed and r.rule == "F5"


@pytest.mark.parametrize(
    "s",
    [
        "TOY: Accused No.3 beat Nila while Ramu held her arms.",
        "TOY: Accused No.3 beat Nila and Ramu too.",
        "TOY: When Ramu arrived and joined, Accused No.3 beat Nila.",
        "TOY: Ramu held her down and Accused No.3 beat her.",
        "TOY: Accused No.3 beat Nila with the husband's aunt looking on.",
        "TOY: Accused No.3 beat Nila, as did the husband's aunt.",
    ],
)
def test_bare_first_names_and_x_kin_refuse(s: str) -> None:
    r = check_attribution_fallback(s, REF)
    assert not r.passed, s


def test_the_judge_holds_the_fallback_as_an_explicit_checker_and_rejects_both() -> None:
    from pravrudhi.application.nyaya_attribution import AccusedAttributionJudge
    from pravrudhi.application.nyaya_attribution_fallback import FallbackChecker

    class Inner:
        name = "inner"

    with pytest.raises(ValueError, match="not both"):
        AccusedAttributionJudge(Inner(), selector=object(), checker=FallbackChecker())  # type: ignore[arg-type]
