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

import math

from pravrudhi.application.nyaya_judges import (
    LABEL_MASS_FLOOR,
    ClampKind,
    ElementJudgment,
    JudgeOutputError,
    JudgeRequest,
    build_house_prompt,
    check_prompt_template,
    parse_house_fact_id,
)
from pravrudhi.application.typed.decoder import DecodeError, TypedDecoder, check_label_mass, score_decision
from pravrudhi.application.typed.schema import bool_field

#: The exact same two options and token surface-form variants nyaya_judges._EST_TOKENS/_NOT_TOKENS use.
_STATUS_FIELD = bool_field("status", true_tokens=(" established", "established"), false_tokens=(" not", "not"))


class TypedHouseJudge:
    """The house element judge, re-expressed through `pravrudhi.application.typed`. Same constructor shape
    as `nyaya_judges.HouseJudge` where it makes sense, but takes a `TypedDecoder` rather than building its
    own transport -- construction (vLLM vs SGLang, live vs injected) is the caller's concern, not this
    class's."""

    name = "house-typed"
    deterministic = True  # judge() always decodes at temperature 0.0

    def __init__(
        self,
        *,
        tau: float,
        statute_chars: int,
        decoder: TypedDecoder,
        max_tokens: int = 30,
        top_logprobs: int = 20,
        label_mass_floor: float = LABEL_MASS_FLOOR,
        constrain_fact_id: bool = False,
        prompt_template: str = "legacy",
    ) -> None:
        if not (isinstance(label_mass_floor, (int, float)) and 0.0 <= label_mass_floor <= 1.0):
            raise ValueError(f"label_mass_floor must be a number in [0, 1], got {label_mass_floor!r}")
        self.label_mass_floor = float(label_mass_floor)
        self.tau = tau
        self.prompt_template = check_prompt_template(prompt_template)
        self.statute_chars = statute_chars
        self.decoder = decoder
        self.max_tokens = max_tokens
        self.top_logprobs = top_logprobs
        self.constrain_fact_id = constrain_fact_id

    def judge(self, request: JudgeRequest) -> ElementJudgment:
        prompt = build_house_prompt(request, statute_chars=self.statute_chars, prompt_template=self.prompt_template)
        res = self.decoder.complete(prompt, max_tokens=self.max_tokens, temperature=0.0, logprobs=self.top_logprobs)
        try:
            scores, missing = score_decision(res, _STATUS_FIELD)
        except DecodeError as e:
            # Same failure class HouseJudge itself raises for the same condition (no evidence either way) --
            # a caller that catches JudgeOutputError around either judge sees the same behaviour.
            raise JudgeOutputError(str(e)) from e
        try:
            check_label_mass(res.top_logprobs[0], _STATUS_FIELD, label_mass_floor=self.label_mass_floor)
        except DecodeError as e:
            raise JudgeOutputError(str(e)) from e
        p = scores["true"]
        # Conservative decision rule (2026-09-28, G-28), mirroring HouseJudge.judge exactly: "false"
        # missing means p is a LOWER bound on the true score (analogous to nyaya_judges' "' not' missing"
        # branch) -- established only when the bound itself already clears tau. "true" missing means p is
        # an UPPER bound (analogous to "' established' missing") -- not_established is definite when the
        # bound is already below tau, undetermined otherwise. Neither missing: p is exact, ordinary rule.
        clamp: ClampKind = "lower_bound" if "false" in missing else ("upper_bound" if "true" in missing else "none")
        bound_undetermined = (clamp == "lower_bound" and p < self.tau) or (clamp == "upper_bound" and p >= self.tau)
        if p < self.tau or bound_undetermined:
            return ElementJudgment("not_established", p, raw=res.text, backend_used=res.backend_index,
                                    clamp=clamp, bound_undetermined=bound_undetermined)
        # Fact granularity (nyaya_judges module doc, unchanged here): the claim is "this fact, whole". The
        # fact id itself is parsed from the greedy completion, exactly like HouseJudge -- not scored like a
        # decision field, since which fact was named is not itself a decision this call makes.
        fact_id = parse_house_fact_id(res.text)
        if self.constrain_fact_id and fact_id is not None:
            fact_id = self._constrained_fact_id(prompt, request, fact_id)
        if fact_id is None:
            return ElementJudgment("established", p, raw=res.text, backend_used=res.backend_index, clamp=clamp)
        text = dict(request.facts).get(fact_id)
        return ElementJudgment(
            "established", p, fact_id, text, "whole_fact" if text is not None else None,
            raw=res.text, backend_used=res.backend_index, clamp=clamp,
        )

    def _constrained_fact_id(self, prompt: str, request: JudgeRequest, greedy_id: str) -> str | None:
        """#197: choose the fact id by walking the completion after `established`, at each position taking the
        best-scoring token that keeps the text a prefix of one of the request's own ids (never `F_narrative`),
        instead of trusting the free-text greedy token. Works for ids split across tokens (a live 4B emits
        ` F` then `2`). Once the text equals an id, a token that extends another id (F1 vs F10) must beat
        every non-extending token to continue; otherwise the id ends there. The greedy id is kept only when
        it is a candidate AND the walk reaches the same id; an unknown id, a disagreement, an exhausted
        walk or an empty step returns None, which the caller reports as established with no fact and no
        quote -- fail closed."""
        ids = [fid for fid, _ in request.facts if fid != "F_narrative"]
        if greedy_id not in ids:
            return None
        res = self.decoder.complete(
            prompt + "established", max_tokens=max(map(len, ids)) + 1, temperature=0.0, logprobs=self.top_logprobs
        )
        text = ""
        for top in res.top_logprobs:
            ext: dict[str, float] = {}
            stop = -math.inf
            for token, lp in top.items():
                t = token.strip()
                if t and any(c.startswith(text + t) and c != text for c in ids):
                    ext[t] = max(lp, ext.get(t, -math.inf))
                else:
                    stop = max(stop, lp)
            if text in ids and (not ext or max(ext.values()) <= stop):
                break
            if not ext:
                return None
            text += max(ext, key=lambda k: ext[k])
        return text if text in ids and text == greedy_id else None
