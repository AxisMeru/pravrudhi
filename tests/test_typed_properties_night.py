"""Hypothesis property tests over the typed layer, night of 2026-09-30 (Tag's claims, pending verification).

CONSTRUCTED inputs only: logprob dictionaries are generated, the statute/facts are toy strings. `derandomize`
is on so a CI run is reproducible; raise `max_examples` locally to hunt. Properties:

  P1 score_decision: scores in [0,1], sum to 1, invariant to dict order, `missing` is exactly the options with
     no token present.
  P2 bound soundness (the G-28 fix): truncating a real full distribution to its top-k, a reported
     lower_bound never exceeds and an upper_bound never undershoots the exact two-way softmax.
  P3 parity: HouseJudge and TypedHouseJudge agree on every generated first-token distribution (same
     status/p/clamp/bound_undetermined/fact, or BOTH raise JudgeOutputError) -- the #133 regression guard.
  P4 the label-mass guard is monotone in its floor, and never accepts a prose top-1.
  P5 lowering tau can only turn not_established into established, never the reverse.
  P6 validate_id_ref accepts exactly the candidates (unicode included), never a nearest match.
"""

from __future__ import annotations

import math

import pytest

hypothesis = pytest.importorskip("hypothesis")
from hypothesis import given, settings  # noqa: E402
from hypothesis import strategies as st  # noqa: E402

from pravrudhi.application.nyaya_judges import HouseJudge, JudgeOutputError, JudgeRequest  # noqa: E402
from pravrudhi.application.typed.decoder import DecodeError, VLLMDecoder, check_label_mass, score_decision  # noqa: E402
from pravrudhi.application.typed.house_judge import _STATUS_FIELD, TypedHouseJudge  # noqa: E402
from pravrudhi.application.typed.schema import Field, FieldKind  # noqa: E402
from pravrudhi.application.typed.validators import IdRefNotKnown, validate_id_ref  # noqa: E402
from pravrudhi.models.openai_compat import CompletionResult  # noqa: E402

PROFILE = settings(max_examples=400, deadline=None, derandomize=True)
EST, NOT = (" established", "established"), (" not", "not")
PROSE = ("Based", " The", " Yes", "\n", " est", " nothing", "Not", " Established")  # none is a label token
REQ = JudgeRequest("c", "an element", False, "CONSTRUCTED statute", "CONSTRUCTED narrative",
                   (("F1", "CONSTRUCTED fact one."), ("F2", "CONSTRUCTED fact two.")))

logprob = st.floats(min_value=-30.0, max_value=0.0, allow_nan=False, allow_infinity=False)


@st.composite
def first_token_tops(draw: st.DrawFn) -> dict[str, float]:
    """A first-token top-k: any subset of the four label variants plus any subset of prose tokens, with
    arbitrary (<= 0, finite) logprobs. May contain no label token at all (the decoder must then raise)."""
    tokens = draw(st.lists(st.sampled_from([*EST, *NOT, *PROSE]), min_size=1, max_size=8, unique=True))
    return {t: draw(logprob) for t in tokens}


def _house(top: dict[str, float], text: str, tau: float) -> HouseJudge:
    return HouseJudge(tau=tau, statute_chars=600, model="m",
                      complete=lambda prompt: CompletionResult(text=text, model="m", top_logprobs=[top], wall_s=0.0))


def _typed(top: dict[str, float], text: str, tau: float) -> TypedHouseJudge:
    def complete(prompt: str, *, max_tokens: int, temperature: float, logprobs: int | None) -> CompletionResult:
        return CompletionResult(text=text, model="m", top_logprobs=[top], wall_s=0.0)

    return TypedHouseJudge(tau=tau, statute_chars=600, decoder=VLLMDecoder(model="m", complete=complete))


def _result(top: dict[str, float]) -> CompletionResult:
    return CompletionResult(text="", model="m", top_logprobs=[top], wall_s=0.0)


@PROFILE
@given(top=first_token_tops())
def test_p1_score_decision_invariants(top: dict[str, float]) -> None:
    try:
        scores, missing = score_decision(_result(top), _STATUS_FIELD)
    except DecodeError:
        assert not any(t in top for t in (*EST, *NOT))  # refuses exactly when no label token is present
        return
    assert set(scores) == {"true", "false"}
    assert all(0.0 <= v <= 1.0 and math.isfinite(v) for v in scores.values())
    assert math.isclose(sum(scores.values()), 1.0, abs_tol=1e-9)
    assert ("true" in missing) == (not any(t in top for t in EST))
    assert ("false" in missing) == (not any(t in top for t in NOT))
    shuffled = dict(reversed(list(top.items())))
    assert score_decision(_result(shuffled), _STATUS_FIELD) == (scores, missing)


@PROFILE
@given(
    probs=st.lists(st.floats(min_value=1e-6, max_value=1.0), min_size=6, max_size=12),
    k=st.integers(min_value=2, max_value=6),
)
def test_p2_bounds_are_sound_against_the_full_distribution(probs: list[float], k: int) -> None:
    """Vocabulary = ' established', ' not' and other tokens, normalised to a true distribution; the server
    reports only its top-k. The exact two-way p needs both label logprobs; the reported (bounded) p must be
    on the safe side of it."""
    total = sum(probs)
    vocab = {"\x00est": probs[0] / total, "\x00not": probs[1] / total,
             **{f"o{i}": p / total for i, p in enumerate(probs[2:])}}
    est_key, not_key = "\x00est", "\x00not"
    exact = vocab[est_key] / (vocab[est_key] + vocab[not_key])
    top_k = dict(sorted(vocab.items(), key=lambda kv: -kv[1])[:k])
    top = {(" established" if name == est_key else " not" if name == not_key else name): math.log(p)
           for name, p in top_k.items()}
    if not any(t in top for t in (*EST, *NOT)):
        return
    scores, missing = score_decision(_result(top), _STATUS_FIELD)
    p = scores["true"]
    if "false" in missing:
        assert p <= exact + 1e-9, "lower bound exceeded the exact value"
    elif "true" in missing:
        assert p >= exact - 1e-9, "upper bound undershot the exact value"
    else:
        assert math.isclose(p, exact, rel_tol=1e-9, abs_tol=1e-12)


@PROFILE
@given(top=first_token_tops(), tau=st.floats(min_value=0.05, max_value=0.95), text=st.sampled_from(["established F1", "established", "not", "Based on"]))
def test_p3_house_and_typed_judges_agree(top: dict[str, float], tau: float, text: str) -> None:
    """The #133 regression guard: same JudgeRequest, same server reply, identical outcome -- including
    identical refusals."""
    outcomes = []
    for build in (_house, _typed):
        try:
            j = build(top, text, tau).judge(REQ)
            outcomes.append((j.status, j.p_established, j.fact_id, j.quote, j.clamp, j.bound_undetermined))
        except JudgeOutputError:
            outcomes.append("refused")
    assert outcomes[0] == outcomes[1] or (
        isinstance(outcomes[0], tuple) and isinstance(outcomes[1], tuple)
        and outcomes[0][0] == outcomes[1][0] and math.isclose(outcomes[0][1], outcomes[1][1], abs_tol=1e-9)
        and outcomes[0][2:] == outcomes[1][2:]
    ), f"house={outcomes[0]} typed={outcomes[1]} top={top}"


@PROFILE
@given(top=first_token_tops(), lo=st.floats(0.0, 1.0), hi=st.floats(0.0, 1.0))
def test_p4_label_mass_guard_is_monotone_and_never_accepts_prose_top1(top: dict[str, float], lo: float, hi: float) -> None:
    lo, hi = min(lo, hi), max(lo, hi)

    def accepts(floor: float) -> bool:
        try:
            check_label_mass(top, _STATUS_FIELD, label_mass_floor=floor)
            return True
        except DecodeError:
            return False

    if accepts(hi):
        assert accepts(lo), "raising the floor accepted something a lower floor refused"
    top1 = max(top, key=lambda t: top[t])
    if top1 not in (*EST, *NOT):
        assert not accepts(0.0), "a prose top-1 was accepted even at floor 0"


@PROFILE
@given(top=first_token_tops(), t1=st.floats(0.05, 0.95), t2=st.floats(0.05, 0.95))
def test_p5_lowering_tau_never_unestablishes(top: dict[str, float], t1: float, t2: float) -> None:
    lo, hi = min(t1, t2), max(t1, t2)
    try:
        strict = _typed(top, "established F1", hi).judge(REQ)
        loose = _typed(top, "established F1", lo).judge(REQ)
    except JudgeOutputError:
        return
    if strict.status == "established":
        assert loose.status == "established"


@PROFILE
@given(candidates=st.lists(st.text(min_size=1, max_size=12), min_size=1, max_size=6, unique=True), value=st.text(max_size=12))
def test_p6_validate_id_ref_is_exact_membership(candidates: list[str], value: str) -> None:
    field = Field(name="fact", kind=FieldKind.ID_REF, candidates=tuple(candidates))
    if value in candidates:
        assert validate_id_ref(field, value) == value
    else:
        with pytest.raises(IdRefNotKnown):
            validate_id_ref(field, value)
    assert validate_id_ref(field, None) is None
