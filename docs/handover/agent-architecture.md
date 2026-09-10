# Handover: multi-agent architecture, arXiv:2609.09153, and knowledge graphs

For a running session picking up this strand and delegating the rest to Studio and Product. Written to be read
once, top to bottom. Companion to `docs/handover/README.md` — read that first if you haven't; this document
assumes it. The decision record with full citations is
`docs/superpowers/specs/2026-09-10-agent-architecture-survey.md` (local-only, gitignored, like every spec) —
read it before changing anything this document describes as "not built" or "proposed."

## 1. The one-paragraph answer

The operator has asked twice now (`r-4b5cdaf1`, 2026-09-09; this session, 2026-09-10) how much of "multi-agent
architectures like LangGraph, OpenAI/Claude/Google agent SDKs" has been explored and implemented. The honest
answer: **thoroughly explored, deliberately not adopted as frameworks, and substantially reimplemented
homegrown instead**, for reasons specific to this project's evidence rules — not from not having looked. A
13-agent research fan-out on 2026-09-09 (workflow `wf_dff1f898-6ab`, 935,592 tokens) evaluated LangGraph and
AutoGen specifically and produced a written refusal with reasons in
`docs/superpowers/specs/2026-09-09-dispatch-cost-instrumentation.md` §3. This session extended that evaluation
to the OpenAI Agents SDK, the Claude Agent SDK, and Google ADK by name (none were named before), read the paper
at arXiv:2609.09153, and closed the knowledge-graph question the same way: researched, and explicitly not
adopted, with one narrow exception that was actually built and tested (§5).

## 2. What already runs: the homegrown multi-agent stack

This is not greenfield. Before you propose building "an agent architecture," read what is already dispatching
work, right now, across roughly **85 concurrent git worktrees and 129 branches** (`git worktree list`,
`git branch -a`, counted 2026-09-10 — recount before you rely on this number, it moves fast):

| Layer | File(s) | What it does |
|---|---|---|
| Routing | `src/pravrudhi/application/routing.py` | Chooses agent + model per tier from measured outcomes (Wilson CI on success rate vs. cost), not a hardcoded table. `configs/routing.yaml` declares the permitted routes per tier; the router never widens permissions past what's declared. |
| Dispatch | `src/pravrudhi/application/swarm.py` | Wave-plans tasks with disjoint-path safety, dispatches, classifies a failure as a usage limit vs. a transport stumble vs. an ordinary rejection, and falls back to another route with a cooldown (`_retry_elsewhere`) rather than stopping. |
| Availability | `src/pravrudhi/application/availability.py` | `classify()` — `"ok" \| "limited" \| "transient" \| "failed"`, config-driven patterns, per-agent cooldown file. |
| Delegation scope | `src/pravrudhi/application/delegate.py` | `TaskSpec` declares `allowed_paths`; overlapping tasks are refused; a diff outside scope or touching a protected path is rejected whole, validated in the worktree before merge. |
| Agent fleet | `src/pravrudhi/agents/` | `registry.py` builds adapters: `ClaudeCodeAgent`, `CodexAgent`, `AlibabaAgent` (OpenCode), `OrcaAgent`, `HostedAgent`. `pravrudhi agents` surveys what's actually runnable. |
| Machine fleet | `src/pravrudhi/hosts/` | `base/fleet/probe/transports`; local/ssh/orca transports are interchangeable. `configs/hosts.yaml` currently has one entry (`mac-mini`). |
| Model access | `src/pravrudhi/models/openai_compat.py` | The **only** path to a model: a stdlib-only `ChatClient` against an OpenAI-compatible endpoint. No `anthropic`, `openai`, or `google.generativeai` package is imported anywhere in `src/` or `pravrudhi_kernel/` — confirmed by grep, not assumed. |
| Extension point | `src/pravrudhi/targets/base.py` | The `Target` protocol — `surfaces()`, `baseline()`, `materialise()`, `train_job()`, `canaries()` — is the one place a new kind of improvable thing plugs in. Two implementations: `targets/lora_grammar.py`, `targets/harness_grammar.py`. |

**Two editions, one codebase.** Studio and Product are not separate apps — `src/pravrudhi/api/edition.py`
reads `PRAVRUDHI_EDITION`; `app/desktop/lib/edition.js` mirrors it for the Electron shell. Both run the whole
improvement loop; the difference is only what it's pointed at (Studio improves Pravrudhi itself; Product
improves the user's own work). "Delegate to Studio and Product" means: hand each edition's own running loop
(its heartbeat, its requests queue) a scoped task through the same request/criteria mechanism the operator
already uses — not build a second delegation channel.

## 3. Why LangGraph / AutoGen / OpenAI Agents SDK / Claude Agent SDK / Google ADK are not vendored

Read `docs/superpowers/specs/2026-09-10-agent-architecture-survey.md` §3 for the full table and citations. In
one line each:

- **LangGraph** — refused. Its mutable-state reducers merge destructively; this project's pramāṇa tags exist
  to carry provenance through every state change, and a destructive merge loses that.
- **AutoGen** — refused. Broadcast group chat re-sends a transcript the ledger already holds once — O(N·M)
  cost for a copy that already exists.
- **OpenAI Agents SDK** — not adopted. Its runtime owns control flow and session state; that is a second
  source of truth about what happened during a dispatch, and CHARTER §6 says evidence comes only from the
  kernel.
- **Claude Agent SDK** — not adopted. It wraps the same single-agent tool loop `agents/cli_agents.py` already
  drives by shelling out to the `claude` CLI and parsing its JSON envelope directly (including `usage` and
  `cost_usd`, landed as card M6.1). Adopting it would add an abstraction over a call this engine already makes.
- **Google ADK** — genuinely unevaluated, not refused. No live Gemini credential exists yet in
  `configs/panel.yaml`'s `google-api` entry to trial it against. This is an open gap, recorded as such — don't
  let anyone tell you it was rejected on principle, it wasn't reached.

What **was** taken from the framework literature, as ideas rather than as vendored code (2026-09-09 spec §3):
SWE-agent's structured-reference / paginated-query pattern, Aider's stable/varying/uncached prompt tiering, and
isolated sub-evaluator context summaries. None of these required adopting a framework; all fit inside the
existing flat-dispatch design.

## 4. arXiv:2609.09153 — "Procedural Graphs: Self-Evolving Execution Structures for LLM Agents"

Lu, Chen, Wu, Arık; submitted 2026-09-08; no public code repository exists yet (checked; the submission is two
days old). It represents an agent's task structure as `(procedure, relation, procedure)` triplets and has an
LLM component rewrite the graph in place from observed trajectories, keeping failed edits on record. The
in-place rewrite is the same risk shape as LangGraph's mutable state reducers (§3) — refused for the same
reason if adopted as written. The triplet *representation* is useful on its own, replayed read-only rather than
mutated; that's what §5 built.

## 5. Knowledge graphs — researched, not adopted, with one exception already built

**No knowledge-graph memory system exists or is planned.** This project's memory architecture
(`docs/blueprint/02-design/08-memory-and-context.md`) is a three-tier event-sourced design — an append-only
ledger, a replayed posterior, and a typed key-value claim store with a provenance chain — and a general graph
database would throw away exactly the property (a pramāṇa tag on every claim) that makes the current design
trustworthy. Don't build one without a concrete need that the existing typed-KV query surface has actually
failed to serve.

**The one graph that is legitimate here already exists as a byproduct of the routing log**, and this session
built it: `src/pravrudhi/application/procedure_graph.py`. `build_graph(root)` replays
`.pravrudhi/routing.jsonl` — which already records every route a dispatched task moved to and why — into a
`ProcedureGraph` of `(from_route, to_route, reason)` edges with counts and acceptance rates. It is read-only
(rebuilt fresh every call, no mutation method exists) and it is a **report**, not a control input — nothing in
`swarm.py` calls it yet. Tests: `tests/test_procedure_graph.py`, 8 cases, all passing; run against the real
161-row log on this machine, it correctly reproduced the `qwen-lite-max → sonnet` fallback already described in
`HANDOFF.md`'s 2026-09-09 section. Full detail and the exact test command are in the spec, §5.

## 6. What to actually delegate next

In priority order, each already scoped enough to hand to a session without further research:

1. **Card M6.3 — the tier model.** Blocked on cost being observable; M6.1 landed 2026-09-09, so this is now
   unblocked. Express it in the existing `Route`/`Outcome`/`Table` vocabulary per the spec — do not invent new
   vocabulary. Owner: whichever edition's loop next has budget; this is engine-wide, not edition-specific.
2. ~~**Card M6.4 — wire `procedure_graph.suggest_next` into `routing.choose`.**~~ **DEFERRED 2026-09-10, on
   evidence.** The decision this asked for is settled, and settled against wiring it — for now.

   The graph is a **re-aggregation of the same `routing.jsonl` that `choose` already reads**, not a new source
   of information. It offers a different view (transition A→B fared better than A→C) which per-route rates
   cannot express — but on this machine's 161-row log that view is **3 edges of exactly 1 observation each**,
   against per-route counts of sonnet 107 and astra 32. A tie-breaker backed by single observations is the n=1
   inference the sequential boundary exists to prevent everywhere else in this engine, and it would be driving
   dispatch.

   The criterion for revisiting is runnable rather than prose: `procedure_graph.tiebreak_readiness(graph)`
   reports `ready`, the best edge count, and what is missing. `test_the_live_routing_log_is_not_yet_ready_to_
   tie_break` asserts it is not ready against the real log — **so that test failing is the signal to pick this
   card back up.** No re-research needed; just run it.
3. ~~**Reconcile M6.2.**~~ **DONE 2026-09-10.** The spec now carries the deviation. `ed6ac7a` shipped the
   transient class and the config-driven patterns as asked and deliberately did not build the backoff,
   because §6 of that same spec had already noted the ceiling was unjustified while DashScope's reset window
   is unread — a 60s ceiling against a 5-minute window retries into certain failure. Fallback-to-a-working-seat
   plus a short cooldown gets the work done instead. **Do not "finish" the backoff**: the spec's ask revives
   only if the reset window becomes known.
4. **Google ADK** — if a Gemini credential becomes available, evaluate it properly rather than leaving it as
   the unknown it is today.
5. **Knowledge graphs** — no action needed unless a concrete query the avacchedaka-store can't serve shows up.
   Don't let "we should look into knowledge graphs" resurface as a request without that concrete failure
   attached; this document is the answer to that question as it stands 2026-09-10.

## 7. Before you touch `swarm.py`, `routing.py`, or `availability.py`

Recount `git worktree list` and `git branch -a` first. At 85 worktrees / 129 branches on 2026-09-10, several
plausibly already touch these exact files (`agent-limit-aware-routing`, `agent-swarm-launch`,
`agent-fleet-visibility`, `agent-vardhana` are worktree names that suggest as much — verify, don't assume from
the name alone). `procedure_graph.py` was deliberately written as a **new file with zero edits to existing
files** for exactly this reason: it can't conflict with concurrent work on the dispatch seam. Any card that
promotes it to a control input (M6.4) will need to touch `routing.py`, and should be coordinated through the
requests/criteria mechanism the rest of this project already uses for exactly this kind of collision risk, not
dispatched blind.

## 8. Where things live

- Decision record with citations: `docs/superpowers/specs/2026-09-10-agent-architecture-survey.md` (local)
- Prior decision this extends: `docs/superpowers/specs/2026-09-09-dispatch-cost-instrumentation.md` (local)
- New code: `src/pravrudhi/application/procedure_graph.py`
- New tests: `tests/test_procedure_graph.py`
- Memory architecture reference: `docs/blueprint/02-design/08-memory-and-context.md`
- Edition split: `src/pravrudhi/api/edition.py`, `app/desktop/lib/edition.js`
- This document: `docs/handover/agent-architecture.md` (public, tracked — no secrets, matches
  `docs/handover/README.md`'s existing convention)
