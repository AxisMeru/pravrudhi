"""HouseJudge re-expressed through the typed layer (T1, docs/decisions/TYPED-LAYER-PLAN-2026-09-24.md).
`nyaya_judges.HouseJudge` is completely untouched by this file's existence -- these tests only exercise the
NEW `TypedHouseJudge`, and separately assert byte-for-byte agreement between the two given the exact same
`JudgeRequest` and the exact same (fake, injected) completion -- the unit-test-level reading of T1's parity
pass bar (the live-GPU reading runs against the real 279-prompt set, see scripts/typed_layer_parity.py).
"""

from __future__ import annotations

import pytest

from pravrudhi.application.nyaya_judges import HouseJudge, JudgeOutputError, JudgeRequest, build_house_prompt
from pravrudhi.application.typed.decoder import VLLMDecoder
from pravrudhi.application.typed.house_judge import TypedHouseJudge
from pravrudhi.models.openai_compat import CompletionResult

STATUTE = "Whoever dishonestly misappropriates or converts to his own use any movable property shall be punished."
REQ = JudgeRequest(
    contract_id="ipc405_misappropriation",
    element="dishonestly misappropriates or converts the property to his own use",
    is_denial=False,
    statute=STATUTE,
    narrative="TOY: Ravi was entrusted with company funds and spent them on himself.",
    facts=(
        ("F1", "TOY: Ravi was given Rs. 50,000 to buy office supplies."),
        ("F2", "TOY: Ravi spent the money on a personal holiday."),
    ),
)


def _house_judge_transport(top: dict[str, float], text: str):
    """nyaya_judges.HouseJudge's own injected `complete`: a single-arg `(prompt) -> CompletionResult`."""

    def complete(prompt: str) -> CompletionResult:
        return CompletionResult(text=text, model="m", top_logprobs=[top], wall_s=0.1)

    return complete


def _decoder_transport(top: dict[str, float], text: str):
    """VLLMDecoder's own injected `complete`: `(prompt, *, max_tokens, temperature, logprobs) -> CompletionResult`."""
    calls: list[tuple[str, int, float, int | None]] = []

    def complete(prompt: str, *, max_tokens: int, temperature: float, logprobs: int | None) -> CompletionResult:
        calls.append((prompt, max_tokens, temperature, logprobs))
        return CompletionResult(text=text, model="m", top_logprobs=[top], wall_s=0.1)

    complete.calls = calls  # type: ignore[attr-defined]
    return complete


def _both_judges(top: dict[str, float], text: str):
    """Same JudgeRequest, same (top, text) server reply, fed to both judges through their own separate but
    identically-configured fake transport -- the results must be identical."""
    original = HouseJudge(tau=0.74, statute_chars=600, model="m", complete=_house_judge_transport(top, text))
    typed = TypedHouseJudge(tau=0.74, statute_chars=600, decoder=VLLMDecoder(model="m", complete=_decoder_transport(top, text)))
    return original.judge(REQ), typed.judge(REQ)


class TestTypedHouseJudgeAgreesWithHouseJudge:
    def test_established_with_a_known_fact_id(self) -> None:
        o, t = _both_judges({" established": -0.05, " not": -4.0}, "established F1")
        assert o.status == t.status == "established"
        assert o.p_established == pytest.approx(t.p_established, abs=1e-9)
        assert (o.fact_id, o.quote, o.quote_source) == (t.fact_id, t.quote, t.quote_source)
        assert (o.fact_id, o.quote, o.quote_source) == (
            "F1", "TOY: Ravi was given Rs. 50,000 to buy office supplies.", "whole_fact",
        )

    def test_not_established_below_tau(self) -> None:
        o, t = _both_judges({" established": -3.0, " not": -0.05}, "not")
        assert o.status == t.status == "not_established"
        assert o.p_established == pytest.approx(t.p_established, abs=1e-9)
        assert (o.fact_id, o.quote) == (t.fact_id, t.quote) == (None, None)

    def test_established_with_an_unknown_fact_id_has_no_quote_on_both(self) -> None:
        # The fact id itself is still reported (F9, what the model actually wrote) -- only the quote and its
        # source are None, since a fact id not among the real facts gets no quote to accept.
        o, t = _both_judges({" established": -0.05}, "established F9")
        assert o.status == t.status == "established"
        assert (o.fact_id, o.quote, o.quote_source) == (t.fact_id, t.quote, t.quote_source) == ("F9", None, None)

    def test_established_with_no_parseable_fact_id_at_all(self) -> None:
        o, t = _both_judges({" established": -0.05}, "established")
        assert (o.status, o.fact_id) == (t.status, t.fact_id) == ("established", None)

    def test_f_narrative_is_rejected_as_evidence_on_both(self) -> None:
        """Both judges share `parse_house_fact_id`: a completion naming the reserved `F_narrative` id (never
        one of the facts either judge's prompt shows, `build_house_prompt` above) must not be accepted as
        evidence by either -- not just excluded from the prompt, but refused as a fact_id too."""
        o, t = _both_judges({" established": -0.05}, "established F_narrative:0:50")
        assert o.status == t.status == "established"
        assert (o.fact_id, o.quote, o.quote_source) == (t.fact_id, t.quote, t.quote_source) == (None, None, None)

    def test_no_evidence_either_way_raises_on_both(self) -> None:
        original = HouseJudge(
            tau=0.74, statute_chars=600, model="m", complete=_house_judge_transport({"unrelated": -0.1}, "?")
        )
        typed = TypedHouseJudge(
            tau=0.74, statute_chars=600, decoder=VLLMDecoder(model="m", complete=_decoder_transport({"unrelated": -0.1}, "?"))
        )
        with pytest.raises(JudgeOutputError):
            original.judge(REQ)
        with pytest.raises(JudgeOutputError):
            typed.judge(REQ)


class TestTypedHouseJudgeUsesTheSameTrainingPrompt:
    def test_sends_build_house_prompt_verbatim(self) -> None:
        complete = _decoder_transport({" established": -0.05}, "established F1")
        typed = TypedHouseJudge(tau=0.74, statute_chars=600, decoder=VLLMDecoder(model="m", complete=complete))
        typed.judge(REQ)
        (prompt, max_tokens, temperature, logprobs), = complete.calls  # type: ignore[attr-defined]
        assert prompt == build_house_prompt(REQ, statute_chars=600)
        assert (max_tokens, temperature, logprobs) == (30, 0.0, 20)


class TestNyayaJudgesModuleIsUntouched:
    def test_house_judge_module_never_imports_the_typed_layer(self) -> None:
        # T1's hard constraint: HouseJudge's default runtime path must not change. The strongest static
        # check available here: nyaya_judges.py itself never imports the typed layer at all.
        import inspect

        import pravrudhi.application.nyaya_judges as mod

        source = inspect.getsource(mod)
        assert "application.typed" not in source
