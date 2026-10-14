"""The assisted request path: every gate, every fallback, delete in `finally`, cost and spend, summary rules."""

from __future__ import annotations

import json
import threading
from pathlib import Path

import httpx
import pytest
import yaml

from pravrudhi.application import nyaya_agent as agent_mod
from pravrudhi.application.a0_assist import assist as AS
from pravrudhi.application.a0_assist import config as C
from pravrudhi.application.a0_assist import driver as D
from pravrudhi.application.a0_assist import limits as L
from pravrudhi.application.a0_assist import store as ST
from pravrudhi.application.a0_assist import transport as TR
from tests import test_nyaya_agent as T

REPO = Path(__file__).resolve().parent.parent
FACTS = list(T.TOY_FACTS)
CID = "bns69"
REQ = {"facts": FACTS, "narrative": "", "contract_ids": [CID]}
SENTINEL = "SENTINEL-client-text-91c2"


def make_cfg(**over):
    raw = yaml.safe_load((REPO / "configs" / "assistant_tier.yaml").read_text())
    raw.update(
        assist_route_enabled=True, agent_id="agent_p", agent_version=1, environment_id="env_p", store_ca_file="/app/ca.crt",
        workflows={},  # the shipped file names the workflow agents; these tests start with screening only
    )
    raw.update(over)
    return C.parse(raw)


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


class FakeStore:
    def __init__(self, reserve=None, fail=None):
        self.log, self._reserve, self.fail = [], ({"ok": True} if reserve is None else reserve), fail or {}

    def _do(self, name, *a):
        self.log.append((name, *a))
        if name in self.fail:
            raise ST.StoreError("x")

    def reserve(self, key, mx, ceiling):
        self._do("reserve", key, mx, ceiling)
        return self._reserve

    def finish(self, key, cost):
        self._do("finish", key, cost)
        return {"ok": True, "over_reserve": bool(cost and cost > 25)}

    def session_created(self, sid, key):
        self._do("session_created", sid, key)
        return {"ok": True}

    def session_deleted(self, sid, status):
        self._do("session_deleted", sid, status)
        return {"ok": True}

    def names(self):
        return [x[0] for x in self.log]


class FakeTransport:
    """A scripted Managed Agents session. `program` is a generator function: it yields events and receives each tool result."""

    def __init__(self, program, clock, *, cost="3", delete_status=204, session_status="idle", fail_create=None, fail=()):
        self.program, self.clock, self.cost, self.delete_status, self.session_status = (
            program,
            clock,
            cost,
            delete_status,
            session_status,
        )
        self.fail_create, self.fail = fail_create, set(fail)
        self.calls, self.posted, self.last_result, self.interrupted = [], [], None, False

    def _c(self, name):
        self.calls.append(name)
        if name in self.fail:
            raise TR.UpstreamError(500, "server_error")

    def create_session(self, agent_id, version, env, budget):
        self._c("create_session")
        if self.fail_create:
            raise self.fail_create
        return {"id": "sesn_test1"}

    def open_stream(self, sid):
        self._c("open_stream")
        outer = self

        class S:
            def __iter__(s):
                gen = outer.program(outer)
                try:
                    ev = next(gen)
                    while True:
                        yield ev
                        ev = gen.send(outer.last_result)
                except StopIteration:
                    return

            def close(s):
                outer.calls.append("stream_close")

        return S()

    def send_events(self, sid, events):
        self._c("send_events")
        self.posted.append(events)
        for e in events:
            if e.get("type") == "user.custom_tool_result":
                self.last_result = json.loads(e["content"][0]["text"])

    def interrupt(self, sid):
        self._c("interrupt")
        self.interrupted = True
        self.session_status = "idle"

    def get_session(self, sid):
        self._c("get_session")
        return {"id": sid, "status": self.session_status, "usage": {"list_cost": self.cost}}

    def list_events(self, sid):
        self._c("list_events")
        return [{"type": "session.usage", "list_cost": self.cost}] if self.cost is not None else []

    def delete_session(self, sid):
        self._c("delete_session")
        return self.delete_status

    def note(self, status):
        self.calls.append(f"note:{status}")


def tool_use(i, name, inp):
    return {"id": f"e{i}", "type": "agent.custom_tool_use", "name": name, "input": inp}


def ideal(summary="Done.", tamper=None, stop="end_turn"):
    def program(tr):
        i = 0
        ing = yield tool_use(i := i + 1, "ingest", {"facts": list(FACTS), "narrative": ""})
        sel = yield tool_use(i := i + 1, "select_contracts", {"contract_ids": [CID] if tamper != "ids" else ["other"]})
        if sel.get("refused"):
            yield {"id": "z", "type": "session.status_idle", "stop_reason": {"type": "end_turn"}}
            return
        desc = yield tool_use(i := i + 1, "describe_contract", {"contract_id": CID})
        outs = []
        for e in desc["elements"]:
            outs.append(
                (
                    yield tool_use(
                        i := i + 1,
                        "judge_element",
                        {
                            "contract_id": CID,
                            "element": e["element_id"],
                            "is_denial": e["is_denial"],
                            "facts": ing["facts"],
                            "narrative": "",
                        },
                    )
                )
            )
        for o in outs:
            if o.get("quote"):
                yield tool_use(i := i + 1, "quote_check", {"facts": ing["facts"], "fact_id": o["fact_id"], "quote": o["quote"]})
        asm = yield tool_use(i := i + 1, "assemble", {"contract_id": CID, "element_results": outs})
        lc = yield tool_use(i := i + 1, "lean_check", {"wire": asm["wire"], "contract_id": CID})
        yield tool_use(i := i + 1, "decide", {"contract_id": CID, "element_results": outs, "lean_outcome": lc["lean_outcome"]})
        yield {"id": "m1", "type": "agent.message", "content": [{"type": "text", "text": summary}]}
        yield {"id": "z", "type": "session.status_idle", "stop_reason": {"type": stop}}

    return program


def ideal_ref(summary="Done.", stop="end_turn"):
    """The same scripted agent as `ideal`, but BY REFERENCE: the hand-over the pinned product agent's tools use."""

    def program(tr):
        i = 0
        ing = yield tool_use(i := i + 1, "ingest", {"facts": list(FACTS), "narrative": ""})
        yield tool_use(i := i + 1, "select_contracts", {"contract_ids": [CID]})
        desc = yield tool_use(i := i + 1, "describe_contract", {"contract_id": CID})
        outs = []
        for e in desc["elements"]:
            outs.append(
                (
                    yield tool_use(
                        i := i + 1,
                        "judge_element",
                        {
                            "ingest_ref": ing["call_ref"], "contract_id": CID,
                            "element": e["element_id"], "is_denial": e["is_denial"],
                        },
                    )
                )
            )
        for o in outs:
            if o.get("quote"):
                yield tool_use(i := i + 1, "quote_check", {"judge_ref": o["call_ref"]})
        refs = [o["call_ref"] for o in outs]
        asm = yield tool_use(i := i + 1, "assemble", {"contract_id": CID, "element_refs": refs})
        lc = yield tool_use(i := i + 1, "lean_check", {"contract_id": CID, "assemble_ref": asm["call_ref"]})
        yield tool_use(
            i := i + 1, "decide",
            {"contract_id": CID, "element_refs": refs, "assemble_ref": asm["call_ref"], "lean_ref": lc["call_ref"]},
        )
        yield {"id": "m1", "type": "agent.message", "content": [{"type": "text", "text": summary}]}
        yield {"id": "z", "type": "session.status_idle", "stop_reason": {"type": stop}}

    return program


CORE = {"run_id": "core-run", "contracts": [{"contract_id": CID, "outcome": "ABSTAIN"}]}


class Env:
    def __init__(self, tmp_path, program=None, *, store=None, tier=None, cfg=None, **tr_kw):
        self.clock = Clock()
        self.cfg = cfg or make_cfg()
        self.store = store or FakeStore()
        self.tr = FakeTransport(program or ideal(), self.clock, **tr_kw)
        self.core_calls = 0
        agent = agent_mod.NyayaAgent(T.ScriptedJudge(T._proof_script(FACTS)), T._registry(), T._config(tmp_path))
        self.agent = agent

        def core():
            self.core_calls += 1
            return dict(CORE)

        self.deps = AS.Deps(
            cfg=self.cfg,
            tier_status=lambda: tier or {"state": "available", "reason": None},
            store=self.store,
            transport=self.tr,
            limiter=L.AssistLimiter(self.cfg.runs_per_user_per_day, self.cfg.runs_per_org_per_day, self.cfg.per_ip_per_minute),
            slots=threading.BoundedSemaphore(self.cfg.max_concurrent_sessions),
            core=core,
            make_driver=lambda: D.A0Driver(agent, request=REQ),
            finalize=lambda d: {"run_id": "assisted", "contracts": [d._decided[c] for c in d._selected]},
            clock=self.clock,
            sleep=self.clock.sleep,
        )

    def run(self, caller=None, consent=True, sid="client-1", request=None):
        caller = caller or AS.Caller(True, "u1", "o1", "1.2.3.4")
        return AS.run_assisted(request or REQ, caller, consent, sid, self.deps)


def block(out):
    return out["assistant"]


# ---- gates: no call, no reservation ---------------------------------------------------------------------------------
def test_consent_absent_raises_and_nothing_happens(tmp_path):
    e = Env(tmp_path)
    for consent in (False, None, "yes", 1):
        with pytest.raises(AS.ConsentRequired):
            e.run(consent=consent)
    assert e.store.log == [] and e.tr.calls == [] and e.core_calls == 0


def test_an_anonymous_caller_gets_the_core_result_with_sign_in_required(tmp_path):
    e = Env(tmp_path)
    out = e.run(AS.Caller(False, None, None, "1.1.1.1"))
    assert out["run_id"] == "core-run" and block(out)["used"] is False and block(out)["reason"] == "sign_in_required"
    assert e.store.log == [] and e.tr.calls == [] and e.core_calls == 1


def test_a_rate_limited_caller_gets_core_and_no_call(tmp_path):
    e = Env(tmp_path)
    for _ in range(e.cfg.per_ip_per_minute):
        e.deps.limiter.admit("u1", "o1", "1.2.3.4")
    out = e.run()
    assert block(out)["reason"] == "rate_limited" and e.store.log == [] and e.tr.calls == []


@pytest.mark.parametrize(
    ("reason", "expected"),
    [
        ("no_key", "not_configured"),
        ("disabled", "not_configured"),
        ("upstream_error", "paused"),
        ("quota", "paused"),
        ("budget_floor", "paused"),
    ],
)
def test_an_unavailable_tier_gives_core_with_the_mapped_reason_and_no_call(tmp_path, reason, expected):
    e = Env(tmp_path, tier={"state": "unavailable", "reason": reason})
    out = e.run()
    assert block(out)["reason"] == expected and block(out)["outcome"] == "refused" and e.store.log == [] and e.tr.calls == []


def test_a_missing_store_or_transport_is_not_configured(tmp_path):
    e = Env(tmp_path)
    e.deps.store = None
    assert block(e.run())["reason"] == "not_configured"
    e2 = Env(tmp_path / "b")
    e2.deps.transport = None
    assert block(e2.run())["reason"] == "not_configured" and e2.store.log == []


def test_all_concurrency_slots_taken_falls_back_without_a_reservation(tmp_path):
    e = Env(tmp_path)
    for _ in range(e.cfg.max_concurrent_sessions):
        assert e.deps.slots.acquire(blocking=False)
    assert block(e.run())["reason"] == "rate_limited" and e.store.log == []


def test_the_slot_is_released_after_every_run(tmp_path):
    e = Env(tmp_path)
    for _ in range(e.cfg.max_concurrent_sessions + 2):
        e.run(AS.Caller(True, "u", "o", None))
    assert e.deps.slots.acquire(blocking=False)


# ---- the reservation ------------------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("res", "reason"),
    [
        ({"ok": False, "reason": "ceiling"}, "daily_ceiling"),
        ({"ok": False, "reason": "not_initialised"}, "paused"),
        ({"ok": False, "reason": "counter_inconsistent"}, "paused"),
        ({}, "paused"),
        ({"ok": "true"}, "paused"),
    ],
)
def test_a_refused_reservation_falls_back_and_starts_no_session(tmp_path, res, reason):
    e = Env(tmp_path, store=FakeStore(reserve=res))
    out = e.run()
    assert block(out)["reason"] == reason and e.tr.calls == [] and e.store.names() == ["reserve"] and e.core_calls == 1


def test_an_unreadable_store_pauses_and_starts_no_session(tmp_path):
    e = Env(tmp_path, store=FakeStore(fail={"reserve": 1}))
    assert block(e.run())["reason"] == "paused" and e.tr.calls == []


def test_the_reservation_uses_the_configured_cents(tmp_path):
    e = Env(tmp_path)
    e.run()
    _, key, mx, ceiling = e.store.log[0]
    assert (mx, ceiling) == (e.cfg.session_budget_cents, e.cfg.daily_ceiling_cents) and len(key) == 36


# ---- the happy path -------------------------------------------------------------------------------------------------
def test_a_finished_run_returns_the_decide_results_and_the_fixed_steps(tmp_path):
    e = Env(tmp_path)
    out = e.run()
    b = block(out)
    assert out["run_id"] == "assisted" and b["used"] is True and b["outcome"] == "finished" and b["reason"] is None
    assert [s["name"] for s in b["steps"]] == list(D.STAGES)
    checks = {s["name"]: s.get("check") for s in b["steps"]}
    assert checks["quote_check"] == "valid" and checks["lean_check"] == "agrees"
    assert b["cost"] == {"used_cents": 3, "budget_cents": e.cfg.session_budget_cents} and b["session_id"] == "client-1"
    assert e.core_calls == 0
    assert out["contracts"][0]["outcome"] == "PROOF"


def test_the_stream_is_opened_before_the_user_message_and_the_message_carries_the_matter(tmp_path):
    e = Env(tmp_path)
    e.run()
    assert e.tr.calls.index("open_stream") < e.tr.calls.index("send_events")
    first = e.tr.posted[0][0]
    assert first["type"] == "user.message" and first["content"][0]["text"] == AS.user_message(REQ)


def test_order_in_finally_cost_then_finish_then_delete_then_record(tmp_path):
    e = Env(tmp_path)
    e.run()
    assert e.tr.calls.index("list_events") < e.tr.calls.index("delete_session")
    assert e.store.names() == ["reserve", "session_created", "finish", "session_deleted"]
    assert e.store.log[2][2] == 3 and e.store.log[3][1:] == ("sesn_test1", 204)


@pytest.mark.parametrize("status", [200, 204, 404])
def test_delete_statuses_are_recorded_as_observed(tmp_path, status):
    e = Env(tmp_path, delete_status=status)
    e.run()
    assert e.store.log[-1] == ("session_deleted", "sesn_test1", status)


@pytest.mark.parametrize("status", [409, 500])
def test_a_failed_delete_is_recorded_so_the_sweeper_can_retry_it(tmp_path, status):
    e = Env(tmp_path, delete_status=status)
    out = e.run()
    assert e.store.log[-1] == ("session_deleted", "sesn_test1", status) and block(out)["used"] is True


def test_a_delete_that_raises_is_recorded_as_599(tmp_path):
    e = Env(tmp_path, fail=("delete_session",))
    e.run()
    assert e.store.log[-1] == ("session_deleted", "sesn_test1", 599)


# ---- cost -----------------------------------------------------------------------------------------------------------
def test_an_unreadable_cost_is_passed_as_none_so_the_store_charges_the_reserve(tmp_path):
    e = Env(tmp_path, cost=None)
    out = e.run()
    assert e.store.log[2] == ("finish", e.store.log[2][1], None)
    assert block(out)["cost"]["used_cents"] == e.cfg.session_budget_cents


def test_a_garbage_cost_is_unreadable(tmp_path):
    for bad in ("abc", "-5", True, float("nan")):
        e = Env(tmp_path / str(bad), cost=bad)
        e.run()
        assert e.store.log[2][2] is None


def test_an_over_reserve_cost_is_charged_in_full_and_counted(tmp_path):
    before = AS.SAFETY_EVENTS["over_reserve"]
    e = Env(tmp_path, cost="40")
    e.run()
    assert e.store.log[2][2] == 40 and AS.SAFETY_EVENTS["over_reserve"] == before + 1


# ---- fallbacks all delete -------------------------------------------------------------------------------------------
def test_a_timeout_gives_core_records_599_and_still_deletes(tmp_path):
    def slow(tr):
        yield tool_use(1, "ingest", {"facts": list(FACTS), "narrative": ""})
        tr.clock.t += 1000
        yield tool_use(2, "select_contracts", {"contract_ids": [CID]})

    e = Env(tmp_path, slow)
    out = e.run()
    assert block(out)["outcome"] == "timed_out" and block(out)["reason"] == "timeout" and out["run_id"] == "core-run"
    assert "delete_session" in e.tr.calls and e.store.names()[-2:] == ["finish", "session_deleted"]


def test_a_stream_read_timeout_is_a_timeout_and_is_noted_as_599(tmp_path):
    def broken(tr):
        yield tool_use(1, "ingest", {"facts": list(FACTS), "narrative": ""})
        raise httpx.ReadTimeout("t")

    e = Env(tmp_path, broken)
    out = e.run()
    assert block(out)["outcome"] == "timed_out" and "note:599" in e.tr.calls and "delete_session" in e.tr.calls


def test_a_create_timeout_is_a_timeout_and_nothing_is_left_to_delete(tmp_path):
    e = Env(tmp_path, fail_create=TR.UpstreamError(599, "ConnectTimeout"))
    out = e.run()
    assert block(out)["outcome"] == "timed_out" and "delete_session" not in e.tr.calls
    assert e.store.names() == ["reserve", "finish"]


@pytest.mark.parametrize("fail", ["open_stream", "send_events"])
def test_an_upstream_error_at_a_call_site_gives_core_and_still_deletes(tmp_path, fail):
    e = Env(tmp_path, fail=(fail,))
    out = e.run()
    assert block(out)["outcome"] == "refused" and block(out)["reason"] == "error" and out["run_id"] == "core-run"
    assert "delete_session" in e.tr.calls and e.store.names()[-1] == "session_deleted"


def test_a_create_failure_gives_core_and_releases_the_reservation_via_finish(tmp_path):
    e = Env(tmp_path, fail_create=TR.UpstreamError(500, "server_error"))
    out = e.run()
    assert block(out)["reason"] == "error" and e.store.names() == ["reserve", "finish"] and e.store.log[1][2] is None


def test_the_budget_stop_gives_core_with_per_session_budget(tmp_path):
    def over(tr):
        yield tool_use(1, "ingest", {"facts": list(FACTS), "narrative": ""})
        yield {"id": "z", "type": "session.status_idle", "stop_reason": {"type": "budget_reached"}}

    out = Env(tmp_path, over).run()
    assert block(out)["outcome"] == "budget" and block(out)["reason"] == "per_session_budget" and out["run_id"] == "core-run"


@pytest.mark.parametrize("etype", ["session.error", "session.status_terminated"])
def test_a_session_error_or_termination_gives_core(tmp_path, etype):
    def bad(tr):
        yield {"id": "x", "type": etype}

    e = Env(tmp_path, bad)
    assert block(e.run())["reason"] == "error" and "delete_session" in e.tr.calls


def test_a_stream_that_ends_without_an_end_of_turn_gives_core(tmp_path):
    def short(tr):
        yield tool_use(1, "ingest", {"facts": list(FACTS), "narrative": ""})

    assert block(Env(tmp_path, short).run())["reason"] == "error"


def test_an_incomplete_run_ends_in_core_not_a_partial_result(tmp_path):
    def partial(tr):
        ing = yield tool_use(1, "ingest", {"facts": list(FACTS), "narrative": ""})
        assert ing["facts"]
        yield {"id": "z", "type": "session.status_idle", "stop_reason": {"type": "end_turn"}}

    out = Env(tmp_path, partial).run()
    assert out["run_id"] == "core-run" and block(out)["reason"] == "error"


def test_a_tampered_tool_input_is_a_safety_refusal_with_the_core_result(tmp_path):
    before = AS.SAFETY_EVENTS["refusals"]
    e = Env(tmp_path, ideal(tamper="ids"))
    out = e.run()
    assert block(out)["outcome"] == "refused" and block(out)["reason"] == "safety" and out["run_id"] == "core-run"
    assert AS.SAFETY_EVENTS["refusals"] > before and "delete_session" in e.tr.calls


def test_the_driver_building_or_finalizing_failing_gives_core_and_still_deletes(tmp_path):
    e = Env(tmp_path)
    e.deps.finalize = lambda d: 1 / 0
    out = e.run()
    assert out["run_id"] == "core-run" and block(out)["reason"] == "error" and "delete_session" in e.tr.calls


# ---- interrupt and wait ---------------------------------------------------------------------------------------------
def test_a_running_session_is_interrupted_and_waited_for_before_the_delete(tmp_path):
    e = Env(tmp_path, session_status="running")
    e.run()
    assert e.tr.interrupted and e.tr.calls.index("interrupt") < e.tr.calls.index("delete_session")


def test_an_idle_session_is_not_interrupted(tmp_path):
    e = Env(tmp_path, session_status="idle")
    e.run()
    assert not e.tr.interrupted


def test_a_session_that_never_goes_idle_is_still_deleted_after_a_bounded_wait(tmp_path):
    e = Env(tmp_path, session_status="running")
    e.tr.interrupt = lambda sid: e.tr.calls.append("interrupt")
    e.run()
    assert "delete_session" in e.tr.calls and e.clock.t <= e.cfg.interrupt_wait_s + 5 + 60


# ---- the summary ----------------------------------------------------------------------------------------------------
def test_a_clean_summary_is_kept_display_only(tmp_path):
    out = Env(tmp_path, ideal(summary="I checked every element and quoted the facts.")).run()
    assert block(out)["summary"] == "I checked every element and quoted the facts."


@pytest.mark.parametrize(
    "text", ["The matter is a DENIAL.", "Result: ABSTAIN", "REFER_TO_LAWYER is advised", "Also see bns85 for this."]
)
def test_a_summary_naming_a_foreign_outcome_or_contract_is_dropped_and_counted(tmp_path, text):
    before = AS.SAFETY_EVENTS["summary_dropped"]
    e = Env(tmp_path, ideal(summary=text))
    e.deps.finalize = lambda d: {"run_id": "assisted", "contracts": [d._decided[c] for c in d._selected]}
    d0 = e.deps.make_driver
    e.deps.make_driver = lambda: _with_listed(d0(), ["bns69", "bns85"])
    out = e.run()
    assert block(out)["summary"] is None and block(out)["used"] is True and out["contracts"][0]["outcome"] == "PROOF"
    assert AS.SAFETY_EVENTS["summary_dropped"] == before + 1


def _with_listed(driver, ids):
    orig = driver._t_select_contracts

    def sel(inp):
        out = orig(inp)
        driver.listed = {i: [] for i in ids}
        return out

    driver._t_select_contracts = sel
    return driver


def test_a_summary_naming_the_real_outcome_is_kept(tmp_path):
    assert block(Env(tmp_path, ideal(summary="The engine's decision is PROOF for bns69.")).run())["summary"].startswith(
        "The engine's"
    )


def test_an_empty_summary_is_none(tmp_path):
    assert block(Env(tmp_path, ideal(summary="  ")).run())["summary"] is None


# ---- no client text or id in any store write ------------------------------------------------------------------------
def test_no_client_text_or_the_client_session_id_reaches_the_store(tmp_path):
    e = Env(tmp_path)
    req = {**REQ, "facts": [*FACTS[:-1], FACTS[-1] + " " + SENTINEL], "narrative": SENTINEL}
    e.deps.make_driver = lambda: D.A0Driver(e.agent, request=req)

    def program(tr):
        yield {"id": "z", "type": "session.status_idle", "stop_reason": {"type": "end_turn"}}

    e.tr.program = program
    e.run(sid=SENTINEL, request=req)
    assert SENTINEL not in json.dumps(e.store.log, default=str)


def test_a_core_error_response_passes_through_untouched(tmp_path):
    e = Env(tmp_path, tier={"state": "unavailable", "reason": "quota"})
    sentinel = object()
    e.deps.core = lambda: sentinel
    assert e.run() is sentinel


# ---- helpers --------------------------------------------------------------------------------------------------------
def test_list_cost_reads_the_last_usage_event_then_the_session_and_never_a_negative():
    assert (
        AS.list_cost_cents([{"type": "session.usage", "list_cost": "12"}, {"type": "session.usage", "list_cost": "60"}], None)
        == 60
    )
    assert AS.list_cost_cents([], {"usage": {"list_cost": "7"}}) == 7
    assert AS.list_cost_cents([{"type": "session.usage", "list_cost": "-3"}], None) is None
    assert AS.list_cost_cents([], None) is None


def test_the_raw_lean_outcome_never_appears_in_the_steps():
    calls = [
        {"tool": "assemble", "input": {}, "output": {"contract_id": "c", "expected_outcome": "PROOF"}},
        {"tool": "lean_check", "input": {}, "output": {"contract_id": "c", "lean_outcome": "ABSTAIN"}},
    ]
    steps = AS.steps_from(calls)
    assert steps == [{"name": "assemble"}, {"name": "lean_check", "check": "differs"}] and "PROOF" not in json.dumps(steps)


# ---- the cost parser: the live shape, ceil, fail closed -------------------------------------------------------------------
@pytest.mark.parametrize(
    ("v", "cents"),
    [
        ({"amount": "10", "currency": "USD"}, 10),
        ({"amount": "2.4", "currency": "USD"}, 3),
        ({"amount": "0", "currency": "USD"}, 0),
        ({"amount": 7, "currency": "USD"}, 7),
        ({"amount": 7.2, "currency": "USD"}, 8),
        ("12", 12),
        ("2.4", 3),
        (5, 5),
        (100, 100),
    ],
)
def test_cost_amount_cents_reads_the_live_shape_and_rounds_up(v, cents):
    assert AS.cost_amount_cents(v) == cents


@pytest.mark.parametrize(
    "v",
    [
        {"amount": "10", "currency": "EUR"},
        {"amount": "10", "currency": "USD", "extra": 1},
        {"amount": "10"},
        {"currency": "USD"},
        {"amount": "-1", "currency": "USD"},
        {"amount": float("nan"), "currency": "USD"},
        {"amount": float("inf"), "currency": "USD"},
        {"amount": True, "currency": "USD"},
        {"amount": "1e3", "currency": "USD"},
        {"amount": "abc", "currency": "USD"},
        {"amount": "101", "currency": "USD"},
        True,
        False,
        None,
        "",
        "-3",
        -3,
        float("nan"),
        [],
        {},
    ],
)
def test_cost_amount_cents_is_none_for_anything_else(v):
    assert AS.cost_amount_cents(v) is None


def test_list_cost_reads_an_object_shaped_usage_event_and_session():
    ev = [{"type": "session.usage", "list_cost": {"amount": "10", "currency": "USD"}}]
    assert AS.list_cost_cents(ev, None) == 10
    assert AS.list_cost_cents([{"type": "session.usage", "usage": {"list_cost": {"amount": "4", "currency": "USD"}}}], None) == 4
    assert AS.list_cost_cents([], {"usage": {"list_cost": {"amount": "7", "currency": "USD"}}}) == 7


def test_the_first_readable_candidate_wins_over_an_unreadable_one():
    ev = [{"type": "session.usage", "list_cost": {"amount": "x", "currency": "USD"}, "usage": {"list_cost": "6"}}]
    assert AS.list_cost_cents(ev, None) == 6


def test_a_live_shaped_cost_flows_through_a_run_and_an_overshoot_is_charged_in_full_and_flagged(tmp_path):
    before = AS.SAFETY_EVENTS["over_reserve"]
    e = Env(tmp_path, cost={"amount": "40", "currency": "USD"})
    e.tr.list_events = lambda sid: [{"type": "session.usage", "list_cost": e.tr.cost}]
    out = e.run()
    assert e.store.log[2][2] == 40 and AS.SAFETY_EVENTS["over_reserve"] == before + 1
    assert block(out)["cost"]["used_cents"] == 40


def test_a_live_shaped_cost_below_the_reserve_is_reported_as_read(tmp_path):
    e = Env(tmp_path, cost={"amount": "3", "currency": "USD"})
    e.tr.list_events = lambda sid: [{"type": "session.usage", "list_cost": e.tr.cost}]
    out = e.run()
    assert e.store.log[2][2] == 3 and block(out)["cost"]["used_cents"] == 3


# ---- per-workflow agents (workflow hook) -------------------------------------------------------------------------------------
class _SpyTransport(FakeTransport):
    def create_session(self, agent_id, version, env, budget):
        self.created_with = (agent_id, version, env, budget)
        return super().create_session(agent_id, version, env, budget)


def _register_hp(e):
    """What a workflow's wiring does: its driver set and its own first message and summary (here the screening ones)."""
    e.deps.workflows = frozenset({"screening", "hearing_prep"})
    e.deps.user_message = AS.user_message
    e.deps.summarise = AS.screening_summarise


def _hp_cfg(**over):
    return make_cfg(workflows={"hearing_prep": {"agent_id": "agent_hp", "agent_version": 3, "environment_id": "env_hp"}}, **over)


def test_an_assisted_run_of_a_workflow_creates_its_session_on_that_workflows_agent(tmp_path):
    e = Env(tmp_path, cfg=_hp_cfg())
    _register_hp(e)
    e.tr = e.deps.transport = _SpyTransport(ideal(), e.clock)
    out = AS.run_assisted(REQ, AS.Caller(True, "u1", "o1", "1.2.3.4"), True, "c1", e.deps, workflow="hearing_prep")
    assert e.tr.created_with == ("agent_hp", 3, "env_hp", e.cfg.session_budget_cents)
    assert block(out)["used"] is True


def test_screening_still_uses_the_top_level_agent_and_is_the_default(tmp_path):
    e = Env(tmp_path, cfg=_hp_cfg())
    e.tr = e.deps.transport = _SpyTransport(ideal(), e.clock)
    out = e.run()  # no workflow argument
    assert e.tr.created_with[:3] == ("agent_p", 1, "env_p") and block(out)["used"] is True


@pytest.mark.parametrize("workflow", ["hearing_prep", "intake", "nonsense"])
def test_a_workflow_with_no_agent_is_not_configured_and_costs_nothing(tmp_path, workflow):
    e = Env(tmp_path)  # no workflows block
    out = AS.run_assisted(REQ, AS.Caller(True, "u1", "o1", "1.2.3.4"), True, "c1", e.deps, workflow=workflow)
    assert out["run_id"] == "core-run" and block(out)["used"] is False and block(out)["reason"] == "not_configured"
    assert e.store.log == [] and e.tr.calls == [] and e.core_calls == 1


def test_an_unconfigured_workflow_does_not_take_a_slot_or_a_reservation(tmp_path):
    e = Env(tmp_path)
    AS.run_assisted(REQ, AS.Caller(True, "u1", "o1", "1.2.3.4"), True, "c1", e.deps, workflow="intake")
    assert e.deps.slots.acquire(blocking=False)  # still free (the slot was never taken)
    assert [x for x in e.store.log if x[0] == "reserve"] == []


def test_an_unconfigured_workflow_is_not_charged_against_the_callers_quota(tmp_path):
    e = Env(tmp_path)
    for _ in range(e.cfg.per_ip_per_minute + 3):
        AS.run_assisted(REQ, AS.Caller(True, "u1", "o1", "1.2.3.4"), True, "c1", e.deps, workflow="intake")
    out = e.run()  # screening for the same caller: the quota is untouched
    assert block(out)["used"] is True


def test_a_configured_workflow_with_no_driver_in_these_deps_is_still_not_configured(tmp_path):
    e = Env(tmp_path, cfg=_hp_cfg())  # the config has a hearing_prep agent; these Deps drive screening only
    assert e.deps.workflows == frozenset({"screening"})
    out = AS.run_assisted(REQ, AS.Caller(True, "u1", "o1", "1.2.3.4"), True, "c1", e.deps, workflow="hearing_prep")
    assert block(out)["reason"] == "not_configured" and e.tr.calls == [] and e.store.log == []
    _register_hp(e)  # a workflow's PR registers its own driver set and hooks
    e.tr = e.deps.transport = _SpyTransport(ideal(), e.clock)
    out = AS.run_assisted(REQ, AS.Caller(True, "u1", "o1", "1.2.3.4"), True, "c1", e.deps, workflow="hearing_prep")
    assert e.tr.created_with[0] == "agent_hp" and block(out)["used"] is True


# ---- the per-workflow message and summary hooks (spec hook) --------------------------------------------------------------------
def test_a_workflows_own_first_message_is_the_one_posted_and_screening_keeps_its_own(tmp_path):
    e = Env(tmp_path)
    e.deps.user_message = lambda request: "RAW:" + request["facts"][0]
    e.run()
    assert e.tr.posted[0][0]["content"][0]["text"] == "RAW:" + FACTS[0]
    d = Env(tmp_path / "default")
    d.run()
    assert d.tr.posted[0][0]["content"][0]["text"] == AS.user_message(REQ)  # no hook: unchanged


def test_a_workflows_own_summary_replaces_the_screening_rule(tmp_path):
    e = Env(tmp_path, program=ideal(summary="The matter is an ABSTAIN"))  # the screening rule would DROP this text
    out = e.run()
    assert block(out)["summary"] is None  # default: dropped (outcome word and contract id not in the decide results)
    e2 = Env(tmp_path / "w", program=ideal(summary="anything"))
    seen = {}

    def summarise(driver, events):
        seen["driver"], seen["events"] = driver, events
        return "WORKFLOW SUMMARY"

    e2.deps.summarise = summarise
    out2 = e2.run()
    assert block(out2)["summary"] == "WORKFLOW SUMMARY" and isinstance(seen["driver"], D.A0Driver) and seen["events"]


def test_a_summary_that_raises_is_no_summary_not_a_failed_run(tmp_path):
    e = Env(tmp_path)

    def boom(driver, events):
        raise RuntimeError("x")

    e.deps.summarise = boom
    out = e.run()
    assert block(out)["used"] is True and block(out)["summary"] is None and "delete_session" in e.tr.calls


def test_a_workflow_message_that_raises_gives_core_and_still_deletes(tmp_path):
    e = Env(tmp_path)

    def boom(request):
        raise KeyError("text")

    e.deps.user_message = boom
    out = e.run()
    assert out["run_id"] == "core-run" and block(out)["used"] is False and "delete_session" in e.tr.calls


def test_a_non_screening_workflow_must_set_its_own_message_and_summary_no_silent_screening_default(tmp_path):
    for missing in ("user_message", "summarise", "both"):
        e = Env(tmp_path / missing, cfg=_hp_cfg())
        _register_hp(e)
        if missing in ("user_message", "both"):
            e.deps.user_message = None
        if missing in ("summarise", "both"):
            e.deps.summarise = None
        out = AS.run_assisted(REQ, AS.Caller(True, "u1", "o1", "1.2.3.4"), True, "c1", e.deps, workflow="hearing_prep")
        assert block(out)["reason"] == "not_configured" and e.tr.calls == [] and e.store.log == []


def test_screening_without_hooks_is_unchanged(tmp_path):
    e = Env(tmp_path)
    assert e.deps.user_message is None and e.deps.summarise is None
    assert block(e.run())["used"] is True


def test_a_hook_summary_that_is_not_text_is_no_summary(tmp_path):
    e = Env(tmp_path)
    e.deps.summarise = lambda driver, events: {"x": 1}
    assert block(e.run())["summary"] is None


@pytest.mark.parametrize("bad", ["", "   ", None, 5, b"x"])
def test_a_workflow_first_message_that_is_not_text_gives_core_and_still_deletes(tmp_path, bad):
    e = Env(tmp_path)
    e.deps.user_message = lambda request: bad
    out = e.run()
    assert out["run_id"] == "core-run" and block(out)["used"] is False and "delete_session" in e.tr.calls


def test_the_summary_hook_gets_a_copy_of_the_events(tmp_path):
    e = Env(tmp_path)

    def mutate(driver, events):
        events.clear()
        return "ok"

    e.deps.summarise = mutate
    out = e.run()
    assert block(out)["summary"] == "ok" and block(out)["used"] is True  # the run's own events were not emptied under it


def test_the_screening_summary_rule_is_not_swallowed(tmp_path, monkeypatch):
    """Unchanged: an exception in the SCREENING default still ends in the ordinary result (only a workflow's own hook is
    display-only)."""
    def boom(*a, **k):
        raise RuntimeError("summary rule broke")

    monkeypatch.setattr(AS, "checked_summary", boom)
    e = Env(tmp_path)
    out = e.run()
    assert out["run_id"] == "core-run" and block(out)["used"] is False and "delete_session" in e.tr.calls
