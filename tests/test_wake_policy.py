"""#1038 PR-2: the engine-side wake policy: who may wake (never role_of/is_admin), per-user limits, and the request-scoped
breaker for the partial run. Invented ids and addresses."""

from __future__ import annotations

import pytest

from pravrudhi.api.identity import User
from pravrudhi.application import wake_policy as WP
from pravrudhi.application.nyaya_judges import AndGateJudge, SecondJudgeCircuitBreaker
from tests.test_nyaya_and_gate import REQ, _judgment, _StubJudge

A = User(id="user-a", email="a@team.example.test", role="authenticated")
B = User(id="user-b", email="b@team.example.test", role="authenticated")


# ---- the allowlist ---------------------------------------------------------------------------------------------------------


def test_the_allowlist_parses_lowercased_and_empty_means_nobody() -> None:
    assert WP.waker_ids({"PRAVRUDHI_WAKERS": " User-A , b@Team.example.test ,, "}) == {"user-a", "b@team.example.test"}
    assert WP.waker_ids({}) == frozenset() and WP.waker_ids({"PRAVRUDHI_WAKERS": ""}) == frozenset()
    assert WP.waker_ids({"PRAVRUDHI_ADMINS": "user-a"}) == frozenset()  # admins do not confer it


def test_the_gate_matrix() -> None:
    wakers = frozenset({"user-a", "b@team.example.test"})
    assert WP.may_wake(A, auth_enabled=True, wakers=wakers) is True  # by id
    assert WP.may_wake(B, auth_enabled=True, wakers=wakers) is True  # by email, case-insensitive
    assert WP.may_wake(User("USER-A", None, "x"), auth_enabled=True, wakers=wakers) is True
    assert WP.may_wake(None, auth_enabled=True, wakers=wakers) is False  # signed out
    assert WP.may_wake(None, auth_enabled=False, wakers=wakers) is False  # auth disabled and no user (role_of would say ADMIN)
    assert WP.may_wake(A, auth_enabled=False, wakers=wakers) is False  # auth disabled
    assert WP.may_wake(A, auth_enabled=True, wakers=frozenset()) is False  # empty allowlist: nobody
    assert WP.may_wake(User("user-c", "c@team.example.test", "service_role"), auth_enabled=True, wakers=wakers) is False
    assert WP.may_wake(User("   ", "a@team.example.test", "x"), auth_enabled=True, wakers=frozenset({""})) is False
    assert WP.may_wake(object(), auth_enabled=True, wakers=wakers) is False  # an odd object is not allowed, no exception


def test_an_operator_on_the_admin_list_is_not_a_waker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PRAVRUDHI_ADMINS", "user-a")
    monkeypatch.delenv("PRAVRUDHI_WAKERS", raising=False)
    assert WP.may_wake(A, auth_enabled=True, wakers=WP.waker_ids()) is False


def test_a_role_claim_never_grants_it() -> None:
    forged = User(id="user-z", email=None, role="admin")  # whatever the token says
    assert WP.may_wake(forged, auth_enabled=True, wakers=frozenset({"user-a"})) is False


# ---- per-user limits ------------------------------------------------------------------------------------------------------


class Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def test_one_active_wake_per_user_and_it_is_idempotent() -> None:
    lim = WP.WakeLimiter(6, 120.0, clock=Clock())
    assert lim.begin("user-a") == "new"
    assert lim.begin("USER-A") == "existing"  # same user again: the existing wake, not a new one
    assert lim.begin("user-b") == "new"  # other users are independent
    lim.end("user-a", ok=True)
    assert lim.begin("user-a") == "new"


def test_the_daily_cap_counts_every_wake_and_rolls_over_after_a_day() -> None:
    clock = Clock()
    lim = WP.WakeLimiter(3, 0.0, clock=clock)
    for _ in range(3):
        assert lim.begin("u") == "new"
        lim.end("u", ok=True)
        clock.t += 60
    assert lim.begin("u") == "limit"
    clock.t += WP.DAY_S - 180 + 1  # the first start leaves the 24 h window
    assert lim.begin("u") == "new"


def test_a_failed_wake_counts_and_starts_a_cooldown() -> None:
    clock = Clock()
    lim = WP.WakeLimiter(2, 120.0, clock=clock)
    assert lim.begin("u") == "new"
    lim.end("u", ok=False)
    assert lim.begin("u") == "cooldown"  # no retry storm
    clock.t += 119
    assert lim.begin("u") == "cooldown"
    clock.t += 2
    assert lim.begin("u") == "new"  # the failed one still counted: this is the 2nd of 2
    lim.end("u", ok=True)
    clock.t += 1
    assert lim.begin("u") == "limit"


def test_a_success_does_not_start_a_cooldown() -> None:
    lim = WP.WakeLimiter(5, 120.0, clock=Clock())
    assert lim.begin("u") == "new"
    lim.end("u", ok=True)
    assert lim.begin("u") == "new"


# ---- the request-scoped breaker for the partial run ----------------------------------------------------------------------


def test_the_partial_breaker_makes_every_established_element_refer_without_asking_the_second() -> None:
    primary = _StubJudge("house-4b", _judgment("established", 0.999))
    second = _StubJudge("house-32b", _judgment("established", 0.999))
    gate = AndGateJudge(primary, second, tau_primary=0.74, tau_second=0.97, breaker=WP.partial_run_breaker())
    outs = [gate.judge(REQ) for _ in range(3)]
    assert second.calls == 0
    assert all(o.status != "established" and o.vetoed_by == "second" for o in outs)
    assert all(o.second_skip_reason and o.second_skip_reason.startswith("second_unavailable") for o in outs)


def test_a_partial_run_leaves_the_shared_breaker_exactly_unchanged_and_other_requests_unaffected() -> None:
    shared = SecondJudgeCircuitBreaker(ttl_s=60.0)
    partial = WP.partial_run_breaker()
    assert partial is not shared and partial.is_open() is True and shared.is_open() is False
    p1 = _StubJudge("house-4b", _judgment("established", 0.97))
    s1 = _StubJudge("house-32b", _judgment("established", 0.99))
    other = AndGateJudge(p1, s1, tau_primary=0.74, tau_second=0.97, breaker=shared)  # a concurrent warm screening
    p2 = _StubJudge("house-4b", _judgment("established", 0.97))
    s2 = _StubJudge("house-32b", _judgment("established", 0.99))
    mine = AndGateJudge(p2, s2, tau_primary=0.74, tau_second=0.97, breaker=partial)
    assert mine.judge(REQ).status != "established" and s2.calls == 0
    assert other.judge(REQ).status == "established" and s1.calls == 1  # the other request still asks the second
    assert shared.is_open() is False  # untouched by the partial run


def test_the_partial_breaker_is_a_fresh_object_each_time() -> None:
    a, b = WP.partial_run_breaker(), WP.partial_run_breaker()
    assert a is not b and a.is_open() and b.is_open()
