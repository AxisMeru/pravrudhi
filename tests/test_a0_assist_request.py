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
        assist_route_enabled=True, agent_id="agent_p", agent_version=1, environment_id="env_p", store_ca_file="/app/ca.crt"
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
