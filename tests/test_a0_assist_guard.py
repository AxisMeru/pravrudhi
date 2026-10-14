"""Pause rules: both arms of every rule, the canary clock arms (59 min / 3 h 1 min / never run / erroring), fail closed."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from pravrudhi.application.a0_assist import config as C
from pravrudhi.application.a0_assist import guard as G
from pravrudhi.application.a0_assist import store as ST

REPO = Path(__file__).resolve().parent.parent
CFG = C.parse(yaml.safe_load((REPO / "configs" / "assistant_tier.yaml").read_text()))


def good(**over):
    s = {
        "initialised": True,
        "canary_failed": False,
        "canary_age_s": 59 * 60,
        "sweep_last_ok_age_s": 60,
        "pending_deletes": 0,
        "oldest_pending_delete_age_s": 0,
    }
    s.update(over)
    return s


def test_a_healthy_status_pauses_nothing():
    assert G.evaluate(good(), CFG) is None


def test_canary_clean_59_minutes_ago_is_not_paused_and_3h1m_is_paused():
    assert G.evaluate(good(canary_age_s=59 * 60), CFG) is None
    assert G.evaluate(good(canary_age_s=3 * 3600), CFG) is None
    assert G.evaluate(good(canary_age_s=3 * 3600 + 60), CFG) == "canary_stale"


def test_a_never_run_canary_pauses():
    assert G.evaluate(good(canary_age_s=None), CFG) == "canary_never_run"


def test_a_failed_canary_pauses_even_when_fresh():
    assert G.evaluate(good(canary_failed=True, canary_age_s=1), CFG) == "canary_failed"


@pytest.mark.parametrize("v", [None, "no", 0, 1])
def test_canary_failed_must_be_exactly_false(v):
    assert G.evaluate(good(canary_failed=v), CFG) == "canary_failed"


@pytest.mark.parametrize("v", [-1, "5", True, 1.5])
def test_a_malformed_canary_age_pauses(v):
    assert G.evaluate(good(canary_age_s=v), CFG) == "canary_never_run"


@pytest.mark.parametrize("bad", [None, [], "x", 5])
def test_a_non_mapping_status_pauses(bad):
    assert G.evaluate(bad, CFG) == "store_status_unreadable"


def test_an_uninitialised_store_pauses():
    assert G.evaluate(good(initialised=False), CFG) == "store_not_initialised"
    assert G.evaluate({k: v for k, v in good().items() if k != "initialised"}, CFG) == "store_not_initialised"


def test_a_sweep_that_has_not_run_recently_pauses_at_three_intervals():
    assert G.evaluate(good(sweep_last_ok_age_s=CFG.sweep_interval_min * 180), CFG) is None
    assert G.evaluate(good(sweep_last_ok_age_s=CFG.sweep_interval_min * 180 + 1), CFG) == "sweep_stale"
    assert G.evaluate(good(sweep_last_ok_age_s=None), CFG) == "sweep_stale"


def test_pending_deletes_pause_by_count_and_by_age():
    assert G.evaluate(good(pending_deletes=CFG.max_pending_deletes), CFG) is None
    assert G.evaluate(good(pending_deletes=CFG.max_pending_deletes + 1), CFG) == "pending_deletes_count"
    two_h = int(CFG.retention_max_hours * 3600)
    assert G.evaluate(good(pending_deletes=1, oldest_pending_delete_age_s=two_h), CFG) is None
    assert G.evaluate(good(pending_deletes=1, oldest_pending_delete_age_s=two_h + 1), CFG) == "pending_deletes_age"


@pytest.mark.parametrize("key", ["pending_deletes", "oldest_pending_delete_age_s"])
def test_a_missing_pending_figure_pauses(key):
    s = good()
    del s[key]
    assert G.evaluate(s, CFG) == "store_status_unreadable"


class FakeStore:
    def __init__(self, status=None, canary=None, err=None):
        self._status, self._canary, self._err = status, canary, err

    def status(self):
        if self._err:
            raise self._err
        return self._status

    def canary(self):
        if self._err:
            raise self._err
        return self._canary


def test_check_store_sets_and_clears_the_store_pause():
    p = G.Pauses()
    assert G.check_store(FakeStore(good(canary_failed=True)), CFG, p) == "canary_failed" and p.reason() == "canary_failed"
    assert G.check_store(FakeStore(good()), CFG, p) is None and p.reason() is None


def test_a_missing_or_failing_store_pauses():
    p = G.Pauses()
    assert G.check_store(None, CFG, p) == "store_not_configured"
    assert G.check_store(FakeStore(err=ST.StoreError("down")), CFG, p) == "store_unreachable"
    assert p.reason() == "store_unreachable"


def test_run_canary_clean_clears_and_a_finding_pauses():
    p = G.Pauses()
    r = G.run_canary(FakeStore(canary={"ok": False, "bad_functions": 1}), p)
    assert r["ok"] is False and p.snapshot() == {"canary": "canary_failed"}
    r = G.run_canary(FakeStore(canary={"ok": True, "bad_functions": 0}), p)
    assert r["ok"] is True and p.reason() is None


def test_an_erroring_canary_pauses_it_is_never_read_as_clean():
    p = G.Pauses()
    r = G.run_canary(FakeStore(err=ST.StoreError("boom")), p)
    assert r["ok"] is False and r["error"] == "store" and p.snapshot() == {"canary": "canary_error"}
    assert G.run_canary(None, p)["error"] == "no_sweeper_store" and p.snapshot() == {"canary": "canary_error"}


@pytest.mark.parametrize("res", [None, [], "ok", {"ok": "true"}, {}])
def test_a_canary_answer_that_is_not_exactly_ok_true_pauses(res):
    p = G.Pauses()
    assert G.run_canary(FakeStore(canary=res), p)["ok"] is False and p.reason() == "canary_failed"


def test_two_sources_pause_independently_and_clear_independently():
    p = G.Pauses()
    p.set("store", "canary_stale")
    p.set("canary", "canary_error")
    p.set("store", None)
    assert p.reason() == "canary_error"
    p.set("canary", None)
    assert p.reason() is None


def test_a_canary_timeout_pauses_with_its_own_reason():
    p = G.Pauses()

    class Slow:
        def canary(self):
            raise ST.StoreTimeout("t")

    r = G.run_canary(Slow(), p)
    assert r == {"ok": False, "error": "timeout"} and p.snapshot() == {"canary": "canary_timeout"}
