"""Anti-abuse limits: each limit below, at and above, and the order and the bounds."""

from __future__ import annotations

import pytest

from pravrudhi.application.a0_assist.limits import DAY_S, AssistLimiter

T0 = 10 * DAY_S + 100.0


class Clock:
    def __init__(self, t: float = T0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


def mk(u=3, o=5, i=2, clock=None, **kw):
    clock = clock or Clock()
    return AssistLimiter(u, o, i, now=clock, **kw), clock


def test_user_limit_below_at_above():
    lim, c = mk(u=3, o=99, i=99)
    ips = iter(f"1.1.1.{n}" for n in range(50))
    assert [lim.admit("u1", "o1", next(ips)) for _ in range(3)] == [None, None, None]  # 1, 2, 3 admitted (at the limit)
    r = lim.admit("u1", "o1", next(ips))
    assert r is not None and r.reason == "per_user_day" and r.retry_after_s > 0


def test_org_limit_below_at_above_across_users():
    lim, _ = mk(u=99, o=3, i=99)
    for n in range(3):
        assert lim.admit(f"u{n}", "org", f"2.2.2.{n}") is None
    assert lim.admit("u9", "org", "2.2.2.9").reason == "per_org_day"
    assert lim.admit("u9", "other-org", "2.2.2.9") is None  # another org is untouched


def test_ip_limit_below_at_above_and_window_slides():
    lim, c = mk(u=99, o=99, i=2)
    assert lim.admit("a", "o", "9.9.9.9") is None and lim.admit("b", "o", "9.9.9.9") is None
    r = lim.admit("c", "o", "9.9.9.9")
    assert r.reason == "per_ip_minute" and 1 <= r.retry_after_s <= 61
    c.t += 59.9
    assert lim.admit("c", "o", "9.9.9.9").reason == "per_ip_minute"  # still inside the minute
    c.t += 0.2
    assert lim.admit("c", "o", "9.9.9.9") is None  # the first run has aged out


def test_other_ip_is_independent():
    lim, _ = mk(u=99, o=99, i=1)
    assert lim.admit("a", "o", "1.1.1.1") is None and lim.admit("a", "o", "1.1.1.2") is None


def test_day_rolls_over_at_utc_midnight():
    lim, c = mk(u=1, o=99, i=99)
    c.t = 5 * DAY_S + DAY_S - 1
    assert lim.admit("u", "o", "1.1.1.1") is None and lim.admit("u", "o", "1.1.1.2").reason == "per_user_day"
    c.t = 6 * DAY_S
    assert lim.admit("u", "o", "1.1.1.3") is None  # a new UTC day


def test_a_refusal_counts_nothing():
    lim, _ = mk(u=1, o=99, i=1)
    assert lim.admit("u", "o", "1.1.1.1") is None
    for _ in range(5):
        assert lim.admit("u", "o", "1.1.1.1").reason == "per_ip_minute"  # refused by IP: the user's count stays 1
    lim2, _ = mk(u=1, o=2, i=99)
    assert lim2.admit("u", "o", "1.1.1.1") is None and lim2.admit("u", "o", "1.1.1.2").reason == "per_user_day"
    assert lim2.admit("v", "o", "1.1.1.3") is None  # the org count is 2 now, not 3: the refused run did not count
    assert lim2.admit("w", "o", "1.1.1.4").reason == "per_org_day"


@pytest.mark.parametrize("args", [(None, "o", "1.1.1.1"), ("", "o", "1.1.1.1"), (None, None, None)])
def test_a_missing_user_is_refused_never_admitted(args):
    lim, _ = mk()
    r = lim.admit(*args)
    assert r is not None and r.reason == "no_identity"


def test_a_workspace_user_with_no_org_is_admitted_and_only_the_user_and_ip_limits_apply():
    lim, _ = mk()
    for _ in range(lim.per_user_day):
        assert lim.admit("u", None, f"9.9.9.{_}") is None
    assert lim.admit("u", None, "9.9.9.200").reason == "per_user_day"
    assert not any(k.startswith("o:") for k in lim._day)


def test_a_missing_ip_skips_only_the_per_ip_limit():
    lim, _ = mk()
    for _ in range(lim.per_ip_minute + 2):
        assert lim.admit(f"u{_}", f"o{_}", None) is None
    assert lim._ip == {} or all(not q for q in lim._ip.values())


@pytest.mark.parametrize("bad", [0, -1, True, 1.5, "3", None])
def test_limits_must_be_positive_integers(bad):
    with pytest.raises(ValueError):
        AssistLimiter(bad, 1, 1)
    with pytest.raises(ValueError):
        AssistLimiter(1, bad, 1)
    with pytest.raises(ValueError):
        AssistLimiter(1, 1, bad)


def test_tables_are_bounded_and_a_new_user_is_never_locked_out():
    lim, _ = mk(u=99, o=99, i=99, max_keys=5)
    for n in range(50):
        assert lim.admit(f"u{n}", f"o{n}", f"3.3.3.{n}") is None
    assert len(lim._day) <= 5 and len(lim._ip) <= 5
    assert lim.admit("fresh", "fresh-org", "4.4.4.4") is None


def test_concurrent_callers_never_exceed_the_user_limit():
    import threading

    lim, _ = mk(u=10, o=1000, i=1000)
    out = []

    def go(n):
        out.append(lim.admit("same", "o", f"5.5.{n // 250}.{n % 250}"))

    ts = [threading.Thread(target=go, args=(n,)) for n in range(40)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sum(1 for r in out if r is None) == 10
