from dataclasses import FrozenInstanceError
from math import comb, inf

import pytest

from pravrudhi.application.discordance import Discordance, discordance


def _scores(wins: int, losses: int, concordant: int = 0) -> tuple[dict[str, int], dict[str, int]]:
    incumbent = {str(i): int(wins <= i < wins + losses) for i in range(wins + losses + concordant)}
    candidate = {str(i): int(i < wins) for i in range(wins + losses + concordant)}
    return incumbent, candidate


def test_worked_pass_rate_difference() -> None:
    incumbent = dict(a=0, b=0, c=1, d=1, e=0, f=0)
    candidate = dict(a=1, b=1, c=0, d=1, e=0, f=1)
    result = discordance(incumbent, candidate)
    assert (result.n, result.concordant, result.wins, result.losses) == (6, 2, 3, 1)
    assert result.delta == pytest.approx(sum(candidate.values()) / 6 - sum(incumbent.values()) / 6)


@pytest.mark.parametrize("incumbent,candidate", [({}, {}), ({"a": 1}, {}), ({}, {"a": 0}), ({"a": 1}, {"b": 0})])
def test_no_shared_items(incumbent: dict[str, int], candidate: dict[str, int]) -> None:
    assert discordance(incumbent, candidate) == Discordance(0, 0, 0, 0, 0.0, 1.0, 0.0, inf)


def test_all_concordant() -> None:
    assert discordance({"a": 1, "b": 0}, {"a": 1, "b": 0}) == Discordance(2, 2, 0, 0, 0.0, 1.0, 0.0, inf)


def test_unshared_ids_ignored_and_inputs_unchanged() -> None:
    incumbent, candidate = _scores(3, 1, 2)
    expected = discordance(incumbent, candidate)
    incumbent["incumbent_only"] = 1
    candidate["candidate_only"] = 0
    before = incumbent.copy(), candidate.copy()
    assert discordance(incumbent, candidate) == expected
    assert (incumbent, candidate) == before


@pytest.mark.parametrize("count", [1, 5, 30])
def test_balanced_discordance(count: int) -> None:
    result = discordance(*_scores(count, count))
    assert result.p_mcnemar == 1.0
    assert result.delta == 0.0
    assert result.or_lower < 1.0 < result.or_upper


def test_large_advantage() -> None:
    result = discordance(*_scores(30, 2))
    assert result.p_mcnemar < 0.000001
    assert result.or_lower > 1.0


@pytest.mark.parametrize("wins,losses,expected", [(5, 0, 0.0625), (0, 5, 0.0625), (4, 1, 0.375), (1, 0, 1.0)])
def test_hand_computable_p_values(wins: int, losses: int, expected: float) -> None:
    assert discordance(*_scores(wins, losses)).p_mcnemar == expected


def test_all_wins_and_all_losses_exact_interval() -> None:
    lower = 0.025 ** (1 / 5)
    wins = discordance(*_scores(5, 0))
    losses = discordance(*_scores(0, 5))
    assert wins.delta == 1.0
    assert wins.or_lower == pytest.approx(lower / (1 - lower))
    assert wins.or_upper == inf
    assert losses.delta == -1.0
    assert losses.or_lower == 0.0
    assert losses.or_upper == pytest.approx((1 - lower) / lower)


def test_interval_inverts_exact_binomial_tails() -> None:
    result = discordance(*_scores(7, 3))
    lower = result.or_lower / (1 + result.or_lower)
    upper = result.or_upper / (1 + result.or_upper)
    assert sum(comb(10, k) * lower**k * (1 - lower) ** (10 - k) for k in range(7, 11)) == pytest.approx(0.025)
    assert sum(comb(10, k) * upper**k * (1 - upper) ** (10 - k) for k in range(8)) == pytest.approx(0.025)


def test_swapping_arms_reverses_effect_and_odds() -> None:
    incumbent, candidate = _scores(8, 2, 4)
    forward = discordance(incumbent, candidate)
    reverse = discordance(candidate, incumbent)
    assert forward.delta == -reverse.delta
    assert forward.p_mcnemar == reverse.p_mcnemar
    assert forward.or_lower == pytest.approx(1 / reverse.or_upper)
    assert forward.or_upper == pytest.approx(1 / reverse.or_lower)


def test_concordant_items_only_dilute_delta() -> None:
    original = discordance(*_scores(5, 1))
    padded = discordance(*_scores(5, 1, 6))
    assert padded.delta == original.delta / 2
    assert (padded.p_mcnemar, padded.or_lower, padded.or_upper) == (original.p_mcnemar, original.or_lower, original.or_upper)


def test_large_counts_do_not_overflow() -> None:
    result = discordance(*_scores(550, 550))
    assert result.p_mcnemar == 1.0
    assert 0 < result.or_lower < 1 < result.or_upper < inf


def test_result_is_frozen() -> None:
    result = discordance({}, {})
    with pytest.raises(FrozenInstanceError):
        result.n = 1


def test_the_screen_row_fields_are_decided_once_for_both_tracks() -> None:
    """One derivation for one decision, because two of them drifted.

    `harness_track` guarded the `discordance` call on the pool's declared kind and `execute` -- the model
    track -- called it unconditionally, so the guard existed in exactly one of the two places that needed it.
    Sealing `iltur-lsi-dev` (ADR-0043) made that reachable: a model-track night on a `set` pool would still
    have raised on its first candidate, at the one call site nobody fixed.

    Asked of the DECLARED kind, not the observed values, so a fractional pool whose items happened to score
    0 or 1 on one rotation cannot be mistaken for a binary one and crash on the next.
    """
    from pravrudhi.application.discordance import discordance_fields

    binary = discordance_fields("choice", {"a": 1, "b": 0}, {"a": 0, "b": 1})
    assert binary["discordance"]["wins"] == 1 and binary["discordance"]["losses"] == 1
    assert binary["discordance_note"] is None

    # The fractional case: the statistic does not apply, and asking for it is what raised.
    fractional = discordance_fields("set", {"a": 1.0}, {"a": 0.667})
    assert fractional["discordance"] is None
    assert "bootstrap" in fractional["discordance_note"] and "ADR-0038" in fractional["discordance_note"]


def test_neither_track_reaches_past_the_shared_helper() -> None:
    """Asserted on the source. A call site added later would be invisible to any behavioural test until a
    `set` night ran on that track, which is exactly how this defect survived: it was latent until a pool of
    the right kind existed, and by then it was a crash in a night rather than a red test."""
    from pathlib import Path

    for name in ("execute.py", "harness_track.py"):
        src = Path(f"src/pravrudhi/application/{name}").read_text()
        assert "discordance_fields(" in src, f"{name} does not use the shared helper"
        assert "asdict(discordance(" not in src, f"{name} still calls discordance directly"
