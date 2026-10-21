"""#1038 PR-1: the pure cold-start core: config parsing, the per-judge state machine, the signing vectors and HttpWaker against a
fake waker server. No RunPod, no network beyond 127.0.0.1."""

from __future__ import annotations

import logging

import pytest

from pravrudhi.application import judge_wake as W
from tests.judge_wake_fakes import FakeHealthServer, FakeWaker

GOOD = {"cold_start_wake": True, "cold_start_waker_url": "https://waker.example.test/wake"}
SECRET = "test-secret-not-a-real-key"


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def cfg(**kw: object) -> W.WakeConfig:
    parsed = W.parse_cold_start_config({**GOOD, **kw})
    assert parsed is not None
    return parsed


# ---- config ---------------------------------------------------------------------------------------------------------------


def test_the_defaults_and_the_flag() -> None:
    c = cfg()
    assert (c.budget_s, c.poll_after_s, c.deadline_margin_s, c.waker_timeout_s) == (300.0, 5, 30.0, 5.0)
    assert c.waker_secret_env == "PRAVRUDHI_WAKER_SECRET" and c.waker_url == GOOD["cold_start_waker_url"]
    for off in (None, {}, {"cold_start_wake": False}, {"cold_start_wake": "true"}, {"cold_start_wake": 1}, [], "x"):
        assert W.parse_cold_start_config(off) is None  # only the boolean true turns it on


@pytest.mark.parametrize(
    "bad",
    [
        {"cold_start_budget_s": 29}, {"cold_start_budget_s": 421}, {"cold_start_budget_s": True}, {"cold_start_budget_s": "300"},
        {"cold_start_poll_after_s": 0}, {"cold_start_poll_after_s": 61}, {"cold_start_deadline_margin_s": -1},
        {"cold_start_deadline_margin_s": 301}, {"cold_start_waker_timeout_s": 0}, {"cold_start_waker_timeout_s": 31},
        {"cold_start_wake_second": "lazy"}, {"cold_start_wake_second": "both"},
        {"cold_start_waker_secret_env": "lower"}, {"cold_start_waker_secret_env": "A"}, {"cold_start_waker_secret_env": 5},
        {"cold_start_waker_url": ""}, {"cold_start_waker_url": None}, {"cold_start_waker_url": "http://waker.example.test/wake"},
        {"cold_start_waker_url": "https://waker.example.test/other"}, {"cold_start_waker_url": "https://waker.example.test/wake?x=1"},
        {"cold_start_waker_url": "https://user@waker.example.test/wake"}, {"cold_start_waker_url": "https:///wake"},
    ],
)
def test_one_bad_value_keeps_it_off(bad: dict[str, object]) -> None:
    assert W.parse_cold_start_config({**GOOD, **bad}) is None


# ---- the state machine ----------------------------------------------------------------------------------------------------


def test_both_judges_wake_and_become_ready() -> None:
    clock = Clock()
    w = W.JudgeWake(cfg(), clock=clock)
    assert w.start({"first": "parked", "second": "parked"}) == ["first", "second"]
    assert w.state() == "waking" and not w.terminal
    w.waker_answered("first", "granted")
    w.waker_answered("second", "already_awake")
    assert [m["state"] for m in w.block()["models"]] == ["starting", "starting"]
    w.health("first", "ready")
    assert w.state() == "waking"
    w.health("second", "ready")
    assert w.state() == "ready" and w.terminal and w.reason() is None


def test_a_judge_that_is_already_ready_is_not_woken() -> None:
    w = W.JudgeWake(cfg(), clock=Clock())
    assert w.start({"first": "ready", "second": "parked"}) == ["second"]
    w.waker_answered("second", "granted")
    w.health("second", "ready")
    assert w.state() == "ready"


def test_unknown_warming_and_parked_readings_never_mark_ready() -> None:
    w = W.JudgeWake(cfg(), clock=Clock())
    w.start({"first": "parked", "second": "parked"})
    w.waker_answered("first", "granted")
    for reading in ("unknown", "warming", "parked", "garbage", ""):
        w.health("first", reading)
        assert w.block()["models"][0]["state"] == "starting"
    w.health("second", "ready")  # a judge that was never granted cannot become ready
    assert w.block()["models"][1]["state"] == "waiting"


def test_the_budget_fails_what_is_still_waiting_or_starting() -> None:
    clock = Clock()
    w = W.JudgeWake(cfg(cold_start_budget_s=100), clock=clock)
    w.start({"first": "parked", "second": "parked"})
    w.waker_answered("first", "granted")
    w.waker_answered("second", "granted")
    w.health("second", "ready")
    clock.t += 99.9
    w.tick()
    assert w.state() == "waking"
    clock.t += 0.2
    w.tick()
    assert (w.state(), w.reason()) == ("timeout", "start_timeout")  # the first never came
    assert [m["state"] for m in w.block()["models"]] == ["failed", "ready"]


def test_the_hard_maximum_caps_the_budget() -> None:
    clock = Clock()
    w = W.JudgeWake(cfg(cold_start_budget_s=420), clock=clock)
    w.start({"first": "parked", "second": "parked"})
    w.waker_answered("first", "granted")
    w.waker_answered("second", "granted")
    clock.t += 419
    w.tick()
    assert w.state() == "waking"
    clock.t += 2
    w.tick()
    assert w.state() == "timeout" and w.block()["deadline_s"] == 450


def test_the_four_b_ready_and_the_second_failed_is_partial() -> None:
    w = W.JudgeWake(cfg(), clock=Clock())
    w.start({"first": "parked", "second": "parked"})
    w.waker_answered("first", "granted")
    w.waker_answered("second", "refused")
    assert w.state() == "waking"  # the first is still starting
    w.health("first", "ready")
    assert (w.state(), w.reason()) == ("partial", "second_judge_unavailable")


def test_a_timed_out_second_with_the_first_ready_is_partial_not_ready() -> None:
    clock = Clock()
    w = W.JudgeWake(cfg(), clock=clock)
    w.start({"first": "ready", "second": "parked"})
    w.waker_answered("second", "granted")
    clock.t += 301
    w.tick()
    assert (w.state(), w.reason()) == ("partial", "second_judge_unavailable")


def test_a_refused_first_is_failed_and_both_refused_is_failed() -> None:
    w = W.JudgeWake(cfg(), clock=Clock())
    w.start({"first": "parked", "second": "parked"})
    w.waker_answered("first", "refused")
    w.waker_answered("second", "refused")
    assert (w.state(), w.reason()) == ("failed", "first_judge_unavailable")
    only_second = W.JudgeWake(cfg(), clock=Clock())
    only_second.start({"first": "parked", "second": "parked"})
    only_second.waker_answered("first", "granted")
    only_second.waker_answered("second", "granted")
    only_second.health("second", "ready")
    assert only_second.state() == "waking"  # the first alone decides: nothing is scored without it


def test_unknown_roles_and_late_answers_change_nothing() -> None:
    w = W.JudgeWake(cfg(), clock=Clock())
    w.start({"first": "ready", "second": "ready"})
    w.waker_answered("first", "refused")  # already ready: ignored
    w.waker_answered("third", "granted")
    w.health("third", "ready")
    assert w.state() == "ready"


def test_the_block_is_coarse_only() -> None:
    w = W.JudgeWake(cfg(), clock=Clock())
    w.start({"first": "parked", "second": "parked"})
    b = w.block()
    assert set(b) == {"state", "models", "reason", "deadline_s"}
    assert all(set(m) == {"role", "state", "progress"} and m["progress"] is None for m in b["models"])
    assert b["deadline_s"] == 330 and [m["role"] for m in b["models"]] == ["first", "second"]


# ---- signing --------------------------------------------------------------------------------------------------------------


def test_the_contract_vectors() -> None:
    assert W.canonical(1761000000, "00112233445566778899aabbccddeeff", "second", "wake") == (
        "v1|POST|/wake|1761000000|00112233445566778899aabbccddeeff|second|wake"
    )
    assert W.sign(SECRET, 1761000000, "00112233445566778899aabbccddeeff", "second", "wake") == (
        "509c66e15bee3f648f44a16b55ec51dadb8a97f2fe1de549f3624745e63e09ee"
    )
    assert W.sign(SECRET, 1761000060, "ffeeddccbbaa99887766554433221100", "first", "wake") == (
        "843f90925ade66d54f767f2a7f7fdfd92ebf7f5d3ca5cd543c415ef0a8fdeb87"
    )
    assert W.sign(SECRET, 1761000300, "0123456789abcdef0123456789abcdef", "second", "extend") == (
        "c068821b19fa4cac136490d86fd8c2c923bcd8fb99449c32cc2f9176fb93b5e7"
    )
    with_ref = W.canonical(1761000300, "0123456789abcdef0123456789abcdef", "second", "extend", "lease-x")
    assert with_ref.endswith("|second|extend|lease-x")


# ---- HttpWaker against the fake waker --------------------------------------------------------------------------------------


def waker(url: str, **kw: object) -> W.HttpWaker:
    return W.HttpWaker(url, SECRET, 2.0, **kw)  # type: ignore[arg-type]


def test_wake_extend_and_idempotence() -> None:
    with FakeWaker() as fw:
        hw = waker(fw.url())
        assert hw.wake("first") == W.WakerAnswer("granted", "lease-first")
        assert hw.wake("first") == W.WakerAnswer("already_awake", "lease-first")
        assert hw.extend("first", "lease-first") == W.WakerAnswer("already_awake", "lease-first")
        assert [c[0] for c in fw.calls] == ["ok", "ok", "ok"] and fw.calls[2][2] == "extend"


def test_every_failure_is_refused_and_nothing_raises() -> None:
    with FakeWaker() as fw:
        for mode in ("refuse", "garbage", "server_error"):
            fw.mode = mode
            assert waker(fw.url()).wake("second").state == "refused"
        fw.mode = "ok"
        assert W.HttpWaker(fw.url(), "wrong-secret", 2.0).wake("second").state == "refused"  # 401
        assert waker(fw.url(), clock=lambda: 1.0).wake("second").state == "refused"  # a stale timestamp: 401
        assert waker(fw.url()).wake("third").state == "refused"  # not a label: never sent
        assert [c[0] for c in fw.calls].count("unauthorised") == 2
    assert waker("https://127.0.0.1:1/wake").wake("first").state == "refused"  # connection refused / not listening


def test_a_slow_waker_times_out_as_refused() -> None:
    with FakeWaker() as fw:
        fw.mode, fw.delay_s = "slow", 1.5
        hw = W.HttpWaker(fw.url(), SECRET, 0.3)
        assert hw.wake("first").state == "refused"


def test_a_replayed_nonce_is_refused() -> None:
    with FakeWaker() as fw:
        hw = waker(fw.url(), nonce=lambda: "ab" * 16)
        assert hw.wake("first").state == "granted"
        assert hw.wake("first").state == "refused"  # same nonce again: 401
        assert [c[0] for c in fw.calls] == ["ok", "unauthorised"]


def test_the_url_path_must_be_slash_wake() -> None:
    with pytest.raises(ValueError):
        W.HttpWaker("https://waker.example.test/other", SECRET)


def test_the_secret_never_reaches_a_log_or_an_answer(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.DEBUG), FakeWaker() as fw:
        hw = waker(fw.url())
        answer = hw.wake("first")
        fw.mode = "garbage"
        waker(fw.url()).wake("second")
    assert SECRET not in caplog.text and SECRET not in repr(answer)


# ---- the fake health server ------------------------------------------------------------------------------------------------


def test_the_fake_health_server_answers_per_endpoint() -> None:
    import json
    import urllib.request

    with FakeHealthServer() as fh:
        fh.set("aaa", workers={"ready": 1})
        fh.set("bbb")
        got = json.loads(urllib.request.urlopen(fh.url("aaa"), timeout=2).read())
        assert got["workers"]["ready"] == 1 and got["jobs"] == {"inQueue": 0, "inProgress": 0}
        assert json.loads(urllib.request.urlopen(fh.url("bbb"), timeout=2).read())["workers"]["ready"] == 0
        assert fh.requests == ["aaa", "bbb"]


# ---- the packaged config -------------------------------------------------------------------------------------------------


def test_the_packaged_config_ships_the_feature_off_with_valid_defaults() -> None:
    from pathlib import Path

    import yaml

    body = yaml.safe_load((Path(__file__).resolve().parent.parent / "configs" / "partner_api.yaml").read_text(encoding="utf-8"))
    assert body["cold_start_wake"] is False and body["cold_start_waker_url"] == ""
    assert W.parse_cold_start_config(body) is None  # off
    with_url = {**body, "cold_start_wake": True, "cold_start_waker_url": "https://waker.example.test/wake"}
    c = W.parse_cold_start_config(with_url)
    assert c is not None and (c.budget_s, c.poll_after_s, c.deadline_margin_s, c.waker_timeout_s) == (300.0, 5, 30.0, 5.0)
    assert W.parse_cold_start_config({**with_url, "cold_start_waker_url": ""}) is None  # on but no waker: stays off
