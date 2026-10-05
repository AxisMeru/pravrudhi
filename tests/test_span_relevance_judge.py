"""Obj-1 Fix 2 Option A: `SpanRelevanceJudge`. Judge doubles only; the fixtures are short constructed
sentences shaped like two Obj-1 DEV failure classes (a span about a different matter; a span that states a
different legal relationship). No Obj-1b content, no model call."""

from __future__ import annotations

from dataclasses import dataclass, field

from pravrudhi.application.nyaya_agent import AgentConfig, _truthful_status
from pravrudhi.application.nyaya_judges import ElementJudgment, JudgeRequest, SpanRelevanceJudge

ELEMENT = "entrusted with property, or with dominion over property"
SPAN_OFF_TOPIC = "The accused had a long-standing personal relationship with a third party."
SPAN_ON_TOPIC = "The complainant handed the gold ornaments to the accused to keep safely for a month."


@dataclass
class Primary:
    """Always establishes `span` on fact F1."""

    span: str = SPAN_OFF_TOPIC
    name: str = "primary"

    def judge(self, request: JudgeRequest) -> ElementJudgment:
        return ElementJudgment("established", 0.95, "F1", self.span, "whole_fact")


@dataclass
class Check:
    """Established iff the span it is shown is in `yes_spans`; records every request."""

    yes_spans: frozenset[str] = frozenset()
    raises: bool = False
    name: str = "check"
    seen: list[JudgeRequest] = field(default_factory=list)

    def judge(self, request: JudgeRequest) -> ElementJudgment:
        self.seen.append(request)
        if self.raises:
            raise RuntimeError("endpoint down")
        text = dict(request.facts)["F1"]
        ok = text in self.yes_spans
        return ElementJudgment("established" if ok else "not_established", 0.9 if ok else 0.1, "F1", text, "whole_fact")


def _req(is_denial: bool = False) -> JudgeRequest:
    return JudgeRequest("ipc405_misappropriation", ELEMENT, is_denial, "stat", "full narrative",
                        (("F1", SPAN_OFF_TOPIC),))


def test_off_topic_span_is_demoted():
    primary = Primary(span=SPAN_OFF_TOPIC)
    check = Check(yes_spans=frozenset({SPAN_ON_TOPIC}))
    j = SpanRelevanceJudge(primary, check).judge(_req())
    assert j.status == "not_established" and j.vetoed_by == "span_relevance" and j.span_relevance_p == 0.1


def test_on_topic_span_survives_with_p_recorded():
    primary = Primary(span=SPAN_ON_TOPIC)
    check = Check(yes_spans=frozenset({SPAN_ON_TOPIC}))
    j = SpanRelevanceJudge(primary, check).judge(_req())
    assert j.status == "established" and j.vetoed_by is None and j.span_relevance_p == 0.9


def test_check_sees_only_the_span():
    primary = Primary(span=SPAN_ON_TOPIC)
    check = Check(yes_spans=frozenset({SPAN_ON_TOPIC}))
    SpanRelevanceJudge(primary, check).judge(_req())
    (probe,) = check.seen
    assert probe.narrative == SPAN_ON_TOPIC and probe.facts == (("F1", SPAN_ON_TOPIC),)
    assert probe.is_denial is False and probe.element == ELEMENT and probe.skip_second is True


def test_denial_elements_are_never_checked():
    primary = Primary(span=SPAN_OFF_TOPIC)
    check = Check()
    j = SpanRelevanceJudge(primary, check).judge(_req(is_denial=True))
    assert j.status == "established" and check.seen == []


def test_not_established_is_passed_through_without_a_check():
    class No:
        name = "no"

        def judge(self, request):
            return ElementJudgment("not_established", 0.2)

    check = Check()
    j = SpanRelevanceJudge(No(), check).judge(_req())
    assert j.status == "not_established" and j.vetoed_by is None and check.seen == []


def test_check_error_fails_closed_with_reason():
    primary = Primary(span=SPAN_ON_TOPIC)
    check = Check(raises=True)
    j = SpanRelevanceJudge(primary, check).judge(_req())
    assert j.status == "not_established" and j.vetoed_by == "span_relevance"
    assert j.span_relevance_skip_reason.startswith("span_relevance_unavailable: RuntimeError")


def test_truthful_status_maps_the_veto_to_plain_not_established():
    anchor = ElementJudgment("not_established", 0.95, "F1", SPAN_OFF_TOPIC, "whole_fact", vetoed_by="span_relevance")
    assert _truthful_status(False, False, anchor, False, second_judge_configured=False) == ("not_established", None)


def test_flag_defaults_off():
    assert AgentConfig.__dataclass_fields__["span_relevance_enabled"].default is False


def test_env_switch_enables_it(monkeypatch):
    from pathlib import Path

    from pravrudhi.application.nyaya_agent import load_agent_config

    root = Path(__file__).resolve().parents[1]
    monkeypatch.delenv("NYAYA_SPAN_RELEVANCE_ENABLED", raising=False)
    assert load_agent_config(root).span_relevance_enabled is False
    monkeypatch.setenv("NYAYA_SPAN_RELEVANCE_ENABLED", "1")
    assert load_agent_config(root).span_relevance_enabled is True
