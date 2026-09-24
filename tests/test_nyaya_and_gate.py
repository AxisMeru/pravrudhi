"""Config C: AND(4B house judge @ 0.74, 32B QLoRA judge @ 0.97) -- `AndGateJudge`.

Both real judges are `HouseJudge`s (same prompt/first-token-logprob code, module doc in nyaya_judges.py); this
file never re-derives p_established or writes a second prompt template, only wraps two `Judge`s and stubs to
drive the gate's own AND/skip/veto/fail-closed logic. Facts and statute text are hand-written toy text.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from pravrudhi.application.nyaya_judges import (
    AndGateJudge,
    ElementJudgment,
    HouseJudge,
    JudgeRequest,
)

REQ = JudgeRequest(
    contract_id="bns85",
    element="the accused is the husband, or a relative of the husband, of the woman",
    is_denial=False,
    statute="Whoever, being the husband ... shall be punished.",
    narrative="TOY: Arun married Bela in 2019.",
    facts=(("F1", "TOY: Arun married Bela in 2019."), ("F2", "TOY: Arun beat Bela.")),
)


@dataclass
class _StubJudge:
    """A `Judge` double that returns a fixed judgment (or raises a fixed exception) and records calls."""

    name: str
    result: ElementJudgment | None = None
    error: BaseException | None = None

    def __post_init__(self) -> None:
        self.calls = 0

    def judge(self, request: JudgeRequest) -> ElementJudgment:
        self.calls += 1
        if self.error is not None:
            raise self.error
        assert self.result is not None
        return self.result


def _judgment(status: str, p: float, *, fact_id: str | None = "F1", quote: str | None = "TOY: Arun married Bela in 2019.",
              backend_used: int | None = 0) -> ElementJudgment:
    return ElementJudgment(status, p, fact_id, quote, "whole_fact" if fact_id else None,
                           raw=f"established {fact_id}" if status == "established" else "not", backend_used=backend_used)


class TestAndDecision:
    """Feeds recorded (p4B, p32B) pairs through the gate at tau 0.74 / 0.97, including the boundary p == tau."""

    @pytest.mark.parametrize(
        "p4b,p32b,expected",
        [
            (0.97, 0.99, "established"),  # both clear the bar
            (0.74, 0.97, "established"),  # both exactly AT their own tau (boundary, >= not >)
            (0.90, 0.96, "not_established"),  # 32B just under its 0.97
            (0.73, 0.999, "not_established"),  # 4B just under its 0.74 -- second must be SKIPPED, not consulted
            (0.50, 0.999, "not_established"),  # 4B far below tau
        ],
    )
    def test_and_of_both_taus(self, p4b: float, p32b: float, expected: str) -> None:
        primary_status = "established" if p4b >= 0.74 else "not_established"
        second_status = "established" if p32b >= 0.97 else "not_established"
        primary = _StubJudge("house-4b", _judgment(primary_status, p4b))
        second = _StubJudge("house-32b", _judgment(second_status, p32b))
        gate = AndGateJudge(primary, second, tau_primary=0.74, tau_second=0.97)
        out = gate.judge(REQ)
        assert out.status == expected
        assert out.p_established == p4b
        assert out.tau_primary == 0.74 and out.tau_second == 0.97
        if primary_status != "established":
            assert second.calls == 0  # cost saved: never asked
        else:
            assert second.calls == 1
            assert out.p_established_second == p32b

    def test_both_established_records_second_p_and_backends(self) -> None:
        primary = _StubJudge("house-4b", _judgment("established", 0.97, backend_used=0))
        second = _StubJudge("house-32b", _judgment("established", 0.99, backend_used=1))
        gate = AndGateJudge(primary, second, tau_primary=0.74, tau_second=0.97)
        out = gate.judge(REQ)
        assert out.status == "established"
        assert out.backend_used == 0 and out.backend_used_second == 1
        assert out.second_judge == "house-32b" and out.second_status == "established"
        assert out.vetoed_by is None


class TestSkipWhenPrimaryRejects:
    def test_primary_not_established_skips_second_and_records_why(self) -> None:
        primary = _StubJudge("house-4b", _judgment("not_established", 0.3, fact_id=None, quote=None))
        second = _StubJudge("house-32b", _judgment("established", 0.99))
        out = AndGateJudge(primary, second, tau_primary=0.74, tau_second=0.97).judge(REQ)
        assert out.status == "not_established"
        assert second.calls == 0
        assert out.second_skipped is True
        assert out.second_skip_reason == "primary_not_established"
        assert out.vetoed_by == "primary"


class TestFailClosedOnSecondUnavailable:
    def test_second_error_never_falls_back_to_primary_alone(self) -> None:
        """The primary alone says established; the second is configured but unreachable. The element must be
        NOT established -- never silently scored on the 4B alone -- and the audit trail records why."""
        primary = _StubJudge("house-4b", _judgment("established", 0.97))
        second = _StubJudge("house-32b", error=ConnectionError("all backends down"))
        out = AndGateJudge(primary, second, tau_primary=0.74, tau_second=0.97).judge(REQ)
        assert out.status == "not_established"
        assert out.vetoed_by == "second"
        assert out.second_skip_reason is not None and "second_unavailable" in out.second_skip_reason

    def test_second_config_fault_surfaces_instead_of_failing_closed(self) -> None:
        """A 4xx from the second judge (bad key, unknown model) is a configuration fault, exactly like the
        primary's -- it must propagate so the run stops, never be read as an ordinary veto."""
        from pravrudhi.models.openai_compat import HTTPStatusError

        primary = _StubJudge("house-4b", _judgment("established", 0.97))
        second = _StubJudge("house-32b", error=RuntimeError("judge backend 0 failed: bad key") )
        second.error.__cause__ = HTTPStatusError(401, "unauthorized")  # type: ignore[union-attr]
        with pytest.raises(RuntimeError):
            AndGateJudge(primary, second, tau_primary=0.74, tau_second=0.97).judge(REQ)


class TestFactIdDisagreement:
    def test_primarys_span_is_kept_and_disagreement_is_recorded(self) -> None:
        primary = _StubJudge("house-4b", _judgment("established", 0.9, fact_id="F1", quote="TOY: Arun married Bela in 2019."))
        second = _StubJudge("house-32b", _judgment("established", 0.98, fact_id="F2", quote="TOY: Arun beat Bela."))
        out = AndGateJudge(primary, second, tau_primary=0.74, tau_second=0.97).judge(REQ)
        assert out.status == "established"
        assert (out.fact_id, out.quote) == ("F1", "TOY: Arun married Bela in 2019.")  # primary's span wins
        assert out.second_fact_id == "F2"
        assert out.fact_id_disagreement is True

    def test_agreeing_fact_ids_are_not_flagged(self) -> None:
        primary = _StubJudge("house-4b", _judgment("established", 0.9, fact_id="F1"))
        second = _StubJudge("house-32b", _judgment("established", 0.98, fact_id="F1"))
        out = AndGateJudge(primary, second, tau_primary=0.74, tau_second=0.97).judge(REQ)
        assert out.fact_id_disagreement is False


class TestReusesHouseJudgeCode:
    """`AndGateJudge` never re-derives p_established or writes a second prompt template: both slots take real
    `HouseJudge`s, and the gate's AND/skip logic drives them through the SAME `build_house_prompt` /
    `p_established_from_top_logprobs` code already covered in test_nyaya_judges.py."""

    def test_wraps_two_real_house_judges(self) -> None:
        from tests.test_nyaya_judges import _completion, _FakeComplete

        fake4b = _FakeComplete(_completion(" established F1", {" established": -0.05, " not": -3.0}))
        fake32b = _FakeComplete(_completion(" established F1", {" established": -0.01, " not": -5.0}))
        judge4b = HouseJudge(complete=fake4b, tau=0.74, statute_chars=600)
        judge32b = HouseJudge(complete=fake32b, tau=0.97, statute_chars=600)
        out = AndGateJudge(judge4b, judge32b, tau_primary=0.74, tau_second=0.97).judge(REQ)
        assert out.status == "established"
        assert fake4b.prompts == fake32b.prompts  # same prompt template, both judges
