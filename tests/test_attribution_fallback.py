"""The REFER-on-any-co-actor fallback. All sentences are constructed toy text."""

from __future__ import annotations

import pytest

from pravrudhi.application.nyaya_attribution import AccusedRef
from pravrudhi.application.nyaya_attribution_fallback import check_attribution_fallback, item_has_multiple_accused

REF = AccusedRef("a3", ("Accused No.3", "A3", "Pet. No.3"), (("Accused No.9", "A9"), ("her husband",)))


@pytest.mark.parametrize(
    "s",
    [
        "TOY: Accused No.3 beat Nila with a hockey stick.",
        "TOY: Nila was slapped by Accused No.3.",
        "TOY: Her husband, Accused No.3, beat Nila.",
        "TOY: Pet. No.3 abused Nila in filthy language.",
        "TOY: When Nila protested, Accused No.3 slapped her.",
    ],
)
def test_single_actor_sentences_pass(s: str) -> None:
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


def test_item_level_rule_f1() -> None:
    assert item_has_multiple_accused(REF)
    assert not item_has_multiple_accused(AccusedRef("a", ("Accused No.3",), (("her husband",),)))
    assert not item_has_multiple_accused(None)


def test_the_agent_builds_the_fallback_selector_and_the_judge_uses_it() -> None:
    from types import SimpleNamespace

    from pravrudhi.application.nyaya_agent import _build_actor_selector
    from pravrudhi.application.nyaya_attribution import AccusedAttributionJudge
    from pravrudhi.application.nyaya_attribution_fallback import FallbackChecker
    from pravrudhi.application.nyaya_judges import ElementJudgment, JudgeRequest

    sel = _build_actor_selector(SimpleNamespace(accused_attribution_selector="fallback"))  # type: ignore[arg-type]
    assert isinstance(sel, FallbackChecker) and sel.name == "fallback"

    class Inner:
        name = "inner"

        def judge(self, request: JudgeRequest) -> ElementJudgment:
            return ElementJudgment(status="established", p_established=0.9, quote="TOY: Accused No.3 and others beat Nila.", fact_id="F1")

    class Req:
        requires_actor = True
        is_denial = False
        accused = REF

    out = AccusedAttributionJudge(Inner(), selector=sel).judge(Req())  # type: ignore[arg-type]
    assert out.attribution["passed"] is False and out.attribution["variant"] == "FALLBACK"
