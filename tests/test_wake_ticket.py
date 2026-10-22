"""#1038 PR-4: the wake ticket (pure functions): bound to the user, the mode and the expiry; anything else is no ticket."""

from __future__ import annotations

import pytest

from pravrudhi.application import wake_ticket as T

S = "test-secret-not-a-real-key"
NOW = 2_000_000_000.0


def clock() -> float:
    return NOW


def test_a_ticket_verifies_for_its_user_and_mode_only() -> None:
    t = T.issue(S, "U-1", "partial", 600, clock=clock)
    assert T.verify(S, t, "u-1", clock=clock) == "partial"  # the user id is compared case-insensitively
    assert T.verify(S, T.issue(S, "u-1", "full", 600, clock=clock), "u-1", clock=clock) == "full"
    assert T.verify(S, t, "u-2", clock=clock) is None
    assert T.verify(S + "x", t, "u-1", clock=clock) is None


def test_the_mode_and_the_expiry_are_signed() -> None:
    t = T.issue(S, "u-1", "partial", 600, clock=clock)
    assert T.verify(S, t.replace(".partial.", ".full."), "u-1", clock=clock) is None  # a partial cannot be upgraded or swapped
    exp = t.split(".")[1]
    assert T.verify(S, t.replace(exp, str(int(exp) + 1000), 1), "u-1", clock=clock) is None  # nor lengthened


def test_an_expired_ticket_is_no_ticket() -> None:
    t = T.issue(S, "u-1", "partial", 600, clock=clock)
    assert T.verify(S, t, "u-1", clock=lambda: NOW + 600) == "partial"
    assert T.verify(S, t, "u-1", clock=lambda: NOW + 601) is None


@pytest.mark.parametrize(
    "bad",
    [None, "", "v1", "v1.1.partial", "v2." + "0" * 20, "x" * 500, "v1.99999999999999.partial." + "0" * 64,
     "v1.1.other." + "0" * 64, "v1.1.partial." + "G" * 64],
)
def test_a_malformed_value_is_no_ticket(bad: str | None) -> None:
    assert T.verify(S, bad, "u-1", clock=clock) is None


def test_no_user_is_no_ticket_and_the_key_is_not_the_waker_key() -> None:
    t = T.issue(S, "u-1", "full", 600, clock=clock)
    assert T.verify(S, t, None, clock=clock) is None and T.verify(S, t, "  ", clock=clock) is None
    assert T._key(S) != S.encode()  # derived: the waker's own signing key is never the ticket key
    with pytest.raises(ValueError):
        T.issue(S, "u-1", "other", 600, clock=clock)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        T.issue(S, " ", "full", 600, clock=clock)
