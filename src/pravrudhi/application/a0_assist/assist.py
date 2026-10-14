"""One assisted screening request, end to end.

`run_assisted` never raises for an operational reason: every way the assistant can be unavailable, slow, over budget or
misbehaving ends in the ordinary screening result (the `core` callable) plus an `assistant` block saying `used: false`
and why. Order of a request:

  1. consent, sign-in, anti-abuse limits, tier status: any refusal = core result, NO Anthropic call, NO reservation;
  2. reserve the spend in the store BEFORE any session exists (refused or unreadable store = paused fallback);
  3. create the session on the pinned product agent, record its id, open the event stream, THEN post the user message;
  4. answer the agent's custom tool calls in-process with the stage driver (tool outputs stay in memory);
  5. the result is the driver's `decide` outputs by reference; the agent's final text is display-only and is dropped
     when it names an outcome word or a contract id the decide results did not produce;
  6. in `finally`, always: interrupt if running and wait (bounded) for idle, read the cost, finish the reservation,
     DELETE the session and record the DELETE status. A failed DELETE leaves the session pending for the sweeper.
"""

from __future__ import annotations

import contextlib
import json
import math
import re
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from pravrudhi.application.a0_assist.config import MAX_SESSION_CENTS, AssistConfig, WorkflowAgent
from pravrudhi.application.a0_assist.limits import AssistLimiter
from pravrudhi.application.a0_assist.store import SpendStore, StoreError, new_session_key
from pravrudhi.application.a0_assist.transport import AnthropicTransport, TransportRefused, UpstreamError

USER_MESSAGE_PREFIX = (
    "Screen this matter with your tools, exactly as your rules say. The matter, as JSON (copy the facts exactly as written):\n"
)
OUTCOME_WORDS = ("PROOF", "DENIAL", "ABSTAIN", "REFER_TO_LAWYER")
DONE_STATUSES = ("idle", "terminated")
UPSTREAM = (UpstreamError, TransportRefused, httpx.HTTPError)
SAFETY_EVENTS = {"summary_dropped": 0, "refusals": 0, "over_reserve": 0}
_safety_lock = threading.Lock()


def safety_event(name: str, n: int = 1) -> None:
    with _safety_lock:
        SAFETY_EVENTS[name] = SAFETY_EVENTS.get(name, 0) + n


class ConsentRequired(Exception):
    """The request did not carry consent: the route answers 400 `consent_required`; no call, no reservation."""


@dataclass(frozen=True)
class Caller:
    authenticated: bool
    user_id: str | None
    org_id: str | None
    ip: str | None


class WorkflowDriver(Protocol):
    """What the runner needs from any workflow's driver: tool calls answered in-process, a record of every call, the binding
    refusals, and the list of problems that make a run incomplete (empty = complete)."""

    calls: list[dict[str, Any]]
    refusals: list[str]

    def call(self, tool: str, inp: dict[str, Any]) -> dict[str, Any]: ...

    def complete(self) -> list[str]: ...


@dataclass
class Deps:
    cfg: AssistConfig
    tier_status: Callable[[], dict[str, Any]]
    store: SpendStore | None
    transport: AnthropicTransport | None
    limiter: AssistLimiter
    slots: threading.BoundedSemaphore
    core: Callable[[], Any]
    make_driver: Callable[[], WorkflowDriver]
    finalize: Callable[[Any], dict[str, Any]]
    #: The workflows THIS Deps can drive (its `make_driver` and `finalize` belong to them). A workflow outside this set is
    #: answered as not configured even if the config has an agent for it: one workflow's driver is never used for another.
    workflows: frozenset[str] = frozenset({"screening"})
    #: The first message of the session for THIS workflow, from the request dict the route built (None = the screening
    #: message: `user_message`).
    user_message: Callable[[dict[str, Any]], str] | None = None
    #: The display-only summary of the agent's prose, from (driver, stream events) (None = the screening
    #: `checked_summary` over the decide results). An exception here means no summary, never a failed run.
    summarise: Callable[[Any, list[dict[str, Any]]], str | None] | None = None
    clock: Callable[[], float] = time.monotonic
    sleep: Callable[[float], None] = time.sleep


INTAKE_USER_MESSAGE_PREFIX = (
    "Prepare this pasted matter text with your tools, exactly as your rules say. "
    "The text, as JSON (copy it exactly as written):\n"
)


def intake_user_message(request: dict[str, Any]) -> str:
    """The first message of an intake session: the pasted text, as message content, exactly as the screening message carries
    its facts. Nothing is stored; the session is deleted after the run."""
    return INTAKE_USER_MESSAGE_PREFIX + json.dumps({"text": request["text"]}, ensure_ascii=False)


def user_message(request: dict[str, Any]) -> str:
    payload = {"facts": request["facts"], "narrative": request.get("narrative", ""), "contract_ids": request["contract_ids"]}
    return USER_MESSAGE_PREFIX + json.dumps(payload, ensure_ascii=False)


def cost_amount_cents(v: Any, bound: int = MAX_SESSION_CENTS) -> int | None:
    """One list_cost value to whole cents, ROUNDED UP, fail closed. The live API returns an object
    {"amount": "10", "currency": "USD"} (the unit is cents, like the budget's own amount). Readable = a dict with EXACTLY the keys
    amount and currency, currency "USD", and an amount that is a plain decimal string or a finite number in [0, bound]; a bare
    string or number is read the same way. Anything else (no amount, a non-numeric amount, another currency, a bool, NaN or inf,
    a negative, an amount above the engine's own per-session limit, which is a unit error) is None = unreadable: the store then
    charges the full reserve, never zero."""
    if isinstance(v, dict):
        if set(v) != {"amount", "currency"} or v["currency"] != "USD":
            return None
        v = v["amount"]
    if isinstance(v, bool):
        return None
    if isinstance(v, str):
        if not re.fullmatch(r"\d+(\.\d+)?", v):
            return None
        v = float(v)
    if isinstance(v, (int, float)):
        return math.ceil(v) if math.isfinite(v) and 0 <= v <= bound else None
    return None


def list_cost_cents(
    events: Iterable[dict[str, Any]], session: dict[str, Any] | None, bound: int = MAX_SESSION_CENTS
) -> int | None:
    """The session's tracked list cost in whole cents: the last session.usage event (top level or under usage), else the session
    object's usage. The first READABLE candidate wins; None when none is readable (the caller charges the full reserve)."""
    cands: list[Any] = []
    for e in reversed(list(events)):
        if e.get("type") == "session.usage":
            cands += [e.get("list_cost"), (e.get("usage") or {}).get("list_cost")]
            break
    if session is not None:
        cands.append((session.get("usage") or {}).get("list_cost"))
    for v in cands:
        if v is None:
            continue
        c = cost_amount_cents(v, bound)
        if c is not None:
            return c
    return None


def final_text(events: Iterable[dict[str, Any]]) -> str:
    msgs = [e for e in events if e.get("type") == "agent.message"]
    if not msgs:
        return ""
    return "".join(b.get("text", "") for b in msgs[-1].get("content", []) if isinstance(b, dict) and b.get("type") == "text")


def checked_summary(text: str, decided: list[dict[str, Any]], known_contracts: Iterable[str]) -> str | None:
    """The agent's prose, or None when it names an outcome word or a contract id the decide results did not produce."""
    if not text.strip():
        return None
    ours = {str(c.get("outcome")) for c in decided}
    ids = {str(c.get("contract_id")) for c in decided}
    for word in OUTCOME_WORDS:
        if re.search(rf"\b{word}\b", text) and word not in ours:
            safety_event("summary_dropped")
            return None
    for cid in known_contracts:
        if cid not in ids and re.search(rf"(?<![A-Za-z0-9_]){re.escape(cid)}(?![A-Za-z0-9_])", text):
            safety_event("summary_dropped")
            return None
    return text


def steps_from(calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One entry per stage name in first-seen order; `check` is the quote check (valid/invalid) and the Lean check
    (agrees/differs); the raw Lean outcome never appears."""
    steps: list[dict[str, Any]] = []
    seen: dict[str, dict[str, Any]] = {}
    verdicts: dict[str, list[bool]] = {"quote_check": [], "lean_check": []}
    assembled: dict[str, Any] = {}
    for c in calls:
        out = c.get("output") or {}
        if out.get("refused"):
            continue
        name = c["tool"]
        if name not in seen:
            seen[name] = {"name": name}
            steps.append(seen[name])
        if name == "quote_check" and "valid" in out:
            verdicts["quote_check"].append(out["valid"] is True)
        if name == "assemble":
            assembled[str(out.get("contract_id", ""))] = out.get("expected_outcome")
        if name == "lean_check":
            verdicts["lean_check"].append(assembled.get(out.get("contract_id", "")) == out.get("lean_outcome"))
    for name, vs in verdicts.items():
        if vs and name in seen:
            if name == "quote_check":
                seen[name]["check"] = "valid" if all(vs) else "invalid"
            else:
                seen[name]["check"] = "agrees" if all(vs) else "differs"
    return steps


def _block(used: bool, outcome: str, reason: str | None, **kw: Any) -> dict[str, Any]:
    block = {
        "used": used,
        "outcome": outcome,
        "reason": reason,
        "steps": kw.get("steps", []),
        "cost": kw.get("cost"),
        "summary": kw.get("summary"),
        "session_id": kw.get("session_id"),
    }
    if kw.get("progress") is not None:
        block["progress"] = kw["progress"]  # only on a run that did not finish: where it stopped (counts and times, no text)
    return block


def _progress(run: _Run, cfg: AssistConfig) -> dict[str, Any] | None:
    """Where a run that did not finish stopped: counts, seconds, last tool name; never text. None when no session ran or when
    the diagnostics themselves cannot be built (a diagnostic must never turn a clean fallback into an error). `events` is
    the stream-event count the event cap uses (replays included)."""
    if run.sid is None:
        return None
    try:
        calls = [c for c in (run.driver.calls if run.driver is not None else []) if isinstance(c, dict)]
        refused = sum(1 for c in calls if isinstance(c.get("output"), dict) and c["output"].get("refused"))
        last = calls[-1].get("tool") if calls else None
        return {
            "tool_calls": len(calls),
            "refused_calls": refused,
            "events": run.n,
            "max_events": cfg.max_events,
            "elapsed_s": None if run.elapsed_s is None else round(run.elapsed_s, 1),
            "wall_limit_s": cfg.call_timeout_s,
            # a tool name is model-supplied: only a plain identifier of sensible length is echoed
            "last_tool": last if isinstance(last, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,39}", last) else None,
        }
    except Exception:  # noqa: BLE001 - see the docstring
        return None


def _with(body: Any, block: dict[str, Any]) -> Any:
    if isinstance(body, dict):
        return {**body, "assistant": block}
    return body  # an error response of the core path (for example a 503) passes through untouched


def _fallback(deps: Deps, outcome: str, reason: str, client_sid: str | None, **kw: Any) -> Any:
    return _with(deps.core(), _block(False, outcome, reason, session_id=client_sid, **kw))


def _tier_reason(tier: dict[str, Any]) -> str:
    return "not_configured" if tier.get("reason") in ("no_key", "disabled") else "paused"


class _Run:
    """The state of one session: everything the `finally` needs."""

    def __init__(self) -> None:
        self.sid: str | None = None
        self.key: str | None = None
        self.events: list[dict[str, Any]] = []
        self.stop: str | None = None  # end_turn | budget | timeout | error
        self.driver: WorkflowDriver | None = None
        self.n = 0  # stream events seen
        self.elapsed_s: float | None = None  # seconds from the first message to the end of the conversation


def run_assisted(
    request: dict[str, Any], caller: Caller, consent: bool, client_session_id: str | None, deps: Deps,
    workflow: str = "screening",
) -> Any:
    cfg = deps.cfg
    if consent is not True:
        raise ConsentRequired()
    if not caller.authenticated:
        return _fallback(deps, "refused", "sign_in_required", client_session_id)
    # A workflow with no agent, or with no driver in this Deps, stays dark. Decided BEFORE the limiter so a request that can
    # never run is not charged against anyone's quota.
    agent = cfg.agent_for(workflow) if workflow in deps.workflows else None
    if workflow != "screening" and (deps.user_message is None or deps.summarise is None):
        agent = None  # a workflow other than screening must say its own first message and summary: no silent screening default
    if agent is None:
        return _fallback(deps, "refused", "not_configured", client_session_id)
    if deps.limiter.admit(caller.user_id, caller.org_id, caller.ip) is not None:
        return _fallback(deps, "refused", "rate_limited", client_session_id)
    tier = deps.tier_status()
    if tier.get("state") != "available":
        return _fallback(deps, "refused", _tier_reason(tier), client_session_id)
    if deps.store is None or deps.transport is None:
        return _fallback(deps, "refused", "not_configured", client_session_id)
    if not deps.slots.acquire(blocking=False):
        return _fallback(deps, "refused", "rate_limited", client_session_id)
    try:
        return _run_with_slot(request, client_session_id, deps, cfg, agent, workflow)
    finally:
        deps.slots.release()


def _run_with_slot(
    request: dict[str, Any], client_sid: str | None, deps: Deps, cfg: AssistConfig, agent: WorkflowAgent, workflow: str
) -> Any:
    store, tr = deps.store, deps.transport
    assert store is not None and tr is not None
    key = new_session_key()
    try:
        res = store.reserve(key, cfg.session_budget_cents, cfg.daily_ceiling_cents)
    except StoreError:
        return _fallback(deps, "refused", "paused", client_sid)
    if res.get("ok") is not True:
        return _fallback(deps, "refused", "daily_ceiling" if res.get("reason") == "ceiling" else "paused", client_sid)
    run = _Run()
    run.key = key
    cost: int | None = None
    outcome_reason: tuple[str, str | None] = ("refused", "error")
    decided_body: dict[str, Any] | None = None
    summary: str | None = None
    try:
        driver = deps.make_driver()
        run.driver = driver
        outcome_reason = _converse(request, run, deps, cfg, agent)
        if outcome_reason == ("finished", None):
            problems = driver.complete()
            if driver.refusals:
                safety_event("refusals", len(driver.refusals))
                outcome_reason = ("refused", "safety")
            elif problems:
                outcome_reason = ("refused", "error")
            else:
                decided_body = deps.finalize(driver)
                summary = _summary(deps, workflow, driver, run.events)
    except UpstreamError as exc:
        timed_out = exc.status == 599 and "Timeout" in str(exc)
        outcome_reason = ("timed_out", "timeout") if timed_out else ("refused", "error")
    except UPSTREAM:
        outcome_reason = ("refused", "error")
    except Exception:  # noqa: BLE001 - nothing the assistant does may break the screening result
        outcome_reason = ("refused", "error")
    finally:
        cost = _close(run, deps, cfg)
    cost_block = {"used_cents": cost if cost is not None else cfg.session_budget_cents, "budget_cents": cfg.session_budget_cents}
    if decided_body is not None and outcome_reason == ("finished", None):
        steps = steps_from(run.driver.calls if run.driver else [])
        block = _block(True, "finished", None, steps=steps, cost=cost_block, summary=summary, session_id=client_sid)
        return _with(decided_body, block)
    outcome, reason = outcome_reason
    steps = steps_from(run.driver.calls) if run.driver and outcome == "refused" and reason == "safety" else []
    return _fallback(
        deps, outcome, reason or "error", client_sid, steps=steps, cost=cost_block, progress=_progress(run, cfg)
    )


def screening_summarise(driver: Any, events: list[dict[str, Any]]) -> str | None:
    """The screening workflow's display-only summary: the agent's prose, dropped when it names an outcome word or a contract
    id the decide results did not produce."""
    return checked_summary(final_text(events), [driver._decided[c] for c in driver._selected or []], driver.listed or {})


def _summary(deps: Deps, workflow: str, driver: Any, events: list[dict[str, Any]]) -> str | None:
    """The display-only summary. Screening without a hook keeps its rule exactly as before (an exception there still ends in
    the ordinary result). A workflow's OWN `summarise` is untrusted display code: its exception, or a return that is not text,
    means no summary, never a failed run; it is handed a copy of the events."""
    if deps.summarise is None:
        return screening_summarise(driver, events)
    try:
        out = deps.summarise(driver, list(events))
    except Exception:  # noqa: BLE001 - display-only: its failure must not fail a finished run
        return None
    return out if isinstance(out, str) else None


def _converse(request: dict[str, Any], run: _Run, deps: Deps, cfg: AssistConfig, agent: WorkflowAgent) -> tuple[str, str | None]:
    tr = deps.transport
    assert tr is not None and run.driver is not None and run.key is not None
    sess = tr.create_session(agent.agent_id, agent.agent_version, agent.environment_id, cfg.session_budget_cents)
    sid = sess["id"]
    run.sid = sid
    deps.store.session_created(sid, run.key)  # type: ignore[union-attr]
    stream = tr.open_stream(sid)
    seen: set[Any] = set()
    t0, n = deps.clock(), 0
    try:
        first = (deps.user_message or user_message)(request)
        if not isinstance(first, str) or not first.strip():
            raise ValueError("the workflow's first message must be non-empty text")
        tr.send_events(sid, [{"type": "user.message", "content": [{"type": "text", "text": first}]}])
        for ev in stream:
            n += 1
            run.n = n
            if ev.get("id") in seen:
                continue
            seen.add(ev.get("id"))
            run.events.append(ev)
            et = ev.get("type")
            if et == "agent.custom_tool_use":
                out = run.driver.call(str(ev.get("name")), ev.get("input", {}))
                tr.send_events(
                    sid,
                    [
                        {
                            "type": "user.custom_tool_result",
                            "custom_tool_use_id": ev["id"],
                            "content": [{"type": "text", "text": json.dumps(out)}],
                        }
                    ],
                )
            elif et == "session.status_idle":
                stop = (ev.get("stop_reason") or {}).get("type")
                if stop == "end_turn":
                    return "finished", None
                if stop == "requires_action":
                    continue
                return ("budget", "per_session_budget") if stop == "budget_reached" else ("refused", "error")
            elif et in ("session.error", "session.status_terminated"):
                return "refused", "error"
            if n >= cfg.max_events or deps.clock() - t0 > cfg.call_timeout_s:
                return "timed_out", "timeout"
        return "refused", "error"  # the stream ended without an end of turn
    except httpx.TimeoutException:
        tr.note(599)
        return "timed_out", "timeout"
    except httpx.HTTPError:
        tr.note(599)
        return "refused", "error"
    finally:
        stream.close()
        run.elapsed_s = deps.clock() - t0


def _close(run: _Run, deps: Deps, cfg: AssistConfig) -> int | None:
    """The `finally` of a request. Every step is independent: a failure in one never skips the others."""
    store, tr = deps.store, deps.transport
    assert store is not None and tr is not None
    cost: int | None = None
    session: dict[str, Any] | None = None
    if run.sid is not None:
        with contextlib.suppress(*UPSTREAM):
            session = tr.get_session(run.sid)
            if (session or {}).get("status") not in DONE_STATUSES:
                with contextlib.suppress(*UPSTREAM):
                    tr.interrupt(run.sid)
                deadline = deps.clock() + cfg.interrupt_wait_s
                while deps.clock() < deadline:
                    deps.sleep(1.0)
                    session = tr.get_session(run.sid)
                    if (session or {}).get("status") in DONE_STATUSES:
                        break
        listed: list[dict[str, Any]] = []
        with contextlib.suppress(*UPSTREAM):
            listed = tr.list_events(run.sid)
        cost = list_cost_cents(listed or run.events, session)
    if run.key is not None:
        try:
            fin = store.finish(run.key, cost)
            if isinstance(fin, dict) and fin.get("over_reserve"):
                safety_event("over_reserve")
        except StoreError:
            pass  # the open reservation is charged in full by the stale sweep
    if run.sid is not None:
        status = 599
        with contextlib.suppress(*UPSTREAM):
            status = tr.delete_session(run.sid)
        with contextlib.suppress(StoreError):
            store.session_deleted(run.sid, status)
    return cost
