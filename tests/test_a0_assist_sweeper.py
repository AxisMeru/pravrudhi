"""The sweeper: deletes only what the store lists, records a delete only on 200/204/404, never raises, runs the canary on
its interval, and pauses the tier when it cannot do its job."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import yaml

from pravrudhi.application.a0_assist import config as C
from pravrudhi.application.a0_assist import guard as G
from pravrudhi.application.a0_assist import store as ST
from pravrudhi.application.a0_assist import sweeper as SW
from pravrudhi.application.a0_assist import transport as TR

REPO = Path(__file__).resolve().parent.parent
CFG = C.parse(yaml.safe_load((REPO / "configs" / "assistant_tier.yaml").read_text()))


def pend(*ids):
    return [{"anthropic_session_id": i, "created_at": "t", "state": "pending_delete"} for i in ids]


class FakeStore:
    def __init__(self, pending=(), canary=None, status=None, fail=None):
        self.pending, self.marked, self.calls = list(pending), [], []
        self._canary = {"ok": True, "bad_functions": 0} if canary is None else canary
        self._status = status
        self.fail = fail or {}

    def _do(self, name):
        self.calls.append(name)
        if name in self.fail:
            raise ST.StoreError("x")

    def list_pending(self, n):
        self._do("list_pending")
        return self.pending

    def session_deleted(self, sid, status):
        self._do("session_deleted")
        self.marked.append((sid, status))
        return {"ok": True}

    def mark_deleted(self, sid, status):
        self._do("mark_deleted")
        self.marked.append((sid, status))
        return {"ok": True}

    def abandon_stale(self, m):
        self._do("abandon_stale")
        return {"abandoned": 0}

    def sweep_ok(self):
        self._do("sweep_ok")
        return {"ok": True}

    def canary(self):
        self._do("canary")
        return self._canary

    def prune(self):
        self._do("prune")
        return 0

    def status(self):
        self._do("status")
        return self._status or {
            "initialised": True,
            "canary_failed": False,
            "canary_age_s": 10,
            "sweep_last_ok_age_s": 5,
            "pending_deletes": 0,
            "oldest_pending_delete_age_s": 0,
        }


class FakeTransport:
    def __init__(self, statuses=None, raises=None):
        self.statuses, self.raises, self.deleted, self.interrupted = statuses or {}, raises, [], []

    def delete_session(self, sid):
        self.deleted.append(sid)
        if self.raises:
            raise self.raises
        return self.statuses.get(sid, 204)

    def interrupt(self, sid):
        self.interrupted.append(sid)
        if self.raises:
            raise self.raises


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def make(store, tr, clock=None):
    p = G.Pauses()
    return SW.Sweeper(CFG, store, tr, p, clock=clock or Clock()), p


@pytest.mark.parametrize("status", [200, 204, 404])
def test_a_delete_is_recorded_for_200_204_and_404_with_the_real_status(status):
    st, tr = FakeStore(pend("sesn_a")), FakeTransport({"sesn_a": status})
    out = make(st, tr)[0].run_once()
    assert st.marked == [("sesn_a", status)] and out["deleted"] == 1 and out["failed"] == 0


@pytest.mark.parametrize("status", [400, 401, 403, 409, 429, 500, 529, 599])
def test_any_other_status_leaves_the_session_pending_and_stops_it(status):
    st, tr = FakeStore(pend("sesn_a")), FakeTransport({"sesn_a": status})
    out = make(st, tr)[0].run_once()
    assert st.marked == [] and out["failed"] == 1 and tr.interrupted == ["sesn_a"]


@pytest.mark.parametrize("exc", [TR.UpstreamError(500), TR.TransportRefused("x"), httpx.ConnectTimeout("t")])
def test_a_transport_failure_is_a_failed_delete_never_an_exception(exc):
    st, tr = FakeStore(pend("sesn_a", "sesn_b")), FakeTransport(raises=exc)
    out = make(st, tr)[0].run_once()
    assert st.marked == [] and out["failed"] == 2 and out["error"] is None


def test_only_the_stores_list_is_ever_deleted_and_one_pass_is_bounded():
    st, tr = FakeStore(pend(*[f"sesn_{i}" for i in range(SW.MAX_PER_PASS + 10)])), FakeTransport()
    make(st, tr)[0].run_once()
    assert len(tr.deleted) == SW.MAX_PER_PASS and set(tr.deleted) <= {r["anthropic_session_id"] for r in st.pending}


def test_a_clean_pass_marks_the_sweep_and_abandons_stale_reservations():
    st = FakeStore()
    out, p = (lambda s: (s[0].run_once(), s[1]))(make(st, FakeTransport()))
    assert "sweep_ok" in st.calls and "abandon_stale" in st.calls and out["abandoned"] == 0 and p.reason() is None


def test_a_store_error_mid_pass_pauses_the_tier_and_does_not_mark_the_sweep():
    st = FakeStore(pend("sesn_a"), fail={"mark_deleted": 1})
    out, p = (lambda s: (s[0].run_once(), s[1]))(make(st, FakeTransport()))
    assert out["error"] == "store" and "sweep_ok" not in st.calls and p.snapshot()["sweeper"] == "sweeper_store_error"


def test_a_pass_after_a_store_error_clears_the_sweeper_pause():
    st = FakeStore(pend("sesn_a"), fail={"mark_deleted": 1})
    sw, p = make(st, FakeTransport())
    sw.run_once()
    st.fail = {}
    sw.run_once()
    assert "sweeper" not in p.snapshot()


def test_a_sweeper_without_a_store_or_transport_pauses_and_never_raises():
    for st, tr in ((None, FakeTransport()), (FakeStore(), None)):
        out, p = (lambda s: (s[0].run_once(), s[1]))(make(st, tr))
        assert out["error"] == "not_configured" and p.reason() is not None


def test_the_canary_runs_on_the_first_pass_then_only_when_the_interval_has_elapsed():
    st, clock = FakeStore(), Clock()
    sw, _ = make(st, FakeTransport(), clock)
    sw.run_once()
    assert st.calls.count("canary") == 1
    clock.t += CFG.canary_interval_min * 60 - 1
    sw.run_once()
    assert st.calls.count("canary") == 1
    clock.t += 2
    sw.run_once()
    assert st.calls.count("canary") == 2


def test_a_canary_finding_pauses_and_a_later_clean_canary_clears():
    st, clock = FakeStore(canary={"ok": False, "bad_functions": 1}), Clock()
    sw, p = make(st, FakeTransport(), clock)
    sw.run_once()
    assert p.snapshot()["canary"] == "canary_failed"
    st._canary = {"ok": True}
    clock.t += CFG.canary_interval_min * 60
    sw.run_once()
    assert "canary" not in p.snapshot()


def test_an_erroring_canary_pauses_the_tier():
    st = FakeStore(fail={"canary": 1})
    sw, p = make(st, FakeTransport())
    sw.run_once()
    assert p.snapshot()["canary"] == "canary_error"


def test_prune_runs_daily_not_every_pass():
    st, clock = FakeStore(), Clock()
    sw, _ = make(st, FakeTransport(), clock)
    sw.run_once()
    sw.run_once()
    assert st.calls.count("prune") == 1
    clock.t += SW.PRUNE_EVERY_S
    sw.run_once()
    assert st.calls.count("prune") == 2


def test_a_prune_error_does_not_stop_the_pass():
    st = FakeStore(fail={"prune": 1})
    assert make(st, FakeTransport())[0].run_once()["error"] is None


def test_the_engine_store_status_feeds_the_pause_rules():
    engine = FakeStore(
        status={
            "initialised": True,
            "canary_failed": True,
            "canary_age_s": 1,
            "sweep_last_ok_age_s": 1,
            "pending_deletes": 0,
            "oldest_pending_delete_age_s": 0,
        }
    )
    p = G.Pauses()
    SW.Sweeper(CFG, FakeStore(), FakeTransport(), p, engine_store=engine).run_once()
    assert p.snapshot()["store"] == "canary_failed"


def make_engine(store, tr, clock=None):
    p = G.Pauses()
    return SW.Sweeper(CFG, store, tr, p, clock=clock or Clock(), mode="engine"), p


def test_engine_mode_records_with_session_deleted_and_never_runs_canary_or_prune():
    st = FakeStore(pend("sesn_a"))
    sw, p = make_engine(st, FakeTransport({"sesn_a": 404}))
    out = sw.run_once()
    assert st.marked == [("sesn_a", 404)] and "mark_deleted" not in st.calls
    assert "canary" not in st.calls and "prune" not in st.calls and out["canary"] is None
    assert "status" in st.calls and p.reason() is None


def test_engine_mode_reads_the_canary_staleness_from_status_so_a_stale_canary_pauses():
    st = FakeStore(
        status={
            "initialised": True,
            "canary_failed": False,
            "canary_age_s": 3 * 3600 + 60,
            "sweep_last_ok_age_s": 1,
            "pending_deletes": 0,
            "oldest_pending_delete_age_s": 0,
        }
    )
    sw, p = make_engine(st, FakeTransport())
    sw.run_once()
    assert p.snapshot()["store"] == "canary_stale"


def test_a_sweeper_without_a_store_in_engine_mode_pauses_without_a_canary_call():
    sw, p = make_engine(None, FakeTransport())
    assert sw.run_once()["error"] == "not_configured" and p.snapshot() == {"sweeper": "sweeper_not_configured"}


def test_an_unknown_mode_is_refused():
    with pytest.raises(ValueError):
        SW.Sweeper(CFG, FakeStore(), FakeTransport(), G.Pauses(), mode="other")
