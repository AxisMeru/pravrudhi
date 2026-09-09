"""Running several agents at once, continuously, without them colliding or overspending.

Delegation handles one task safely. A swarm is the same guarantees applied to many tasks in parallel, plus the
question delegation does not answer: which agent and which model should take this piece of work?

Routing is by tier, and the tiers are about difficulty rather than importance. Mechanical work goes to an
open-weight model on hardware already paid for; ordinary build work goes to a fast hosted agent; design work goes
to a stronger one; and the most capable model is reserved for the few tasks where being wrong is expensive, because
using it everywhere would be waste rather than diligence. A tier is a claim about the work, so it is recorded with
the result and can be argued with afterwards.

Parallelism is bounded by disjoint ownership rather than by a worker count: tasks run together only when their
declared paths cannot collide, which is checked before anything is dispatched rather than discovered in a merge.
"""

from __future__ import annotations

import json
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pravrudhi.application import availability, continuity, routing
from pravrudhi.application import blackboard as blackboard_mod
from pravrudhi.application.delegate import TaskSpec, Verdict, dispatch, overlapping

# tier -> (agent name, model). Cost rises with tier; so should difficulty.
#
# Two corrections, both from measurement rather than from taste.
#
# The mechanical tier used to route to a local open-weight model. It produced no change at all, twice, on tasks a
# hosted agent completed in minutes. Serving text and driving an agentic tool-calling loop are different
# capabilities, and a 1.7B model has the first and not the second. The tier now routes to the cheapest hosted
# model instead, and local models are used for what they are good at.
#
# The standard tier used to pass model=None, which takes the agent's default. On this account that default is the
# most expensive model available, so three of the four tiers were silently spending the top rate: twenty sessions
# in one day consumed seven million input tokens, nearly all of it on work that did not need that model. Naming a
# model explicitly at every tier is the fix, and the top model is now reserved for the tier that says critical.
ROUTES: dict[str, tuple[str, str | None]] = {
    "mechanical": ("claude-code", "sonnet"),
    "standard": ("claude-code", "sonnet"),
    "design": ("claude-code", "sonnet"),
    "critical": ("codex", "gpt-6-astra"),
}

# Prepended to every dispatched prompt. Measured cost per session was about 355,000 input tokens, and most of that
# was an agent reading the repository to orient itself rather than reading the files its task named. An agent that
# is told what to read does not have to go looking.
SCOPE_PREAMBLE = """Work only within the files this task names, plus whatever they import. Do not survey the
repository, do not read directories that the task does not mention, and do not run repository-wide searches to
orient yourself: the task below already names what you need. If you genuinely cannot proceed without reading
something unnamed, read that one thing and say in your final message what it was and why.

"""
TIERS = tuple(ROUTES)


@dataclass(frozen=True)
class SwarmTask:
    spec: TaskSpec
    tier: str = "standard"
    why: str = ""

    def route(self) -> tuple[str, str | None]:
        """The static fallback. `run_wave` prefers the router, which chooses from measured outcomes; this is what
        remains when no routing configuration can be read."""
        if self.tier not in ROUTES:
            raise ValueError(f"unknown tier {self.tier!r}; expected one of {', '.join(TIERS)}")
        return ROUTES[self.tier]


def uncommitted(root: Path) -> list[str]:
    """Files changed but not committed. An agent's worktree branches from HEAD, not from the working tree, so a
    task that depends on uncommitted work is dispatched against a base where that work does not exist. This cost a
    real task once: a module was committed moments after the swarm launched, and the agent that imported it failed
    validation through no fault of its own."""
    p = subprocess.run(["git", "status", "--porcelain"], cwd=root, capture_output=True, text=True)
    return [ln[3:] for ln in p.stdout.splitlines() if ln.strip() and not ln.startswith("??")]


def plan(tasks: list[SwarmTask]) -> tuple[list[list[SwarmTask]], list[tuple[str, str]]]:
    """Group tasks into waves that can run together.

    A wave holds tasks whose declared paths are provably disjoint. Anything that conflicts is deferred to a later
    wave rather than dropped, so a conflict costs sequencing rather than work.
    """
    remaining = list(tasks)
    waves: list[list[SwarmTask]] = []
    conflicts: list[tuple[str, str]] = []
    while remaining:
        wave: list[SwarmTask] = []
        deferred: list[SwarmTask] = []
        for t in remaining:
            clash = overlapping([w.spec for w in wave] + [t.spec])
            if clash and any(t.spec.task_id in pair for pair in clash):
                deferred.append(t)
                conflicts.extend(pair for pair in clash if t.spec.task_id in pair)
            else:
                wave.append(t)
        waves.append(wave)
        if len(deferred) == len(remaining):  # nothing could be scheduled; stop rather than spin
            waves.append(deferred)
            break
        remaining = deferred
    return [w for w in waves if w], sorted(set(conflicts))


def _verdict_text(verdict: Verdict) -> str:
    return " ".join(verdict.reasons)


def _hit_a_usage_limit(agent_id: str, verdict: Verdict) -> bool:
    """Did this run fail because the account is spent rather than because the work was wrong?

    `dispatch` decides this while it still holds the agent's whole output and records it on the verdict. Reading
    the rejection reason instead, as this used to, meant classifying a 200-character slice taken from the front
    of the output — and a vendor announces a usage limit at the end, after echoing the prompt. Astra hit its
    limit for real and four tasks were recorded as having failed on their merits.

    The reason text is still consulted, for a verdict built somewhere other than `dispatch`.
    """
    if verdict.accepted:
        return False
    if verdict.limited:
        return True
    return availability.classify(agent_id, _verdict_text(verdict), 1) == "limited"


MAX_FALLBACKS = 4
"""How many rate-limited routes to walk past before giving up. Bounded so a table where every route reports a
limit ends the task with a reason rather than dispatching forever."""


def _parse_reset(verdict: Verdict) -> datetime | None:
    """When the vendor said this account returns, from the verdict `dispatch` already parsed it into."""
    raw = getattr(verdict, "resets_at", "")
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError:
        return None


def _retry_elsewhere(
    build_agent: Any, t: SwarmTask, root: Path, table: Any, rows: list[Any],
    chosen: dict[str, str], chosen_agent: dict[str, str], results: list[Verdict], *, log: Any,
) -> Verdict:
    """Cool the limited agent down, then run the same task on the best route that is not cooling.

    This is what the operator means by a sentinel: when the paid accounts are spent, the free-tier and local
    routes carry the work rather than the loop stopping until someone notices.
    """
    spent = chosen_agent.get(t.spec.task_id, "")
    last = results[-1] if results else Verdict(t.spec.task_id, spent, False)

    # Walk the chain rather than stepping once. This dispatched a single fallback and returned whatever came
    # back, so a fallback that was itself rate limited ended the task — the loop stopping quietly, which is the
    # one thing the sentinel exists to prevent. It never showed while there was exactly one free route below the
    # paid ones. With a tool loop and a single-shot writer both sitting there, one step is not enough.
    for _ in range(MAX_FALLBACKS):
        # Prefer the vendor's own stated return time over a fixed window. A guess made in the presence of the
        # answer held the strongest model out of rotation for half an hour after it had already come back.
        stated = _parse_reset(last)
        availability.mark_limited(root, spent, until=stated)
        continuity.note(root, kind="limited", summary=f"{spent} hit a usage limit on {t.spec.task_id}",
                        agent=spent, detail=_verdict_text(last))
        if table is None:
            return Verdict(t.spec.task_id, spent, False, [f"{spent} is rate limited and no routing table is loaded"])
        try:
            choice = routing.choose(table, rows, t.tier, root=root)
        except routing.RoutingError as e:
            return Verdict(t.spec.task_id, spent, False, [f"{spent} is rate limited and no fallback route exists: {e}"])
        agent_name, model = choice.route.pair()
        if agent_name == spent:
            return Verdict(t.spec.task_id, spent, False,
                           [f"{spent} is rate limited and it is the only route at this tier"])
        agent = build_agent(agent_name, model)
        if agent is None:
            return Verdict(t.spec.task_id, spent, False,
                           [f"{spent} is rate limited and the fallback {agent_name} is not available here"])
        log(f"fallback {t.spec.task_id}: {spent} is rate limited -> {choice.route.id} ({choice.reason})")
        continuity.note(root, kind="fallback", summary=f"{t.spec.task_id} moved from {spent} to {choice.route.id}",
                        agent=agent_name)
        chosen[t.spec.task_id] = choice.route.id
        chosen_agent[t.spec.task_id] = agent_name
        verdict = dispatch(agent, replace(t.spec, prompt=SCOPE_PREAMBLE + t.spec.prompt), log=log)
        if not _hit_a_usage_limit(agent_name, verdict):
            return verdict
        spent, last = agent_name, verdict

    return Verdict(t.spec.task_id, spent, False,
                   [f"every route at the {t.tier} tier is rate limited after {MAX_FALLBACKS} attempts"])


def _note_verdict(root: Path, verdict: Verdict) -> None:
    """Append one line to the agent trace for a finished task."""
    body = (f"touched {len(verdict.files)} file(s): {', '.join(verdict.files)}"
            if verdict.accepted else "; ".join(verdict.reasons) or "rejected, no reason recorded")
    continuity.note(
        root, kind="accepted" if verdict.accepted else "rejected",
        summary=f"{verdict.task_id} {'accepted' if verdict.accepted else 'rejected'} by {verdict.agent}",
        detail=body[:600], agent=verdict.agent,
    )


def run_wave(
    build_agent: Any, wave: list[SwarmTask], *, log: Any = print, root: Path | None = None,
    blackboard: bool = False, wave_id: str = "default",
) -> list[Verdict]:
    """Dispatch one wave in parallel. Each task gets its own agent instance and its own worktree.

    When a workspace root is given, the route for each task is chosen by `application/routing.py` from the outcomes
    already recorded there, and this wave's outcomes are appended to that log. Without a root the static ROUTES
    table decides and nothing is recorded, which is what keeps the function usable in a test.

    When `blackboard` is true and a root is given, each task's brief also gains a peer briefing -- what earlier
    tasks in this run already found, warned about, or established as a convention, from
    `application/blackboard.py::digest` -- and once a task completes, its verdict's reasons and touched files are
    posted back as a finding so later waves inherit them. `wave_id` scopes the blackboard file so several waves of
    the same run share one board; callers that pass `blackboard=False` (the default) see exactly the previous
    behaviour, since nothing here is read or written.
    """
    results: list[Verdict] = []
    chosen: dict[str, str] = {}
    chosen_agent: dict[str, str] = {}
    table = None
    rows: list[Any] = []
    if root is not None:
        try:
            table = routing.load_table()
            rows = routing.outcomes(root)
        except (OSError, routing.RoutingError) as e:  # a bad table must not stop the work
            log(f"routing table unavailable ({e}); falling back to the static table")
            table = None
    briefing = ""
    if blackboard and root is not None:
        briefing = blackboard_mod.peer_briefing(blackboard_mod.digest(root, wave_id, 2000))
    with ThreadPoolExecutor(max_workers=max(1, len(wave))) as pool:
        futures = {}
        for t in wave:
            agent_name: str
            model: str | None
            if table is not None:
                choice = routing.choose(table, rows, t.tier)
                agent_name, model = choice.route.pair()
                chosen[t.spec.task_id] = choice.route.id
                log(f"route {t.spec.task_id} [{t.tier}] -> {choice.route.id}: {choice.reason}")
            else:
                agent_name, model = t.route()
            chosen_agent[t.spec.task_id] = agent_name
            agent = build_agent(agent_name, model)
            if agent is None and table is not None:
                # An unrunnable route hands its work on, exactly as a rate-limited one does. Those two conditions
                # were treated differently for no reason anyone stated: a vendor limit fell back through the
                # routing table, while "this CLI is not on my PATH" returned a rejection and stopped. That gap
                # cost five hours on 2026-09-08 — `systemd --user` has no nvm bin directory, so `opencode` was
                # invisible to the heartbeat, which rejected every dispatch hourly while `claude-code` sat ready.
                # Cost order is preserved because the table is re-asked with the dead route excluded, so the
                # replacement is the next cheapest usable seat rather than whatever happens to be first.
                for candidate in routing.permitted_after(table, rows, t.tier, exclude={agent_name}, root=root):
                    replacement, replacement_model = candidate.pair()
                    agent = build_agent(replacement, replacement_model)
                    if agent is not None:
                        log(f"route {t.spec.task_id}: {agent_name} cannot run here -> {candidate.id}")
                        if root is not None:
                            continuity.note(
                                root, kind="fallback",
                                summary=f"{t.spec.task_id} moved from {agent_name} to {candidate.id}",
                                detail=f"{agent_name} is not runnable on this host", agent=replacement,
                            )
                        agent_name, model = replacement, replacement_model
                        chosen[t.spec.task_id] = candidate.id
                        chosen_agent[t.spec.task_id] = replacement
                        break
            if agent is None:
                results.append(
                    Verdict(task_id=t.spec.task_id, agent=agent_name, accepted=False,
                            reasons=[f"no agent available for tier {t.tier} ({agent_name}), and no other route "
                                     f"at this tier can run here either"])
                )
                continue
            log(f"dispatch {t.spec.task_id} -> {agent.name} [{t.tier}] {model or 'default'}"
                f"{' ' + t.why if t.why else ''}")
            scoped = replace(t.spec, prompt=SCOPE_PREAMBLE + briefing + t.spec.prompt)
            futures[pool.submit(dispatch, agent, scoped, log=log)] = t
        for fut in as_completed(futures):
            t = futures[fut]
            try:
                verdict = fut.result()
                rid = chosen.get(t.spec.task_id)
                limited = root is not None and _hit_a_usage_limit(chosen_agent.get(t.spec.task_id, ""), verdict)
                if limited and root is not None:
                    # A usage limit says nothing about the quality of the route, so it must not be recorded as a
                    # loss; it says the account is spent for now. Cool that agent down and hand the task to the
                    # cheapest route still standing, which is what keeps the loop running unattended.
                    verdict = _retry_elsewhere(
                        build_agent, t, root, table, rows, chosen, chosen_agent, results, log=log
                    )
                    rid = chosen.get(t.spec.task_id)
                results.append(verdict)
                if root is not None:
                    # What this agent actually did, in the one chronological place a person can read across a
                    # whole wave. Adapted from OpenClaw, whose dashboard shows each agent's messages as rounds
                    # run; this engine dispatched agents and recorded only limits and fallbacks, so the record
                    # of the work itself existed nowhere a reader could follow.
                    #
                    # Guarded separately, and that is the point rather than caution. Written bare inside this
                    # try, a rejected note raised and the wave's own `except` turned it into a second verdict
                    # reading "dispatch raised" — a side record inventing an outcome for work that had already
                    # succeeded. A record of what happened must never be able to change what happened.
                    try:
                        _note_verdict(root, verdict)
                    except Exception as note_error:  # noqa: BLE001
                        log(f"trace: not recording {verdict.task_id} ({note_error})")
                if root is not None and rid is not None and not limited:
                    routing.record_outcome(root, routing.Outcome(
                        tier=t.tier, route_id=rid, task_id=t.spec.task_id,
                        accepted=verdict.accepted, wall_s=verdict.wall_s,
                        tokens=getattr(verdict, "tokens", None),
                        cost_usd=getattr(verdict, "cost_usd", None),
                        cache_read_tokens=getattr(verdict, "cache_read_tokens", None),
                        cache_write_tokens=getattr(verdict, "cache_write_tokens", None),
                    ))
                if blackboard and root is not None:
                    if verdict.accepted:
                        body = f"touched {len(verdict.files)} file(s): {', '.join(verdict.files) or 'none'}"
                    else:
                        body = "; ".join(verdict.reasons) or "rejected with no reason recorded"
                    try:
                        blackboard_mod.post(
                            root, wave_id, author=verdict.agent, kind="finding",
                            subject=f"{verdict.task_id}: {'accepted' if verdict.accepted else 'rejected'}",
                            body=body, refs=tuple(verdict.files),
                        )
                    except blackboard_mod.BlackboardError as e:  # a note that reads as a ledger number must not stop the wave
                        log(f"blackboard: not posting {verdict.task_id}'s finding ({e})")
            except Exception as e:  # an agent crashing must not take the wave with it
                results.append(
                    # Name the agent that was actually routed, not the static fallback: a crash record that blames the
                    # wrong agent teaches the router the wrong lesson.
                    Verdict(task_id=t.spec.task_id, agent=chosen_agent.get(t.spec.task_id, t.route()[0]),
                            accepted=False, reasons=[f"dispatch raised: {e}"])
                )
    return results


def run_swarm(
    build_agent: Any, tasks: list[SwarmTask], *, root: Path | None = None, log: Any = print
) -> dict[str, Any]:
    if root is not None:
        dirty = uncommitted(root)
        if dirty:
            log(
                f"swarm: WARNING {len(dirty)} uncommitted file(s); agent worktrees branch from HEAD, so a task "
                f"depending on them will fail against a base that lacks them: {', '.join(dirty[:5])}"
            )
    waves, conflicts = plan(tasks)
    log(f"swarm: {len(tasks)} tasks in {len(waves)} wave(s); {len(conflicts)} conflict pair(s) deferred")
    verdicts: list[Verdict] = []
    for i, wave in enumerate(waves, 1):
        log(f"--- wave {i}: {', '.join(t.spec.task_id for t in wave)}")
        verdicts.extend(run_wave(build_agent, wave, log=log))
    accepted = [v for v in verdicts if v.accepted]
    log(f"swarm: {len(accepted)}/{len(verdicts)} accepted")
    return {
        "waves": len(waves),
        "conflicts": conflicts,
        "accepted": [v.to_dict() for v in accepted],
        "rejected": [v.to_dict() for v in verdicts if not v.accepted],
    }


def render_swarm(result: dict[str, Any]) -> str:
    lines = [f"swarm: {len(result['accepted'])} accepted, {len(result['rejected'])} rejected, {result['waves']} wave(s)"]
    for v in result["accepted"]:
        lines.append(f"  ok    {v['task_id']:16} {v['agent']:22} {len(v['files'])} file(s)  {v['wall_s']}s")
    for v in result["rejected"]:
        lines.append(f"  FAIL  {v['task_id']:16} {v['agent']:22} {'; '.join(v['reasons'])[:90]}")
    return "\n".join(lines)


__all__ = ["uncommitted", "ROUTES", "TIERS", "SwarmTask", "plan", "run_wave", "run_swarm", "render_swarm", "TaskSpec",
    "json", "replace"]
