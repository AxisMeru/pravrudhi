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

    workspace_result = True

    def workspace_ok(self):
        self.calls_ws = getattr(self, "calls_ws", 0) + 1
        return self.workspace_result

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
    out, p = (lambda s: (s[0].run_once(), s[1]))(make_engine(st, FakeTransport()))
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
    SW.Sweeper(CFG, FakeStore(), FakeTransport(), p, engine_store=engine, mode="engine").run_once()
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


# ---- delete canary and the by-agent orphan scan (engine mode, product agent configured) ---------------------------------
def cfg_with_agent():
    raw = yaml.safe_load((REPO / "configs" / "assistant_tier.yaml").read_text())
    raw.update(
        assist_route_enabled=True, agent_id="agent_prod", agent_version=1, environment_id="env_p", store_ca_file="/app/ca.crt"
    )
    return C.parse(raw)


class AgentTransport(FakeTransport):
    def __init__(self, statuses=None, listed=(), create_raises=None, **kw):
        super().__init__(statuses, **kw)
        self.listed, self.create_raises, self.created, self.list_agent_ids = list(listed), create_raises, 0, []

    def create_session(self, agent_id, version, env, budget):
        if self.create_raises:
            raise self.create_raises
        self.created += 1
        return {"id": "sesn_canary"}

    def list_sessions(self, agent_id, *, limit=100):
        self.list_agent_ids.append(agent_id)
        yield from self.listed


NOW = 1_700_000_000.0


def iso(age_min):
    from datetime import UTC, datetime

    return datetime.fromtimestamp(NOW - age_min * 60, UTC).isoformat().replace("+00:00", "Z")


def eng_sweeper(store, tr, **kw):
    p = G.Pauses()
    return SW.Sweeper(cfg_with_agent(), store, tr, p, mode="engine", wall_clock=lambda: NOW, clock=Clock(), **kw), p


def test_the_delete_canary_runs_on_the_first_pass_and_a_success_clears_the_pause():
    tr = AgentTransport({"sesn_canary": 204})
    sw, p = eng_sweeper(FakeStore(), tr)
    sw.run_once()
    assert tr.created == 1 and "sesn_canary" in tr.deleted and "delete_canary" not in p.snapshot()
    sw.run_once()
    assert tr.created == 1  # once per process after a success


@pytest.mark.parametrize("status", [200, 204, 404])
def test_a_delete_canary_answered_200_204_or_404_passes(status):
    tr = AgentTransport({"sesn_canary": status})
    sw, p = eng_sweeper(FakeStore(), tr)
    sw.run_once()
    assert "delete_canary" not in p.snapshot() and tr.created == 1


@pytest.mark.parametrize("status", [401, 403, 409, 500])
def test_a_key_that_cannot_delete_pauses_the_tier_and_is_retried_every_pass(status):
    tr = AgentTransport({"sesn_canary": status})
    sw, p = eng_sweeper(FakeStore(), tr)
    sw.run_once()
    assert p.snapshot()["delete_canary"] == "delete_canary_failed"
    tr.statuses["sesn_canary"] = 204
    sw.run_once()
    assert tr.created == 2 and "delete_canary" not in p.snapshot()


@pytest.mark.parametrize("exc", [TR.UpstreamError(403), TR.TransportRefused("x"), httpx.ConnectError("c")])
def test_a_create_failure_in_the_delete_canary_pauses(exc):
    sw, p = eng_sweeper(FakeStore(), AgentTransport(create_raises=exc))
    sw.run_once()
    assert p.snapshot()["delete_canary"] == "delete_canary_failed"


def test_the_backstop_mode_runs_neither_the_delete_canary_nor_the_orphan_scan_by_default():
    tr = AgentTransport(listed=[{"id": "sesn_o", "created_at": iso(999)}])
    p = G.Pauses()
    SW.Sweeper(cfg_with_agent(), FakeStore(), tr, p, mode="backstop", wall_clock=lambda: NOW, clock=Clock()).run_once()
    assert tr.created == 0 and tr.list_agent_ids == [] and tr.deleted == []


def test_an_old_session_of_the_product_agent_unknown_to_the_store_is_deleted_a_young_one_is_spared():
    cfg = cfg_with_agent()
    tr = AgentTransport(
        {"sesn_canary": 204},
        listed=[
            {"id": "sesn_old", "created_at": iso(cfg.abandon_after_min + 1)},
            {"id": "sesn_young", "created_at": iso(cfg.abandon_after_min - 1)},
            {"id": "sesn_edge", "created_at": iso(cfg.abandon_after_min)},
            {"id": "sesn_nodate"},
            {"id": "sesn_badate", "created_at": "yesterday"},
            {"created_at": iso(999)},
        ],
    )
    sw, _ = eng_sweeper(FakeStore(), tr)
    out = sw.run_once()
    assert (
        "sesn_old" in tr.deleted
        and "sesn_young" not in tr.deleted
        and "sesn_nodate" not in tr.deleted
        and "sesn_badate" not in tr.deleted
    )
    assert "sesn_edge" in tr.deleted  # exactly at the limit counts as old; one minute younger does not
    assert out["orphans_deleted"] == 2 and tr.list_agent_ids == ["agent_prod"]


def test_a_session_the_store_lists_as_pending_is_not_deleted_twice_by_the_scan():
    cfg = cfg_with_agent()
    tr = AgentTransport({"sesn_canary": 204}, listed=[{"id": "sesn_a", "created_at": iso(cfg.abandon_after_min + 5)}])
    st = FakeStore(pend("sesn_a"))
    eng_sweeper(st, tr)[0].run_once()
    assert tr.deleted.count("sesn_a") == 1 and st.marked == [("sesn_a", 204)]


def test_a_failed_listing_is_tolerated_and_reported_and_the_pass_still_marks_the_sweep():
    class Bad(AgentTransport):
        def list_sessions(self, agent_id, *, limit=100):
            raise TR.UpstreamError(500)
            yield

    st = FakeStore()
    out = eng_sweeper(st, Bad({"sesn_canary": 204}))[0].run_once()
    assert out["orphan_scan"] == "failed" and "sweep_ok" in st.calls


def test_a_failed_orphan_delete_does_not_stop_the_scan():
    tr = AgentTransport(
        {"sesn_canary": 204, "sesn_x": 500},
        listed=[{"id": "sesn_x", "created_at": iso(999)}, {"id": "sesn_y", "created_at": iso(999)}],
    )
    out = eng_sweeper(FakeStore(), tr)[0].run_once()
    assert out["orphans_deleted"] == 1 and "sesn_y" in tr.deleted


def test_the_engine_checks_its_own_workspace_first_and_a_failure_pauses_without_the_delete_canary():
    tr = AgentTransport({"sesn_canary": 204})
    tr.workspace_result = False
    sw, p = eng_sweeper(FakeStore(), tr)
    sw.run_once()
    assert p.snapshot()["workspace"] == "workspace_check_failed" and tr.created == 0
    tr.workspace_result = True
    sw.run_once()
    assert "workspace" not in p.snapshot() and tr.created == 1 and "delete_canary" not in p.snapshot()


def test_the_workspace_check_stops_being_repeated_once_it_has_passed():
    tr = AgentTransport({"sesn_canary": 204})
    sw, _ = eng_sweeper(FakeStore(), tr)
    sw.run_once()
    sw.run_once()
    assert tr.calls_ws == 1


def test_the_backstop_mode_does_not_run_the_engine_workspace_check():
    tr = FakeTransport()
    SW.Sweeper(CFG, FakeStore(), tr, G.Pauses(), mode="backstop", clock=Clock()).run_once()
    assert getattr(tr, "calls_ws", 0) == 0


# ---- each mode calls only the functions its database role is granted ----
ENGINE_ROLE = {
    "reserve",
    "finish",
    "abandon_stale",
    "sweep_ok",
    "status",
    "canary",
    "session_created",
    "session_deleted",
    "list_pending",
}
SWEEPER_ROLE = {"list_pending", "mark_deleted", "prune", "canary", "spend_summary"}


class RoleStore(FakeStore):
    """A store that refuses (as the database does) any function the role is not granted, and records the refusal."""

    def __init__(self, granted, **kw):
        super().__init__(**kw)
        self.granted, self.refused = granted, []

    def _do(self, name, *a):
        if name not in self.granted:
            self.refused.append(name)
            raise ST.StoreError("permission denied")
        super()._do(name, *a)

    def spend_summary(self):
        self._do("spend_summary")
        return {}


def test_backstop_mode_never_calls_abandon_stale_or_sweep_ok_and_a_sweeper_role_pass_is_clean():
    st = RoleStore(SWEEPER_ROLE, pending=pend("sesn_a"))
    p = G.Pauses()
    sw = SW.Sweeper(CFG, st, FakeTransport(), p, mode="backstop", clock=Clock())
    out = sw.run_once()
    assert (
        st.refused == [] and out["error"] is None and out["deleted"] == 1 and out["canary"]["ok"] is True and out["pruned"] == 0
    )
    assert "abandon_stale" not in st.calls and "sweep_ok" not in st.calls and st.marked == [("sesn_a", 204)]
    assert out["abandoned"] is None and p.snapshot() == {}


def test_a_refused_sweep_ok_or_abandon_stale_cannot_alert_the_backstop():
    for name in ("sweep_ok", "abandon_stale"):
        st = FakeStore(pend("sesn_a"), fail={name: 1})
        out = make(st, FakeTransport())[0].run_once()
        assert out["error"] is None and name not in st.calls and out["deleted"] == 1


def test_engine_mode_calls_both_and_a_failing_sweep_ok_is_still_an_error_and_a_pause():
    st = FakeStore(pend("sesn_a"))
    out, p = (lambda s: (s[0].run_once(), s[1]))(make_engine(st, FakeTransport()))
    assert "abandon_stale" in st.calls and "sweep_ok" in st.calls and out["abandoned"] == 0
    bad = FakeStore(fail={"sweep_ok": 1})
    sw, p2 = make_engine(bad, FakeTransport())
    out2 = sw.run_once()
    assert out2["error"] == "store" and p2.snapshot()["sweeper"] == "sweeper_store_error"


def test_the_engine_mode_pass_uses_only_the_engine_roles_functions_even_with_every_feature_on():
    st = RoleStore(ENGINE_ROLE, pending=pend("sesn_a"))
    tr = AgentTransport({"sesn_canary": 204}, listed=[{"id": "sesn_old", "created_at": iso(999)}])
    sw, p = eng_sweeper(st, tr)
    out = sw.run_once()
    assert st.refused == [] and out["error"] is None and "mark_deleted" not in st.calls and "prune" not in st.calls


def test_the_backstop_process_run_uses_only_the_sweeper_roles_functions(tmp_path, monkeypatch):
    from tests import test_a0_assist_backstop as TB

    st = RoleStore(SWEEPER_ROLE, pending=[])
    st.list_pending = lambda n: (st._do("list_pending"), [])[1]
    code, out = TB.B.run(TB.root_with(tmp_path), {}, store=st, transport=FakeTransport(), canary=True, prune=True, now=TB.NOW)
    assert st.refused == [] and code == 0 and out["ok"] is True and out["canary"] is True


# ---- the role grant lists cannot drift from the migration ----
def test_the_grant_parser_reads_multi_line_statements_comments_and_role_lists():
    from tests.test_a0_assist_backstop_pg_it import grants_of

    sql = """
    -- grant execute on function assist.commented_out() to assist_spend_rpc;
    grant execute on function assist.reserve(text, integer, integer), assist.finish(text, bigint),
      assist.status() to assist_spend_rpc;
    grant execute on function assist.list_pending(integer), assist.canary() to assist_spend_rpc, assist_sweeper_rpc;
    grant execute on function assist.prune() to assist_sweeper_rpc;
    grant usage on schema assist to assist_spend_rpc;
    """
    g = grants_of(sql)
    assert g["assist_spend_rpc"] == {"reserve", "finish", "status", "list_pending", "canary"}
    assert g["assist_sweeper_rpc"] == {"list_pending", "canary", "prune"}


def test_the_role_lists_used_by_these_tests_equal_the_grants_of_the_migration_when_it_is_available():
    import os

    path = os.environ.get("PRAVRUDHI_A0_MIGRATION")
    if not path:
        pytest.skip("set PRAVRUDHI_A0_MIGRATION to the applied migration file to compare the lists")
    from tests.test_a0_assist_backstop_pg_it import grants_of

    g = grants_of(Path(path).read_text())
    assert g["assist_sweeper_rpc"] == SWEEPER_ROLE and g["assist_spend_rpc"] == ENGINE_ROLE
