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
        o, t = _both_judges({" established": -0.05, " not": -4.0}, "established F9")
        assert o.status == t.status == "established"
        assert (o.fact_id, o.quote, o.quote_source) == (t.fact_id, t.quote, t.quote_source) == ("F9", None, None)

    def test_established_with_no_parseable_fact_id_at_all(self) -> None:
        o, t = _both_judges({" established": -0.05, " not": -4.0}, "established")
        assert (o.status, o.fact_id) == (t.status, t.fact_id) == ("established", None)

    def test_f_narrative_is_rejected_as_evidence_on_both(self) -> None:
        """Both judges share `parse_house_fact_id`: a completion naming the reserved `F_narrative` id (never
        one of the facts either judge's prompt shows, `build_house_prompt` above) must not be accepted as
        evidence by either -- not just excluded from the prompt, but refused as a fact_id too."""
        o, t = _both_judges({" established": -0.05, " not": -4.0}, "established F_narrative:0:50")
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


class TestTypedHouseJudgeConservativeDecisionRuleAgreesWithHouseJudge:
    """Per R1's finding on #120 (G-28) and Lead-2's follow-up ask: decoder.score_decision had the
    identical clamp-to-0/1 bug, independently, and TypedHouseJudge.judge needs the same conservative
    rule HouseJudge.judge got. These three mirror #120's own judge()-level tests, through BOTH judges
    at once via _both_judges, confirming the fix landed identically on both."""

    def test_lower_bound_clearing_tau_is_established_on_both(self) -> None:
        o, t = _both_judges({" established": -0.05, " F": -3.0}, "established F1")
        assert o.status == t.status == "established"
        assert o.clamp == t.clamp == "lower_bound"
        assert o.bound_undetermined is t.bound_undetermined is False
        assert o.p_established == pytest.approx(t.p_established)

    def test_lower_bound_below_tau_is_not_established_and_flagged_on_both(self) -> None:
        o, t = _both_judges({" established": -0.05, " F": -0.5}, "established F1")
        assert o.status == t.status == "not_established"
        assert o.clamp == t.clamp == "lower_bound"
        assert o.bound_undetermined is t.bound_undetermined is True
        assert o.p_established == pytest.approx(t.p_established)

    def test_upper_bound_is_a_genuine_not_established_on_both_never_undetermined(self) -> None:
        o, t = _both_judges({" not": -0.1, " F": -6.0}, "not")
        assert o.status == t.status == "not_established"
        assert o.clamp == t.clamp == "upper_bound"
        assert o.bound_undetermined is t.bound_undetermined is False
        assert o.p_established == pytest.approx(t.p_established)


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


class TestNonFiniteLogprobsNeverEstablish:
    """#172: the typed judge must fail closed (JudgeOutputError) on a NaN/+inf logprob, like HouseJudge (#156)."""

    @staticmethod
    def _typed(top: dict[str, float]) -> TypedHouseJudge:
        return TypedHouseJudge(
            tau=0.74, statute_chars=600,
            decoder=VLLMDecoder(model="m", complete=_decoder_transport(top, " established F1:5:22")),
        )

    @pytest.mark.parametrize(
        "top",
        [
            {"established": float("nan"), " established": -0.1},
            {" established": -0.1, " not": -3.0, "Based": float("nan")},
            {" established": float("nan"), " not": -1.0},
            {" established": float("inf"), " not": -1.0},
        ],
    )
    def test_nan_or_inf_logprob_raises_judge_output_error(self, top: dict[str, float]) -> None:
        with pytest.raises(JudgeOutputError):
            self._typed(top).judge(REQ)

    def test_fuzz_no_non_finite_input_is_ever_established(self) -> None:
        import random

        rng = random.Random(172)
        keys = [" established", "established", " not", "not", "Based", " the"]
        escapes = 0
        for _ in range(500):
            top = {k: rng.uniform(-12.0, 0.0) for k in rng.sample(keys, rng.randint(2, len(keys)))}
            for k in rng.sample(sorted(top), rng.randint(1, len(top))):
                top[k] = rng.choice([float("nan"), float("inf")])
            try:
                j = self._typed(top).judge(REQ)
            except JudgeOutputError:
                continue
            escapes += 1
            assert j.status != "established"
        assert escapes == 0


def _two_call_decoder(first: tuple[dict[str, float], str], second: dict[str, float]):
    """Call 1 is the decision completion; call 2 (the id-choice call) returns `second` as its position-0 top-k."""
    calls: list[tuple[str, int, float, int | None]] = []

    def complete(prompt: str, *, max_tokens: int, temperature: float, logprobs: int | None) -> CompletionResult:
        calls.append((prompt, max_tokens, temperature, logprobs))
        if len(calls) == 1:
            return CompletionResult(text=first[1], model="m", top_logprobs=[first[0]], wall_s=0.1)
        return CompletionResult(text=" F2", model="m", top_logprobs=[second], wall_s=0.1)

    complete.calls = calls  # type: ignore[attr-defined]
    return complete


EST = {" established": -0.05, " not": -4.0}


def _constrained(first_text: str, second: dict[str, float]):
    complete = _two_call_decoder((EST, first_text), second)
    judge = TypedHouseJudge(
        tau=0.74, statute_chars=600, decoder=VLLMDecoder(model="m", complete=complete), constrain_fact_id=True
    )
    return judge.judge(REQ), complete


class TestFactIdAsConstrainedChoice:
    """#197: the fact id is a choice over the request's own ids, not free text."""

    def test_default_is_unchanged_single_call(self) -> None:
        complete = _decoder_transport(EST, "established F1")
        TypedHouseJudge(tau=0.74, statute_chars=600, decoder=VLLMDecoder(model="m", complete=complete)).judge(REQ)
        assert len(complete.calls) == 1  # type: ignore[attr-defined]

    def test_choice_agrees_with_greedy_id(self) -> None:
        j, complete = _constrained("established F2", {" F1": -3.0, " F2": -0.1})
        assert (j.status, j.fact_id, j.quote_source) == ("established", "F2", "whole_fact")
        assert len(complete.calls) == 2  # type: ignore[attr-defined]
        prompt, max_tokens, temperature, _ = complete.calls[1]  # type: ignore[attr-defined]
        assert prompt == build_house_prompt(REQ, statute_chars=600) + "established"
        assert (max_tokens, temperature) == (len("F2") + 1, 0.0)

    def test_greedy_id_outside_the_request_ids_fails_closed(self) -> None:
        j, _ = _constrained("established F9", {" F1": -3.0, " F2": -0.1})
        assert (j.status, j.fact_id, j.quote, j.quote_source) == ("established", None, None, None)

    def test_greedy_and_choice_disagree_fails_closed(self) -> None:
        j, _ = _constrained("established F1", {" F1": -3.0, " F2": -0.1})
        assert (j.fact_id, j.quote, j.quote_source) == (None, None, None)

    def test_f_narrative_is_never_a_candidate(self) -> None:
        req = JudgeRequest(
            REQ.contract_id, REQ.element, False, REQ.statute, REQ.narrative, (*REQ.facts, ("F_narrative", "TOY: narrative.")),
        )
        complete = _two_call_decoder((EST, "established F1"), {" F_narrative": -0.01, " F1": -2.0, " F2": -3.0})
        j = TypedHouseJudge(
            tau=0.74, statute_chars=600, decoder=VLLMDecoder(model="m", complete=complete), constrain_fact_id=True
        ).judge(req)
        assert (j.fact_id, j.quote_source) == ("F1", "whole_fact")

    def test_ambiguous_prefix_token_alone_is_no_evidence_and_fails_closed(self) -> None:
        req = JudgeRequest(
            REQ.contract_id, REQ.element, False, REQ.statute, REQ.narrative, (("F1", "TOY a."), ("F10", "TOY b.")),
        )
        complete = _two_call_decoder((EST, "established F1"), {" F": -0.01})
        j = TypedHouseJudge(
            tau=0.74, statute_chars=600, decoder=VLLMDecoder(model="m", complete=complete), constrain_fact_id=True
        ).judge(req)
        assert (j.fact_id, j.quote, j.quote_source) == (None, None, None)

    def test_not_established_makes_no_second_call(self) -> None:
        complete = _two_call_decoder(({" established": -3.0, " not": -0.05}, "not"), {" F1": -0.1})
        j = TypedHouseJudge(
            tau=0.74, statute_chars=600, decoder=VLLMDecoder(model="m", complete=complete), constrain_fact_id=True
        ).judge(REQ)
        assert j.status == "not_established" and len(complete.calls) == 1  # type: ignore[attr-defined]


def _walk(req: JudgeRequest, greedy: str, steps: list[dict[str, float]]):
    complete = _two_call_decoder((EST, greedy), {})
    inner = complete

    def wrapped(prompt: str, *, max_tokens: int, temperature: float, logprobs: int | None) -> CompletionResult:
        if len(inner.calls) == 1:  # type: ignore[attr-defined]
            inner.calls.append((prompt, max_tokens, temperature, logprobs))  # type: ignore[attr-defined]
            return CompletionResult(text=" F", model="m", top_logprobs=steps, wall_s=0.1)
        return inner(prompt, max_tokens=max_tokens, temperature=temperature, logprobs=logprobs)

    return TypedHouseJudge(
        tau=0.74, statute_chars=600, decoder=VLLMDecoder(model="m", complete=wrapped), constrain_fact_id=True
    ).judge(req)


class TestFactIdAcrossTokens:
    """The live 4B splits ids: ` F` then `2` then `:`."""

    def test_split_id_chain_resolves(self) -> None:
        steps = [{" F": -0.0004, "<|im_end|>": -8.5}, {"2": -0.0005, "1": -7.6}, {":": -0.002, ":F": -6.0}]
        j = _walk(REQ, "established F2:0:101", steps)
        assert (j.fact_id, j.quote_source) == ("F2", "whole_fact")

    def test_walk_restricted_to_candidates_overrides_a_non_candidate_argmax(self) -> None:
        # Global argmax at step 1 is "9" (not a candidate); the walk takes the best allowed token, "2", which
        # then disagrees with a greedy "F1" -> fail closed.
        steps = [{" F": -0.0004}, {"9": -0.01, "2": -2.0, "1": -5.0}, {":": -0.01}]
        j = _walk(REQ, "established F1", steps)
        assert (j.fact_id, j.quote_source) == (None, None)

    def test_f1_versus_f10_is_decided_by_the_continuation(self) -> None:
        req = JudgeRequest(REQ.contract_id, REQ.element, False, REQ.statute, REQ.narrative,
                           (("F1", "TOY a."), ("F10", "TOY b.")))
        ends = [{" F": -0.0}, {"1": -0.0}, {":": -0.1, "0": -3.0}]
        cont = [{" F": -0.0}, {"1": -0.0}, {"0": -0.1, ":": -3.0}, {":": -0.1}]
        assert _walk(req, "established F1:0:5", ends).fact_id == "F1"
        assert _walk(req, "established F10:0:5", cont).fact_id == "F10"
        assert _walk(req, "established F1:0:5", cont).fact_id is None
