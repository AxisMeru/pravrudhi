# UX / feature-parity backlog — closing the gap with Orca and reaching product-desk quality

Compiled 2026-09-06 by running the app (systemd `pravrudhi-app`, http://127.0.0.1:8200, engine v0.2.3), reading
`~/opt/orca` and the NemoClaw/OpenClaw skill docs (the closest documented analog to Orca's vocabulary present on
this machine — no `orca`-named skill exists locally), reading `src/pravrudhi/agents/orca_agent.py` and
`cli_agents.py`, and diffing the engine's own OpenAPI surface (43 paths) against the 11 pages the frontend
actually renders. Screenshots taken with Playwright live in the scratchpad used for this pass, not committed here.

Every item below names the concrete evidence it rests on: the endpoint, the file, or the exact behavior observed
in the running app. Nothing here is speculative UI polish disconnected from what the engine already computes —
the theme, repeatedly, is that the kernel and API already do the work and the interface simply never asked for it.

## How the ranking works

Ranked for a user who wants to improve their own model and *watch it happen*, with the items that would make that
person choose Pravrudhi over Orca at the top. Orca's proven vocabulary — from `orca_agent.py`'s own words, "worktree
lifecycle, terminal sessions, diff review, several agents visible side by side" — plus the NemoClaw/OpenClaw
patterns (sandbox status, live network-approval TUI, operator approval flow, `nemoclaw list`/`status`/`logs`,
messaging-channel management, remote deploy) are the concrete competitor reference points used throughout, since no
`orca`-specific skill is installed locally to quote instead.

---

## Tier 0 — currently broken (fix before anything else ships)

### 1. `/heartbeat` is dead in production right now
**What a user cannot do today:** open the Heartbeat page at all. It renders a Next.js client-side crash screen
("This page couldn't load — Reload to try again") on every visit, with real data behind it.
**Root cause, confirmed by instrumented Playwright run:**
`TypeError: Cannot read properties of undefined (reading 'toFixed')` thrown from
`_next/static/chunks/3e-490ab65q31.js`, which decompiles to `e.result.wall_s.toFixed(1)` — **unguarded**. The
*source* tree already has the fix (`app/frontend/src/components/heartbeat/HeartbeatTimeline.tsx:44`:
`(beat.result.wall_s ?? 0).toFixed(1)`), but the **deployed build being served does not** — it's stale relative to
source. Separately, even a redeploy only papers over a real contract mismatch: `GET /api/heartbeat` (live,
verified via curl) returns `result` objects shaped `{accepted, files, reasons, route}` — there is no `wall_s` key
at all, and the field is `route`, not `agent`. `HeartbeatTimeline.tsx` reads `beat.result.agent` (always renders
blank) and `beat.result.wall_s` (always falls back to a fabricated "0.0s" once patched) — neither matches what the
engine actually sends.
**Competitor pattern:** none needed — this is table-stakes "the page loads."
**Fix:** (a) rebuild and redeploy the frontend so the running bundle matches source; (b) fix the actual type
mismatch — `HeartbeatResult` in `app/frontend/src/lib/heartbeat.ts` should read `route` (matching the API) and
either the backend should start emitting `wall_s` for heartbeat dispatch results or the field should be dropped
from the timeline instead of silently showing "0.0s"; (c) add a Playwright/e2e regression that loads `/heartbeat`
against a fixture with a real (`wall_s`-less) beat, so this class of drift is caught before deploy, not by a user.
**Files:** `app/frontend/src/components/heartbeat/HeartbeatTimeline.tsx`, `app/frontend/src/lib/heartbeat.ts`,
`src/pravrudhi/api/server.py` (heartbeat endpoint), the deploy step that builds `app/frontend`.
**Size:** 3–5h engineering + a deploy. Flag as urgent: this is a fully broken page in the running product, not a
missing feature.

---

## Tier 1 — close the loop (these are what would make someone choose Pravrudhi over Orca)

### 2. No way to sign off the inbox — the engine's entire promotion model is CLI/curl-only
**What a user cannot do today:** approve, reject, or defer a candidate. Right now, live: `GET /api/inbox` returns
**three real pending packs** (`harness-night1/c-0060` amber, `harness-night3/c-0078` green, and one more), each
with a `POST /api/inbox/sign` waiting on a human decision — this is not hypothetical data, it is sitting there
right now, unreachable from the browser. CHARTER §6 makes this the load-bearing human act ("promotion to
canonical... is a human act via the inbox") and there is no page for it at all — `/api/inbox` has zero frontend
consumers (`grep` across `app/frontend/src` confirms no callers of the inbox endpoints).
**Why it's #1:** every other improvement in this backlog is downstream of a loop that can run; this is the one
step in the loop that structurally *requires* a human, and today that human needs a terminal and `curl` with a
hand-set `X-Pravrudhi-Operator` header. This is also the single feature that, if missing, makes the whole "night"
narrative (`pravrudhi night --budget N`) unfinishable from the app.
**Competitor pattern:** NemoClaw/OpenShell's live operator-approval TUI (`openshell term`, "Blocked egress requests
awaiting operator approval" — see `nemoclaw-user-monitor-sandbox` and `nemoclaw-user-manage-policy` skills) is the
closest documented analog: a queue of pending decisions, each approve/deny/defer, with the reason visible before
you decide.
**Build:** new `/inbox` page — list of pending packs (badge color, night, candidate id) each expanding into the
evidence backing it (`GET /api/evidence/{name}` already serves markdown), an operator-name field (the backend
enforces `X-Pravrudhi-Operator` must be a real human name, not an agent identity — the frontend needs a place to
capture and remember that name, e.g. in Settings, not re-typed per decision), and Approve/Reject/Defer buttons
wired to `POST /api/inbox/sign`.
**Endpoints:** `GET /api/inbox`, `GET /api/evidence/{name}`, `POST /api/inbox/sign`.
**Size:** 16–24h (list + evidence detail view + sign flow + operator-identity capture in Settings).

### 3. Swarm is entirely read-only — nothing can be launched, watched live, or stopped from it
**What a user cannot do today:** start an agent from the Swarm page. Confirmed on screen: "Live — No agent process
is running right now" with no button anywhere nearby to change that; the Fleet table shows five agents all
"available" with no per-row action; "Recent dispatches" is a history list, not a control surface. Contrast with
Objectives, which *does* have a working per-objective Dispatch button (`app/frontend/src/app/objectives/page.tsx:
420–483`, calling `POST /api/objectives/{oid}/subagents`) — so the dispatch machinery exists and works, it is just
absent from the one page whose entire stated purpose ("every agent... what is running right now") is to be the
control room for it.
**Competitor pattern:** Orca's own module docstring (`orca_agent.py`) states the exact shape being reused headlessly
here — "worktree lifecycle, terminal sessions, diff review... several agents visible side by side" — Orca's UI
lets you start a session against an agent, watch its terminal live, and see the diff when it finishes. Swarm has
the fleet and routing tables but none of the start/watch/diff triad.
**Build:** on `/swarm`, add (a) a "run ad hoc task" affordance per fleet row or above the Live panel, (b) wire
`LivePanel.tsx` to actually reflect an in-flight run once one exists (it currently only has a static "not running"
state to render), (c) surface the diff for an accepted dispatch — `proposals/code-harness/...` paths are already
printed as flat strings in "Recent dispatches"; make them clickable into a real diff view.
**Endpoints:** `POST /api/objectives/{oid}/subagents` (reusable), `GET /api/swarm/live`, `GET /api/runs/{run_id}`.
**Files:** `app/frontend/src/app/swarm/page.tsx`, `app/frontend/src/components/swarm/LivePanel.tsx`,
`app/frontend/src/components/swarm/DispatchesTable.tsx`.
**Size:** 20–28h (dispatch control + live wiring + a first diff viewer; the diff viewer is the expensive part and
overlaps with item 9 below).

### 4. The candidate population — 188 real, kernel-scored vectors — is reduced to four colored numbers
**What a user cannot do today:** watch their model actually improve, candidate by candidate, which is the single
thing the operator described wanting ("watch it run" is literally the Improve page's own subtitle). Live data:
`GET /api/candidates` returns 188 full records right now, each with a `bucket` (task family / target model /
corpus), an `edit_family`, a `proposed_seq` (lineage order), and an `xs` vector (the actual LoRA edit); `GET
/api/candidates/{cid}` gives the same detail for one. All of this is reduced, on the Improve page, to four pills:
"grey 19 · amber 13 · green 2 · red 154." No page lets you click a candidate, see what it changed, why it was
rejected, or watch the population evolve night over night.
**Why this is the differentiator:** no competitor pattern applies here — Orca, Claude Desktop, and the
NemoClaw/OpenClaw stack are all agent-session tools; none of them run a sealed-pool selection loop over scored
model variants. This is Pravrudhi's own kernel output and nobody else has this screen to copy, which is exactly
what "no adaptation of Orca or OpenClaw ideas" should look like in practice.
**Build:** a `/candidates` view (or a rebuilt Improve-page side panel) — a scatter/beeswarm of the 188 candidates
colored by badge (grey/amber/green/red, matching the existing badge vocabulary), filterable by bucket and
edit_family, each point opening a detail panel with its `xs` summary, lineage (`proposed_seq`), and the
observations that scored it (`GET /api/observations` already carries `candidate_id`, `payload.observed.value`,
`cost_gpu_h`). A legend explaining what grey/amber/green/red mean (this exists nowhere in the UI today, not even a
tooltip).
**Endpoints:** `GET /api/candidates`, `GET /api/candidates/{cid}`, `GET /api/observations`.
**Size:** 24–32h (visualization is the bulk of it; the data is already there and typed).

### 5. Stating an objective requires already knowing the exact benchmark string — there is no intent→plan flow
**What a user cannot do today:** describe what they want in plain words and have the system figure out how to
measure it. The "State an objective" form (`app/frontend/src/app/objectives/page.tsx:583–700`) is real and
functional — it POSTs to `/api/objectives` — but its "Benchmark metric" field has placeholder text like
`mmlu_professional_law acc,none` and gives no help finding or validating that string; a user who doesn't already
know `lm-evaluation-harness` metric syntax cannot complete the form. Meanwhile the backend already knows how to
turn an objective into a plan (`GET /api/objectives/{oid}/plan`, calling `compile_intent(...)`) and into Loom
source (`GET /api/objectives/{oid}/loom`) — but only *after* the objective is created and saved, never as part of
composing it, so a user commits blind and only discovers the compiled plan by expanding a collapsed section
afterward.
**Competitor pattern:** the transferable idea (not the tool) from NemoClaw's onboarding wizard
(`nemoclaw-user-get-started` skill) is the "review configuration before you commit" screen — it lays out exactly
what will happen (provider, model, policy tier) and asks "Apply this configuration? [Y/n]" before doing anything
irreversible. Pravrudhi's objective form has no equivalent preview step.
**Build:** a guided composer — free-text intent first, then a searchable benchmark/metric picker seeded from
benchmarks the engine has already scored (visible today across the Objectives/Progress pages: `humaneval+`,
`mbpp+`, `gsm8k`, `mmlu_professional_law`, `mmlu_jurisprudence`), then a review step that renders the compiled plan
and Loom source inline (reusing the already-built `PlanView`/`LoomView` components at
`objectives/page.tsx:181–330`) before the final "Record it."
**Endpoints:** `POST /api/objectives`, `GET /api/objectives/{oid}/plan`, `GET /api/objectives/{oid}/loom`.
**Size:** 16–20h.

---

## Tier 2 — real backend data with zero frontend surface

### 6. Tools & recipes catalogue has no page
**Live data:** `GET /api/tools` → 12 entries (agent-claude-code, agent-codex, agent-orca, model-llamacpp,
runtime-docker, runtime-uv, ...), each with `category`, `provides`, a `detect` rule, and a live `available`
boolean for *this* machine. `GET /api/recipes` → 17 entries spanning corpus/finetune/pretrain/performance
capabilities, each naming the backing skill and source (NeMo Data Designer, NeMo AutoModel, Megatron Bridge). None
of this reaches the frontend — `grep` confirms zero consumers.
**Competitor pattern:** NemoClaw's `.agents/skills/` catalogue and OpenClaw's plugin listing — "what capabilities
does this installation actually have, and which ones are live on this box." Claude Desktop's connector/plugin
settings screen is the same shape.
**Build:** a `/tools` page (or a Settings tab) with two grouped tables — Tools by category with a live/red-not-found
dot per row, and Recipes by capability with the source skill linked.
**Endpoints:** `GET /api/tools`, `GET /api/recipes`.
**Size:** 10–14h.

### 7. Memory has no page — notes, threads, and preferences are invisible
**Live data:** `GET /api/memory` returns real content right now (1 note, 5 threads, 0 preferences), and
`POST /api/memory/notes` lets a caller record a durable fact (rejected if it reads as a bare numeric claim — the
kernel/memory separation the CHARTER insists on). Zero UI.
**Competitor pattern:** Claude Desktop / ChatGPT's Memory settings screen — see what's remembered, add or forget a
fact.
**Build:** `/memory` page — a note list with a "remember this" text box (`POST /api/memory/notes`), a preferences
list, and thread links back into Chat (the 5 threads already have their own IDs surfaced in `/api/chat/threads`
which Chat itself lists, so this mainly needs a landing page that ties memory to those threads).
**Endpoints:** `GET /api/memory`, `POST /api/memory/notes`.
**Size:** 8–12h.

### 8. No way to enroll or manage a remote machine from the interface
**Live data:** `GET /api/hosts` returns two enrolled machines (`local`: RTX 5090, can train; `mac-mini`: Apple M4,
cannot train) rendered nicely on `/machines` — but that page is entirely read-only, and there is no POST endpoint
at all for hosts in the OpenAPI surface, meaning even the backend has no path for this yet.
**Competitor pattern:** NemoClaw's `nemoclaw deploy <instance>` / Brev remote-GPU flow and OpenShell's
`openshell forward` port management (`nemoclaw-user-deploy-remote`, `nemoclaw-user-manage-sandboxes` skills) —
provision or attach a remote machine, see its health, without hand-editing a config file.
**Build:** lower priority than items above since it requires new backend surface, not just a missing page — but
worth scoping: a "Connect a machine" flow (SSH target, key path) that writes to whatever `hosts.yaml`/config the
backend already reads for `local`/`mac-mini`, plus a health/status refresh action per row.
**Endpoints:** needs a new `POST /api/hosts` (does not exist today) + `GET /api/hosts` (exists).
**Size:** 20–28h (roughly half of it is backend work this backlog otherwise assumes exists and doesn't).

### 9. Diffs — the "review the change" step everyone expects — don't exist anywhere in the UI
**What a user cannot do today:** see the actual code/config diff a dispatched agent produced. The Swarm page's
"Recent dispatches" prints file paths as truncated strings (`proposals/code-harness/agents/README.md,
proposals/code-harness/agents/checks.py, ...`); Objectives' subagent runs table shows step/agent/accepted/wall
but no diff; there is no diff viewer component anywhere in `app/frontend/src/components`.
**Competitor pattern:** this is Orca's core promise, named directly in its own module docstring — "diff review"
alongside worktree lifecycle and terminal sessions — and it is the single most-expected feature of any coding-agent
tool (Claude Desktop, Codex app, GitHub PR review all lead with it).
**Build:** a diff viewer component (unified or side-by-side, syntax-highlighted) that opens from a dispatch row on
Swarm/Objectives, reading the files already listed in the dispatch record. Requires a new endpoint to fetch a
diff/file content for a given run — check whether `GET /api/runs/{run_id}` already carries enough (it returns
`RunDetail`; worth checking if diff content is already in there before building a new endpoint).
**Size:** 20–30h depending on whether run detail already carries diff content or a new read path is needed.

---

## Tier 3 — interaction-quality parity with Claude Desktop / Codex app

### 10. Chat does not stream
**Confirmed:** `POST /api/chat` (`src/pravrudhi/api/chat.py:41`) is a single blocking async handler returning one
JSON payload; there is no `EventSource`/streaming fetch anywhere in `app/frontend/src/app/chat/page.tsx`. Every
reply appears all at once after a full round trip. Contrast with `/api/runs/{run_id}/events`, which *is* proper
SSE (`app/frontend/src/lib/api.ts:440`, `new EventSource(...)`) — the frontend already knows how to consume a
stream, it's just not wired for chat.
**Competitor pattern:** token-by-token streaming is baseline in Claude Desktop and the Codex app; its absence reads
as "slow" and "not really live" even when latency is fine.
**Build:** SSE or chunked-response streaming on `/api/chat`, consumed the same way `subscribeRunEvents` already
consumes run events.
**Size:** 12–18h (backend streaming + frontend incremental render + tool-citation handling mid-stream, since the
page's whole premise — "only tool-backed numbers make it into the reply" — has to keep holding for partial output).

### 11. No command palette / keyboard-first navigation
**Observed:** every page requires a mouse click on the left sidebar; there is no `Cmd+K`-style palette, no
keyboard shortcuts for New chat / State an objective / Dispatch, despite this being an 11-page app with several
create/action flows.
**Competitor pattern:** Claude Desktop and the Codex app both ship a command palette as a first-class citizen;
absence is immediately noticeable to anyone coming from either.
**Size:** 10–14h for a basic palette (page navigation + the 3–4 primary actions).

### 12. No notifications for long-running work
**Observed:** a dispatched subagent run, a training run, or an inbox item arriving has no toast, badge count, or
browser notification anywhere — a user has to keep a tab open and manually refresh/poll (Runs page does poll every
5s per `runs/page.tsx:276`, but only while that tab is open and focused on that page).
**Competitor pattern:** Claude Desktop and Codex both notify on task completion; NemoClaw's messaging-channel
integration (Telegram/Discord/Slack alerts on sandbox events, `nemoclaw-user-manage-sandboxes`) is the more ambitious
version of the same need — "tell me when the night finishes" without babysitting a tab.
**Size:** 8–12h for in-app toast/badge notifications; a messaging-channel integration (email/Slack/Telegram on
night completion or inbox arrival) is a larger, separate item (20h+) but matches user intent ("start a night,
walk away, get told when it's done") closer than any in-tab affordance can.

### 13. No offline / engine-down state beyond a generic banner
**Observed:** `ConnectionBanner.tsx` exists and is shown on every page, but there's no differentiated recovery
guidance in the web UI the way the desktop shell has (`app/desktop/main.js`'s `doctor()` flow: named checks, a
copyable recovery command per failure, a 30s health-check deadline with retry). The web app, run standalone
(without the Electron shell), has no equivalent — if the engine dies mid-session, the browser tab has nothing like
the desktop app's structured recovery screen.
**Competitor pattern:** the desktop shell (already built, see `app/desktop/README.md`) already solved this well;
the web frontend hasn't adopted the same pattern for people who run `pravrudhi app` and use a browser tab directly
rather than the Electron shell.
**Build:** surface `GET /api/doctor` (already returns 6 named checks with `ok`/detail, confirmed live) in the web
UI's connection-lost state, not just in the desktop app.
**Size:** 6–10h, mostly reusing what `main.js`'s `parseDoctor`/`recovery` already model on the desktop side.

---

## Tier 4 — smaller inconsistencies worth fixing alongside the above

### 14. Three different UI concepts are all called "run" or "dispatch," inconsistently
`/runs` tracks only `POST /api/runs` training/harness runs from the Improve page (currently empty: "No runs yet —
start one from Improve"). Objectives' Subagent routing shows its own `runs` array from
`GET /api/objectives/{oid}/subagents`. Swarm's "Recent dispatches" shows a third, heartbeat-driven history. None of
these three lists cross-links to the others despite overlapping meaning to a user (e.g., a heartbeat-dispatched
subagent run for `code-harness` never shows up in `/runs`, which only understands Improve-triggered training runs).
Worth a short audit + either unifying the concept or clearly labeling the three as distinct ("training runs" vs
"agent dispatches" vs "heartbeat activity") consistently across pages.
**Size:** 6–10h for labeling/cross-linking; larger if unified into one model.

### 15. No badge legend anywhere
Grey/amber/green/red candidate badges appear on the Improve page pill counts and (implicitly) would appear on the
candidate visualization in item 4, with no explanation anywhere in the UI of what they mean. A one-line legend or
tooltip is trivial and currently entirely absent.
**Size:** 1–2h, bundle with item 4.

### 16. Objectives cannot be edited, archived, or deleted from the API or UI
`GET/POST /api/objectives` and `GET /api/objectives/{oid}` are the only objective routes in the OpenAPI surface —
no PUT/PATCH/DELETE. A mistyped benchmark metric or an abandoned objective has no path to correction short of
editing files on disk. Flagging as a real gap even though it needs backend work first.
**Size:** 12–16h including the missing backend routes.

---

## Top ten, in priority order

1. **Fix `/heartbeat`'s production crash (item 1).** It is the app being broken right now, for real data, in
   front of anyone who clicks the third item in the sidebar. Nothing else matters if the product looks broken on
   first exploration.
2. **Build the inbox sign-off page (item 2).** Three real candidates are waiting on a human decision right now and
   the only way to make that decision is `curl`; this is the one step in the whole design the CHARTER names as
   irreducibly human, and it has no UI at all.
3. **Make Swarm launch and watch work, not just report (item 3).** The dispatch machinery already works on
   Objectives; Swarm is the page whose entire premise is "what's running right now," and right now it can only
   ever say no.
4. **Build the candidate population view (item 4).** This is the single screen most likely to make someone choose
   Pravrudhi over Orca — 188 real scored candidates exist and are currently four numbers; nothing else in this
   backlog produces a moment of "watch your model improve" this directly.
5. **Turn objective-creation into a guided intent→plan flow (item 5).** The form works but silently requires
   benchmark-syntax literacy; the backend already compiles a plan and Loom source, just never before commit.
6. **Ship the diff viewer (item 9).** Every competitor pattern surveyed leads with this; Pravrudhi currently has
   none, anywhere, despite running a swarm whose entire output is diffs.
7. **Build the tools & recipes catalogue page (item 6).** Fully computed, zero frontage; cheap relative to its
   value for anyone deciding what the engine can actually do on their machine.
8. **Build the memory page (item 7).** Small, self-contained, and the CHARTER treats memory/ledger separation as
   load-bearing enough that it deserves to be visible, not just API-only.
9. **Stream chat responses (item 10).** The SSE plumbing already exists for run events; not extending it to chat
   is the most visible remaining "this doesn't feel like a 2026 product" gap.
10. **Add in-app notifications for long-running work (item 12).** A night or a dispatch is exactly the kind of
    task a user starts and walks away from; right now walking away means missing the result.
