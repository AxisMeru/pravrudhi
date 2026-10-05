"""#133 reproduction (Tag's claim, pending verification): `TypedHouseJudge` drops the label-mass guard.

`nyaya_judges.p_established_from_top_logprobs` (HouseJudge's decision primitive) refuses, with
`JudgeOutputError`, when (a) the top-1 first token is not a label token, or (b) the label tokens' combined
probability mass is below `label_mass_floor` (2026-09-28 production-safety guard, config key
`house_judge.label_mass_floor`). `TypedHouseJudge` (the `typed_layer: true` path) re-expresses the same
decision through `decoder.score_decision`, which has neither check, and its constructor takes no
`label_mass_floor` at all -- so flipping `typed_layer` on silently removes a production safety guard.

All inputs here are CONSTRUCTED (hand-written logprob dictionaries and a toy statute); nothing is real
material. The fake transports are test doubles, never used outside tests.
"""

from __future__ import annotations

import math

import pytest

from pravrudhi.application.nyaya_agent import _build_house_judge
from pravrudhi.application.nyaya_judges import HouseJudge, JudgeOutputError, JudgeRequest
from pravrudhi.application.typed.decoder import VLLMDecoder
from pravrudhi.application.typed.house_judge import TypedHouseJudge
from pravrudhi.models.openai_compat import CompletionResult

REQ = JudgeRequest(
    contract_id="constructed_toy",
    element="dishonestly misappropriates the property",
    is_denial=False,
    statute="CONSTRUCTED: whoever dishonestly misappropriates movable property shall be punished.",
    narrative="CONSTRUCTED: A was given money for supplies and spent it on himself.",
    facts=(("F1", "CONSTRUCTED: A was given 100 units for supplies."), ("F2", "CONSTRUCTED: A spent it on a trip.")),
)
TAU = 0.74

#: A prose completion ("Based on ...") whose own top-k happens to contain both label tokens at tiny mass.
#: p(est | est, not) = 0.988 >= TAU, but the model's greediest token is prose: label mass ~= 0.2025 < 0.5.
PROSE_TOP1 = {"Based": -0.2, " established": -1.6, " not": -6.0}
#: top-1 IS a label token but the two labels together hold only ~0.413 of the mass (< the 0.5 floor).
LOW_MASS_LABEL_TOP1 = {" established": -0.9, "Based": -1.1, " not": -5.0}


def _house(top: dict[str, float], text: str, **kw: float) -> HouseJudge:
    def complete(prompt: str) -> CompletionResult:
        return CompletionResult(text=text, model="m", top_logprobs=[top], wall_s=0.0)

    return HouseJudge(tau=TAU, statute_chars=600, model="m", complete=complete, **kw)


def _typed(top: dict[str, float], text: str, **kw: float) -> TypedHouseJudge:
    def complete(prompt: str, *, max_tokens: int, temperature: float, logprobs: int | None) -> CompletionResult:
        return CompletionResult(text=text, model="m", top_logprobs=[top], wall_s=0.0)

    return TypedHouseJudge(tau=TAU, statute_chars=600, decoder=VLLMDecoder(model="m", complete=complete), **kw)


@pytest.mark.parametrize("top", [PROSE_TOP1, LOW_MASS_LABEL_TOP1], ids=["prose-top1", "low-label-mass"])
def test_house_judge_refuses_but_typed_judge_must_too(top: dict[str, float]) -> None:
    """The control (HouseJudge refuses) plus the #133 assertion (TypedHouseJudge must refuse identically).
    On the default branch the second `raises` fails: TypedHouseJudge returns `established` from prose."""
    with pytest.raises(JudgeOutputError):
        _house(top, "Based on the facts").judge(REQ)
    with pytest.raises(JudgeOutputError):
        _typed(top, "Based on the facts").judge(REQ)


def test_typed_judge_accepts_a_label_mass_floor_and_uses_it() -> None:
    """The constructor must take the floor (HouseJudge's own kwarg name) and a LOWER floor must let the
    low-mass case through, proving the floor is actually read, not just stored."""
    low = _typed(LOW_MASS_LABEL_TOP1, "established F1", label_mass_floor=0.2).judge(REQ)
    assert low.status == "established"
    assert math.isclose(low.p_established, 1.0 / (1.0 + math.exp(-5.0 + 0.9)), rel_tol=1e-12)
    with pytest.raises(JudgeOutputError):
        _typed(LOW_MASS_LABEL_TOP1, "established F1", label_mass_floor=0.5).judge(REQ)


_HJ_CFG = {
    "base_url": "http://127.0.0.1:1/v1", "model": "m", "statute_chars": 600, "max_tokens": 30,
    "top_logprobs": 20, "timeout_s": 5, "label_mass_floor": 0.37,
}


def test_build_house_judge_threads_label_mass_floor_into_the_typed_slot() -> None:
    """`NyayaAgent.house()` builds the typed slot through `_build_house_judge`; the configured floor must
    arrive on the judge (and so must the second judge's inherited one -- same builder)."""
    judge = _build_house_judge(_HJ_CFG, tau=TAU, typed=True, api_key_env="NYAYA_TEST_UNSET_KEY")
    assert judge.label_mass_floor == 0.37  # type: ignore[attr-defined]


def test_build_house_judge_typed_requires_label_mass_floor_like_from_config() -> None:
    """HouseJudge.from_config uses a bare subscript (R1's review of #124): a config without the safety floor
    is refused, not defaulted. The typed slot must refuse the same config identically."""
    cfg = {k: v for k, v in _HJ_CFG.items() if k != "label_mass_floor"}
    with pytest.raises(KeyError, match="label_mass_floor"):
        HouseJudge.from_config(cfg, tau=TAU, api_key_env="NYAYA_TEST_UNSET_KEY")
    with pytest.raises(KeyError, match="label_mass_floor"):
        _build_house_judge(cfg, tau=TAU, typed=True, api_key_env="NYAYA_TEST_UNSET_KEY")
