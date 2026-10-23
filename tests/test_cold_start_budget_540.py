"""0.6.34: cold_start_budget_s 540: the budget bound, the shipped value, and the dead-wake TTL that must stay beyond
the wake's own deadline (a fixed 450 s released a slow but live wake as dead mid-flight). Invented ids."""

from __future__ import annotations

import time
from pathlib import Path

import yaml

from pravrudhi.application import judge_wake as jw
from pravrudhi.application import wake_store as ws
from tests.wake_store_fakes import FakeWakeDb


def _cfg(**over):  # type: ignore[no-untyped-def]
    cfg = jw.parse_cold_start_config({"cold_start_wake": True, **over})
    assert cfg is not None
    return cfg


def test_a_budget_of_540_is_valid_and_one_past_the_hard_bound_keeps_cold_start_off() -> None:
    assert _cfg(cold_start_budget_s=540).budget_s == 540.0
    assert jw.parse_cold_start_config({"cold_start_wake": True, "cold_start_budget_s": 601}) is None
    assert jw.parse_cold_start_config({"cold_start_wake": True, "cold_start_budget_s": 29}) is None


def test_the_shipped_config_carries_540_and_parses() -> None:
    body = yaml.safe_load((Path(__file__).resolve().parents[1] / "configs" / "partner_api.yaml").read_text())
    assert body["cold_start_budget_s"] == 540
    cfg = jw.parse_cold_start_config({**body, "cold_start_wake": True})
    assert cfg is not None and cfg.budget_s == 540.0
    assert cfg.budget_s + cfg.deadline_margin_s < 900  # inside the waker's 900 s lease


def test_the_dead_wake_ttl_is_beyond_the_wake_deadline_for_the_default_and_for_540() -> None:
    d = _cfg()  # default 300 + 30: the old 450 bound is kept
    assert jw.active_ttl_s(d) == 450.0
    s = _cfg(cold_start_budget_s=540)
    assert jw.active_ttl_s(s) >= s.budget_s + s.deadline_margin_s + 60.0 and jw.active_ttl_s(s) == 630.0
    big = _cfg(cold_start_budget_s=600, cold_start_deadline_margin_s=300)
    assert jw.active_ttl_s(big) > big.budget_s + big.deadline_margin_s


def test_a_slow_live_wake_is_not_released_as_dead_before_its_deadline_in_either_store() -> None:
    cfg = _cfg(cold_start_budget_s=540)
    t = [1000.0]
    mem = ws.MemoryWakeStore(cfg, clock=lambda: t[0])
    db = FakeWakeDb(lambda: t[0])
    pg = ws.PgWakeStore(db.call, cfg)
    for store in (mem, pg):
        assert store.limit_begin("u-1")[0] == "new"
    t[0] += 500.0  # past the old 450 s bound, before budget + margin (570 s)
    for store in (mem, pg):
        assert store.limit_begin("u-1")[0] == "existing"  # still the same active wake, not released as failed (no cooldown)
    t[0] += 200.0  # well past the new bound (630 s): now it IS a dead wake, released as failed
    for store in (mem, pg):
        assert store.limit_begin("u-1")[0] == "cooldown"


def test_the_ttl_passed_to_the_database_function_follows_the_config() -> None:
    cfg = _cfg(cold_start_budget_s=540)
    seen: list[tuple[str, tuple]] = []  # type: ignore[type-arg]
    db = FakeWakeDb(time.time)

    def call(name: str, *args):  # type: ignore[no-untyped-def]
        seen.append((name, args))
        return db.call(name, *args)

    ws.PgWakeStore(call, cfg).limit_begin("u-2")
    begin = next(a for n, a in seen if n == "wake_begin")
    assert begin[3] == 630.0  # p_active_ttl
