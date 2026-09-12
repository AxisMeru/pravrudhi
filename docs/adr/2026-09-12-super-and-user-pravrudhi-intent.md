# ADR — Super Pravrudhi and User Pravrudhi: the operator's intent, answered under delegation

- Status: accepted (2026-09-12, `agent-for-operator` under the delegation of 2026-09-10, ADR-0040)
- Request: `r-799f8dfb`, criterion 0
- Related: `2026-09-11-prabhasa-nyaya-product-delivery.md` (the edition split derived from `api/roles.py`),
  `2026-09-11-loop-build-mode.md`

## Why this document exists

On 2026-09-07 the operator asked, verbatim:

> continue..askuserquestion to gather my actual intent on super pravrudhi and user pravrudhi...also give me
> control in super pravrudhi to directly interact with you using the model network that you have built as this
> claude code session may hit session limit in a day or 2...so prep it and test it and keep it ready for me

The loop drafted a criterion requiring "the operator's answers to the intent questions" as a durable artifact
quoting the questions and the selected answers. Three dispatches of that criterion ended the same way: the
headless agent formulated the questions, found it had no way to ask a person, and correctly refused to invent
the answers (2026-09-12, dispatches `r-799f8dfb-0`; the third one's own words are recorded in the heartbeat
ledger under `no change produced:`). The questions below are that agent's, copied verbatim from its final
message.

The answers are **not** selections the operator typed. They are decisions taken by the team-lead session as
`agent-for-operator` under the standing delegation — operator, 2026-09-10: *"there is no point in waiting for
me for gate approvals...stop that and proceed autonomously..change adr/rules if needed..continue"*; operator,
2026-09-12 07:30: *"continue autonomously..don't stop for me...all loops and your scaffolding work should
proceed per the backlog and the overarching goals/objectives"*. Where the operator's own words already decide a
point they are quoted; the rest is a delegated decision, marked as such, and the operator can overturn any of
it through the liaison channel with a one-line reply. A reader must never mistake this for a transcript of the
operator choosing options.

## The questions (verbatim, from the dispatched agent) and the answers

**On Super Pravrudhi (the Studio/builder edition — you, the operator, facing the RSI engine):**

> 1. What must Super Pravrudhi expose that User Pravrudhi never should? Candidates: (a) direct read/write on
> `pravrudhi_kernel`/`research`/`gates`; (b) power to trigger builds/deploys of the RSI engine itself; (c) full
> roster control — add/remove seats, override cooldown/routing; (d) something else you have in mind.

**Answer (delegated):** (a), (b), (c) and (d), with one carve-out inside (a).

- (a) Studio reads and writes `research/` and `gates/` (the ledger, requests, verdicts, gate records, inbox
  signing under `configs/delegation.yaml`). `pravrudhi_kernel/` (T0) is **not** writable from any surface:
  CHARTER §6 — a kernel change is an accepted ADR before the commit. Studio may *propose* one; nothing in
  Studio applies one.
- (b) Yes: `pravrudhi selfbuild propose/close/cycle`, releases and the hosted engines' redeploy are Studio
  acts. User Pravrudhi updates itself from published releases only (`app/desktop/lib/updates.js`).
- (c) Yes: seats (`configs/seats.yaml`), routing (`configs/routing.yaml`), cooldowns, and the agent roster
  are Studio's. User Pravrudhi sees none of it.
- (d) The operator-facing loop itself: heartbeat state, dispatch, judging, the requests store, and the
  swarm/machines pages. The whole `ADMIN_ONLY` set in `src/pravrudhi/api/roles.py` is the executable form of
  this answer; `tests/test_edition_pages.py` asserts the page split is derived from it, not restated.

> 2. Your line "give me control in super pravrudhi to directly interact with you using the model network that
> you have built" — which of these is it: (a) a chat/console surface inside Super Pravrudhi that lets you talk
> to whichever agent/model is currently live in the roster (Claude, Codex, OpenRouter-routed models), so you're
> not pinned to this one Claude Code session; (b) automatic failover — if this session hits its limit, another
> roster seat picks the task back up mid-stream with context; (c) both?

**Answer (delegated):** (c), built in the order (a) then (b).

- (a) exists as the `/chat` and `/terminal` pages behind the Studio sign-in and the hosted Studio door
  (`deploy/gateway/`), routed through `configs/routing.yaml` and the OpenAI-compatible client. It is the
  operator's control surface when no Claude Code session is attached. Model choice is the router's; the page
  shows which seat answered.
- (b) is the seats mechanism (`pravrudhi.agents.account`, `configs/seats.yaml`: ordered seats, the next one
  taken when the first is unavailable) applied to the **loop's** work — dispatches carry their task id and
  worktree, so a retry on another seat resumes the task, not the conversation. "Mid-stream with context" for a
  human conversation is not promised: a conversation's context lives in the session that held it, and the
  honest failover is the requests store plus `HANDOFF.md`, which every seat reads.

> 3. Should that control surface be able to edit `pravrudhi_kernel` directly (a full coding-agent seat), or
> should it stay chat/oversight-only, with real kernel edits still routed through the existing sandboxed worker
> pipeline (like the one that produced this very task)?

**Answer (delegated):** chat/oversight-only for the kernel. The surface may start a dispatch (a sandboxed
worker in its own worktree, judged before integration) and may sign an inbox pack under the delegation, but it
never edits `pravrudhi_kernel/` itself, and no dispatch may either: T0 changes are an accepted ADR first,
listed in ADR-0047. Engine code (`src/pravrudhi/`) is edited by dispatches, not by the chat surface directly.

**On User Pravrudhi (the end-user/desktop product):**

> 4. Per the desktop-app memory, User Pravrudhi is meant for end users to "build their own things," not to
> touch Pravrudhi's own self-improvement. Does that still hold as the dividing line, or has your thinking moved?
> Should the admin login stay reserved for internal testing (the prabhasa-nyaya proxy path), with real distinct
> user accounts and BYOK model/API keys as the only production path?

**Answer:** it holds, and here the operator's own words decide it. Operator, 2026-09-07:

> remember the desktop app will not have access to any api/routing for any model provider..they have to bring
> theirs, configure it etc..you have to enable..but for admin it uses the one as the core pravrudhi

So: User Pravrudhi is bring-your-own-key only; the engine's core credentials and the routing table are never
reachable from a product install or a non-admin caller (`roles.role_of`). The admin account is the operator's
and is also the internal-testing identity (the prabhasa-nyaya proxy path runs as admin on the Studio
engine). Real users are distinct Supabase accounts, each with their own workspaces (`/api/workspaces`); a test
account (`gateway-probe@axismeru.com`) exercises the non-admin path nightly. Product users improve *their*
model, agent or app; the engine's self-improvement is Studio's alone.

**On scope of what I do right now (I'm sandboxed to `docs/*`, `tests/*` for this task):**

> 5. "Prep it and test it and keep it ready" — for this pass, is a design ADR plus contract/spec tests
> (defining and asserting the expected interface/behavior split, without touching `pravrudhi_kernel`) the right
> deliverable, with actual implementation left to a follow-up unrestricted task? Or did you want something
> narrower this round?

**Answer (delegated):** yes — this ADR is the durable artifact; the interface split it describes is already
asserted by `tests/test_roles.py`, `tests/test_edition_pages.py`, `tests/test_edition.py` and
`tests/test_api_identity_routes.py`, and the remaining criteria of `r-799f8dfb` (the desktop conversation
surface, its route and its exclusion from the product build) are build work for later dispatches, each with
its own test. Nothing in this pass touches `pravrudhi_kernel`.

## Consequences

- `r-799f8dfb` criterion 0 has its artifact. Its remaining criteria are build criteria against this ADR.
- Any headless dispatch that finds it needs a person's answer stops and says so, as this one did; the team
  lead answers under the delegation and records the answer here or in a sibling ADR. A criterion may not
  demand words the operator never typed.
- If the operator overturns any answer above, this file is amended with the operator's words and the date;
  the old answer stays visible as superseded.
