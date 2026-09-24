"""HouseJudge re-expressed through the typed layer (T1, docs/decisions/TYPED-LAYER-PLAN-2026-09-24.md).

`TypedHouseJudge` reuses `nyaya_judges.build_house_prompt` and `nyaya_judges.parse_house_fact_id` verbatim
(imported, never duplicated) -- the training prompt and the fact-id parsing are unchanged. The one thing that
actually goes through the NEW typed-layer machinery is the established/not_established decision itself:
`decoder.score_decision` over a `bool_field`, instead of `nyaya_judges.p_established_from_top_logprobs`
directly. `nyaya_judges.py` is not imported *into* by this module in the other direction, and is not edited
at all -- `HouseJudge`'s default runtime path is unchanged by this file's existence.

Whether the two actually agree is an empirical question, not assumed from the algebra being equivalent (see
test_typed_house_judge.py for the unit-test-level check, and scripts/typed_layer_parity.py for the live,
GPU-level check against the 279-prompt calibration/heldout set that is T1's real pass bar).
"""

from __future__ import annotations

from pravrudhi.application.nyaya_judges import (
    ElementJudgment,
    JudgeOutputError,
    JudgeRequest,
    build_house_prompt,
    parse_house_fact_id,
)
from pravrudhi.application.typed.decoder import DecodeError, TypedDecoder, score_decision
from pravrudhi.application.typed.schema import bool_field

#: The exact same two options and token surface-form variants nyaya_judges._EST_TOKENS/_NOT_TOKENS use.
_STATUS_FIELD = bool_field("status", true_tokens=(" established", "established"), false_tokens=(" not", "not"))


class TypedHouseJudge:
    """The house element judge, re-expressed through `pravrudhi.application.typed`. Same constructor shape
    as `nyaya_judges.HouseJudge` where it makes sense, but takes a `TypedDecoder` rather than building its
    own transport -- construction (vLLM vs SGLang, live vs injected) is the caller's concern, not this
    class's."""

    name = "house-typed"

    def __init__(
        self,
        *,
        tau: float,
        statute_chars: int,
        decoder: TypedDecoder,
        max_tokens: int = 30,
        top_logprobs: int = 20,
    ) -> None:
        self.tau = tau
        self.statute_chars = statute_chars
        self.decoder = decoder
        self.max_tokens = max_tokens
        self.top_logprobs = top_logprobs

    def judge(self, request: JudgeRequest) -> ElementJudgment:
        prompt = build_house_prompt(request, statute_chars=self.statute_chars)
        res = self.decoder.complete(prompt, max_tokens=self.max_tokens, temperature=0.0, logprobs=self.top_logprobs)
        try:
            scores = score_decision(res, _STATUS_FIELD)
        except DecodeError as e:
            # Same failure class HouseJudge itself raises for the same condition (no evidence either way) --
            # a caller that catches JudgeOutputError around either judge sees the same behaviour.
            raise JudgeOutputError(str(e)) from e
        p = scores["true"]
        if p < self.tau:
            return ElementJudgment("not_established", p, raw=res.text, backend_used=res.backend_index)
        # Fact granularity (nyaya_judges module doc, unchanged here): the claim is "this fact, whole". The
        # fact id itself is parsed from the greedy completion, exactly like HouseJudge -- not scored like a
        # decision field, since which fact was named is not itself a decision this call makes.
        fact_id = parse_house_fact_id(res.text)
        if fact_id is None:
            return ElementJudgment("established", p, raw=res.text, backend_used=res.backend_index)
        text = dict(request.facts).get(fact_id)
        return ElementJudgment(
            "established", p, fact_id, text, "whole_fact" if text is not None else None,
            raw=res.text, backend_used=res.backend_index,
        )
