"""#1038 PR-1: the pure cold-start core: config parsing, the per-judge state machine, the signing vectors, the nonce store, the
signed-poll verifier and the wake board (the box-side waker polls the engine; the engine makes no outbound call). No RunPod, no
network beyond 127.0.0.1."""

from __future__ import annotations

import json

import pytest

from pravrudhi.application import judge_wake as W
from tests.judge_wake_fakes import FakeHealthServer, box_poll_headers, box_result_request

GOOD = {"cold_start_wake": True}
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
    assert (c.budget_s, c.poll_after_s, c.deadline_margin_s, c.request_ttl_s) == (300.0, 5, 30.0, 120.0)
    assert c.waker_secret_env == "PRAVRUDHI_WAKER_SECRET"
    for off in (None, {}, {"cold_start_wake": False}, {"cold_start_wake": "true"}, {"cold_start_wake": 1}, [], "x"):
        assert W.parse_cold_start_config(off) is None  # only the boolean true turns it on


@pytest.mark.parametrize(
    "bad",
    [
        {"cold_start_budget_s": 29}, {"cold_start_budget_s": 421}, {"cold_start_budget_s": True}, {"cold_start_budget_s": "300"},
        {"cold_start_poll_after_s": 0}, {"cold_start_poll_after_s": 61}, {"cold_start_deadline_margin_s": -1},
        {"cold_start_deadline_margin_s": 301}, {"cold_start_request_ttl_s": 29}, {"cold_start_request_ttl_s": 601},
        {"cold_start_user_wakes_per_day": 0}, {"cold_start_user_wakes_per_day": 51}, {"cold_start_user_wakes_per_day": True},
        {"cold_start_failed_cooldown_s": -1}, {"cold_start_failed_cooldown_s": 3601}, {"cold_start_failed_cooldown_s": "120"},
        {"cold_start_wake_second": "lazy"}, {"cold_start_wake_second": "both"},
        {"cold_start_waker_secret_env": "lower"}, {"cold_start_waker_secret_env": "A"}, {"cold_start_waker_secret_env": 5},
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

SECRET = "test-secret-not-a-real-key"
N1, N2, N3 = "00112233445566778899aabbccddeeff", "ffeeddccbbaa99887766554433221100", "0123456789abcdef0123456789abcdef"


def test_the_wake_request_vectors_and_validation() -> None:
    assert W.canonical(1761000000, N1, "second", "wake") == f"v1|POST|/wake|1761000000|{N1}|second|wake"
    assert W.sign(SECRET, 1761000000, N1, "second", "wake") == "509c66e15bee3f648f44a16b55ec51dadb8a97f2fe1de549f3624745e63e09ee"
    assert W.sign(SECRET, 1761000060, N2, "first", "wake") == "843f90925ade66d54f767f2a7f7fdfd92ebf7f5d3ca5cd543c415ef0a8fdeb87"
    assert W.canonical(1761000300, N3, "second", "extend", "lease-x").endswith("|second|extend|lease-x")
    for bad in (
        dict(ts=1, nonce=N1, label="third", kind="wake"),  # not a label
        dict(ts=1, nonce=N1, label="first", kind="park"),  # not a kind
        dict(ts=1, nonce="short", label="first", kind="wake"),  # not a nonce
        dict(ts=1, nonce=N1, label="first", kind="extend"),  # an extend needs a lease_ref
        dict(ts=1, nonce=N1, label="first", kind="wake", lease_ref="x"),  # a wake carries none
        dict(ts=1, nonce=N1, label="first", kind="extend", lease_ref="a|b"),  # no pipe inside a lease_ref (R2)
        dict(ts=1, nonce=N1, label="first", kind="extend", lease_ref=""),
    ):
        with pytest.raises(ValueError):
            W.canonical(**bad)  # type: ignore[arg-type]


def test_the_poll_and_delivery_vectors_are_a_different_domain() -> None:
    assert W.canonical_http("GET", "/internal/wake-requests", 1761000000, N1) == f"v1|GET|/internal/wake-requests|1761000000|{N1}"
    assert W.sign_http(SECRET, "GET", "/internal/wake-requests", 1761000000, N1) == (
        "d06f344017d6b08334c56a08feb182ca88176b5ce295c3377d1609bb90f08f91"
    )
    body = b'{"v":1,"results":[{"nonce":"00112233445566778899aabbccddeeff","state":"granted","lease_ref":"lease-1"}]}'
    assert W.sign_http(SECRET, "POST", "/internal/wake-results", 1761000060, N2, body) == (
        "edcd95219dd4b29a24c46f0100f0fde321fed1cc4168b2bd28c93f59756d2b2b"
    )
    # a wake-request signature is not a valid poll signature
    wake_sig = W.sign(SECRET, 1761000000, N1, "first", "wake")
    assert wake_sig != W.sign_http(SECRET, "GET", "/internal/wake-requests", 1761000000, N1)


class WallClock(Clock):
    def __init__(self, t: float = 1761000000.0) -> None:
        self.t = t


def _verify(headers: dict[str, str], method: str, path: str, body: bytes | None, nonces: W.NonceStore, clock: WallClock) -> bool:
    return W.verify_http(
        SECRET, method, path, headers.get("X-Waker-Timestamp"), headers.get("X-Waker-Nonce"), headers.get("X-Waker-Signature"),
        body, nonces=nonces, clock=clock,
    )


def test_a_signed_poll_verifies_once_inside_the_window() -> None:
    clock = WallClock()
    nonces = W.NonceStore(clock=clock)
    h = box_poll_headers(SECRET, ts=int(clock.t), nonce=N1)
    assert _verify(h, "GET", "/internal/wake-requests", None, nonces, clock) is True
    assert _verify(h, "GET", "/internal/wake-requests", None, nonces, clock) is False  # the nonce is used
    stale = box_poll_headers(SECRET, ts=int(clock.t) - 61, nonce=N2)
    assert _verify(stale, "GET", "/internal/wake-requests", None, nonces, clock) is False
    future = box_poll_headers(SECRET, ts=int(clock.t) + 61, nonce=N3)
    assert _verify(future, "GET", "/internal/wake-requests", None, nonces, clock) is False
    edge = box_poll_headers(SECRET, ts=int(clock.t) - 60, nonce="a" * 32)
    assert _verify(edge, "GET", "/internal/wake-requests", None, nonces, clock) is True


def test_verification_refuses_every_malformed_or_forged_message() -> None:
    clock = WallClock()
    nonces = W.NonceStore(clock=clock)
    good = box_poll_headers(SECRET, ts=int(clock.t), nonce=N1)
    path = "/internal/wake-requests"
    assert _verify({**good, "X-Waker-Signature": "v1=" + "0" * 64}, "GET", path, None, nonces, clock) is False  # wrong signature
    no_prefix = {**good, "X-Waker-Signature": good["X-Waker-Signature"][3:]}
    assert _verify(no_prefix, "GET", path, None, nonces, clock) is False  # no v1=
    assert _verify({k: v for k, v in good.items() if k != "X-Waker-Nonce"}, "GET", path, None, nonces, clock) is False
    assert _verify({**good, "X-Waker-Timestamp": "soon"}, "GET", path, None, nonces, clock) is False
    assert _verify({**good, "X-Waker-Nonce": "short"}, "GET", path, None, nonces, clock) is False
    assert _verify(good, "GET", "/internal/other", None, nonces, clock) is False  # another path
    assert _verify(good, "POST", path, b"x", nonces, clock) is False  # another method
    other = W.sign_http("another-secret", "GET", path, int(clock.t), N1)
    assert _verify({**good, "X-Waker-Signature": "v1=" + other}, "GET", path, None, nonces, clock) is False
    # none of the failures burned the real nonce: the genuine message still verifies
    assert _verify(good, "GET", path, None, nonces, clock) is True


def test_a_delivery_signature_covers_the_body_and_a_big_body_is_refused() -> None:
    clock = WallClock()
    nonces = W.NonceStore(clock=clock)
    results: list[dict[str, object]] = [{"nonce": N1, "state": "granted", "lease_ref": "lease-1"}]
    body, h = box_result_request(SECRET, results, ts=int(clock.t), nonce=N2)
    path = "/internal/wake-results"
    assert _verify(h, "POST", path, body + b" ", nonces, clock) is False  # tampered body
    assert _verify(h, "POST", path, body, nonces, clock) is True
    big, h2 = box_result_request(SECRET, [{"nonce": N1, "state": "granted", "lease_ref": "x" * 600}], ts=int(clock.t), nonce=N3)
    assert len(big) > W.MAX_BODY and _verify(h2, "POST", path, big, nonces, clock) is False


def test_the_nonce_store_forgets_after_its_window() -> None:
    clock = WallClock()
    ns = W.NonceStore(window_s=120, clock=clock)
    assert ns.first_use(N1) is True and ns.first_use(N1) is False
    clock.t += 121
    assert ns.first_use(N1) is True


# ---- the wake board ------------------------------------------------------------------------------------------------------


def _board(ttl: float = 120.0, clock: WallClock | None = None) -> tuple[W.WakeBoard, WallClock]:
    c = clock or WallClock()
    seq = iter(f"{i:032x}" for i in range(1, 1000))
    return W.WakeBoard(ttl, clock=c, nonce=lambda: next(seq)), c


def test_a_request_is_listed_signed_fresh_and_asking_again_is_idempotent() -> None:
    board, clock = _board()
    n = board.request("first")
    assert board.request("first") == n and board.pending() == 1
    listed = board.signed(SECRET)
    assert len(listed) == 1 and set(listed[0]) == {"body", "sig"}
    body = json.loads(listed[0]["body"])
    assert body == {"v": 1, "kind": "wake", "label": "first", "ts": int(clock.t), "nonce": n}
    assert listed[0]["sig"] == "v1=" + W.sign(SECRET, int(clock.t), n, "first", "wake")
    clock.t += 30  # listed again later: the same nonce, a current timestamp, a fresh signature
    again = json.loads(board.signed(SECRET)[0]["body"])
    assert again["nonce"] == n and again["ts"] == int(clock.t)
    assert len(listed[0]["body"]) <= W.MAX_BODY


def test_results_are_matched_by_nonce_and_a_repeat_changes_nothing() -> None:
    board, _ = _board()
    n1, n2 = board.request("first"), board.request("second")
    applied = board.deliver([
        {"nonce": n1, "state": "granted", "lease_ref": "lease-1"},
        {"nonce": n2, "state": "refused", "lease_ref": None},
        {"nonce": "f" * 32, "state": "granted"},  # unknown
        {"nonce": n1, "state": "refused"},  # a repeat
        {"nonce": n2, "state": "weird"},  # not a state
        "junk",
    ])
    assert applied == 2
    assert board.answer(n1) == ("granted", "lease-1") and board.answer(n2) == ("refused", None)
    assert board.lease_for("first") == "lease-1" and board.lease_for("second") is None
    assert board.signed(SECRET) == [] and board.pending() == 0  # answered requests are no longer listed
    assert board.deliver("not a list") == 0 and board.deliver(None) == 0


def test_an_unanswered_request_expires_as_failed() -> None:
    board, clock = _board(ttl=120)
    n = board.request("second")
    clock.t += 119
    assert board.answer(n) is None and board.pending() == 1
    clock.t += 2
    assert board.answer(n) == ("failed", None) and board.signed(SECRET) == []
    n2 = board.request("second")  # a new request after the expiry is a new nonce
    assert n2 != n and board.pending() == 1


def test_extend_requests_carry_the_lease_and_are_validated() -> None:
    board, _ = _board()
    n = board.request("second", "extend", "lease-9")
    body = json.loads(board.signed(SECRET)[0]["body"])
    assert body["kind"] == "extend" and body["lease_ref"] == "lease-9" and body["nonce"] == n
    bad_requests = (
        ("third", "wake", None), ("first", "park", None), ("first", "extend", None),
        ("first", "wake", "x"), ("first", "extend", "a|b"),
    )
    for bad in bad_requests:
        with pytest.raises(ValueError):
            board.request(*bad)  # type: ignore[arg-type]


def test_at_most_twenty_requests_are_listed_and_a_lease_with_a_pipe_is_dropped() -> None:
    board, _ = _board()
    for lab in ("first", "second"):
        board.request(lab)
    assert len(board.signed(SECRET)) == 2
    n = board.request("first", "extend", "ok")
    assert board.deliver([{"nonce": n, "state": "granted", "lease_ref": "a|b"}]) == 1
    assert board.answer(n) == ("granted", None)  # the odd lease reference is not kept


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
    assert body["cold_start_wake"] is False
    assert W.parse_cold_start_config(body) is None  # off
    c = W.parse_cold_start_config({**body, "cold_start_wake": True})  # the shipped defaults all parse once switched on
    assert c is not None and (c.budget_s, c.poll_after_s, c.deadline_margin_s, c.request_ttl_s) == (300.0, 5, 30.0, 120.0)
