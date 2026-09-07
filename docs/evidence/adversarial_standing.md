# Standing adversarial review — Pravrudhi

Run 2026-09-06, ~19:26–20:35 BST. Everything below was executed, not read. Screenshots and raw
command output live under the scratchpad this session used; paths to the exact commands are given
inline so any of this can be re-run. Method: real browser (Playwright, viewport 1440x1400), real
SSH to the Mac mini, real `npm install && node --test` on the desktop app, real API calls against
the running engine at `http://127.0.0.1:8200`, real writes/reads against the ledger and the
objectives store (one test objective was created through the UI to prove the write path works,
then deleted — `.pravrudhi/objectives/audit-test-obj.yaml`).

Engine at audit time: v0.2.3, `pravrudhi-app.service` active since 2026-09-06 16:35:56 BST.

---

## Goal 1 — auto-update with safeguards; RTX + Mac mini as end-user test installs; active updates only on dev; release only tests e2e

**Verdict: PARTIAL.**

What works, verified live:

- Three separate systemd timers exist and are firing: `pravrudhi-app.service` (dev, always-on),
  `pravrudhi-update.timer` (dev channel, every 30 min), `pravrudhi-release-update.timer` (release
  channel, every 30 min). `systemctl --user list-timers | grep pravrudhi` confirms all three are
  `waiting`/scheduled, not dead.
- The release install at `~/pravrudhi-release` actually updates. `journalctl --user -u
  pravrudhi-release-update.service --since "1 day ago"` shows a real version jump: `{"applied":
  true, "reason": "switched to 0.2.1", ...}` at 15:53, then a rate-limit failure at 16:41
  (`"could not reach the GitHub releases API: HTTP Error 403: rate limit exceeded"` — handled
  gracefully, `applied:false`, no crash), then successful applies at 0.2.3 from 17:12 onward.
- The safeguard the deploy script itself documents is real and was needed: an `--if-due` CLI flag
  was added in 0.2.3, but both end-user installs were still on 0.2.1 when the scheduler first ran
  it, so every run failed with "No such option: --if-due" until the wrapper script added a
  fallback to the old invocation. This is visible directly: on the Mac mini,
  `~/pravrudhi-release/logs/update.err` still contains that exact stale error, but its mtime
  (16:18:39) is well before `update.log`'s most recent successful entries (up to 20:12), so this
  was a real self-inflicted outage that the safeguard designed for was needed for and then fixed —
  not theoretical.
- Mac mini confirmed via `ssh sharath@192.168.0.201`: `launchctl list | grep pravrudhi` shows
  `com.pravrudhi.release-update` present with last exit code 0. `update.log` shows the same
  "switched to 0.2.3" pattern as the RTX box.

What is broken or unverified:

- **The release channel does not appear to be idempotent.** Both the RTX box and the Mac mini log
  `{"applied": true, "reason": "switched to 0.2.3", ...}` on *every single 30-minute run* since
  17:12 — eight consecutive runs on the RTX box alone, all claiming a "switch" to the same version
  it was already on. A correctly functioning update check should report "already up to date" (or
  similar) on 7 of those 8 runs. Either the update mechanism is silently re-deploying the release
  every cycle (wasteful, and a real risk under "run 24x7" if a redeploy ever races an in-flight
  request), or the "reason" string is misleading and the real behavior is a no-op mislabeled as a
  switch — both are worth fixing, and I could not tell which from the log alone.
  Smallest fix: emit a distinct `"reason": "already on 0.2.3"` (or equivalent) on a repeat check,
  and confirm in the deploy script's own log that a no-op path exists and is hit.
- **The developer channel is the one actually running as "the app" in this session, and its own
  "Updates" panel (`/settings`) is set to `channel: dev`, `Auto-apply: on`.** That is the opposite
  of what was asked — the instruction was that active/auto-apply update behavior belongs on the
  *developer's own dev install*, and the RTX/Mac installs are supposed to be end-user test
  targets for the *release* channel, which they correctly are. So this part is actually
  consistent with the ask; flagging only because the Settings page presents "dev" and "release" as
  equally-weighted radio options with no copy explaining which one this operator's own rule
  applies to — a new user reading this page has no way to know that dev auto-apply belongs only to
  the developer's own machine.
- I did not find a UI surface anywhere (`/settings`, `/machines`) that shows *both* end-user
  install's current version/last-update-time side by side — the only place that information lives
  is in per-machine `journalctl`/`~/pravrudhi-release/logs`. Goal 1 asks to "make it the end user
  testing," which implies visibility, and there is none in the product itself.

---

## Goal 2 — an app richer than Orca; no visible openclaw/nemoclaw adaptation

**Verdict: PARTIAL on richness, NOT MET on openclaw/nemoclaw.**

- `grep -rIl "nemoclaw\|openclaw" --include="*.py" --include="*.ts" --include="*.tsx" src/ app/
  pravrudhi_kernel/` returns **zero files**. Nothing in the actual product code references either
  system. The only place "nemoclaw" appears anywhere in this environment is in the *Claude Code
  skill catalog available to the operator's agent* (`nemoclaw-user-*` skills) — i.e., a tool
  available to whoever drives this repo, never touched by the repo itself. The operator asked
  twice, across two sessions apparently, and it is still absent. This is the single cleanest "not
  even started" item in the whole audit.
- Orca integration, by contrast, is real: `src/pravrudhi/agents/orca_agent.py` (185 lines) shells
  out to the actual `orca-ide` CLI, parses its JSON envelope, and drives three agent types
  (two hosted assistants, one local-GPU model via OpenCode) through it — not a stub. The `/swarm`
  and `/settings` pages both show `orca:claude`, `orca:codex`, `orca:local` as live, `available`
  fleet members, matching the code.
- Richness vs. Orca specifically: I have no live Orca instance to diff against, so I can't compare
  directly, but functionally the product now has real objective-authoring, a live progress
  dashboard sourced from the ledger, a swarm/routing view, a heartbeat log, and a chat surface
  that refuses to state a number no tool call backed — that is a wider feature surface than a
  typical single-purpose IDE agent runner. But three of those surfaces have real defects (see
  Goals 4, 6, 7 below), so "richer in principle" does not yet mean "richer in what actually
  renders and works."

Smallest fix for the explicit miss: pick one concrete openclaw or nemoclaw capability (the
skill catalog already lists `nemoclaw-user-deploy-remote`, `nemoclaw-user-configure-inference`,
`nemoclaw-user-manage-sandboxes`) and wire at least one into `src/pravrudhi/agents/` the same way
`orca_agent.py` wires Orca, or explicitly write in the objectives/swarm docs why it was rejected.
Silence is what's actually wrong here, not the absence per se.

---

## Goal 3 — GitHub Pages live demo that continuously updates (app + paper)

**Verdict: MET, with one broken link discovered and self-corrected during this audit.**

- `curl -s -o /dev/null -w "%{http_code}" https://sharathsphd.github.io/pravrudhi/` → `200`.
  `.../app/` → `200`. Screenshotted both in a real browser
  (`npx playwright screenshot --viewport-size=1440,1400 --wait-for-timeout=5000`).
- The root page is not a static stub: it renders live objective numbers pulled from the ledger
  (`humaneval+ pass@1 0.598 → 0.646`, `gsm8k 0.409 → 0.490`, etc., matching the local `/progress`
  page and the ledger rows referenced there) and a "Recent commits" list of 12+ real, dated commit
  messages, auto-generated from the actual git history — this is a genuine continuously-updating
  changelog, not hand-written copy.
- `/app/` on Pages serves a real "Recorded demo" build of the Improve page, explicitly labeled
  "Recorded demo — real runs from an RTX 5090. To improve your own model, install the engine and
  open it locally," with a Night-4 replay (proposed/measured/rejected candidate log matching the
  ledger) and Pause/Replay controls. Honest framing: it does not pretend to be a live backend on a
  static host.
- The one thing that did NOT work on first check: `https://sharathsphd.github.io/pravrudhi/paper.pdf`
  → `404`. The actual link on the page is `./paper/main.pdf`, which resolves (`200`). This was my
  own bad guess at the URL, not a site defect — recorded here only so the same guess isn't repeated.

No further action needed for this goal; it is doing what was asked.

---

## Goal 4 — Pages page as a rich live monitoring dashboard + full demo, updating every version

**Verdict: PARTIAL.** The dashboard half is real; the "full demo of all features" half is thin and
one of the core live pages is currently broken (see Goal 7's heartbeat finding, which belongs here
too).

- `/progress` (local, and mirrored to Pages) renders four objective cards with baseline→current
  numbers and confidence-interval deltas, plus four real line/error-bar charts
  (`TRACK H humaneval`, `TRACK H mbpp`, `TRACK M gsm8k`, `TRACK NYAYA mmlu_jurisprudence`) drawn
  from ledger rows, labeled "recorded snapshot · engine v0.2.3." This is genuinely a metrics
  dashboard, not a mockup — verified by screenshot and by cross-referencing the two ledger rows
  the objectives page cites (`research/ledger.jsonl` rows 1844/1845, 1846/1847, 717/718 — I did not
  re-verify every row number by line-count, but the file is 2450 real JSONL lines of kernel-emitted
  observe/prune/audit records, not a synthetic stand-in).
- What is missing from "full-fledged user demo of the features": the Pages `/app/` build only
  demos **Improve**. There is no recorded/replayed view of Objectives, Swarm, Heartbeat, Chat,
  Runs, Models, or Machines on the public site — a visitor sees one page's worth of the product,
  not the whole feature set the operator listed as wanted (intent menu, tools/skills routing,
  multi-agent orchestration).
- The local `/heartbeat` page — the page whose entire purpose is "show the engine working 24x7" —
  is currently **crashing** for any real viewer (full detail under Goal 7). Since Pages is a
  recorded snapshot of local pages, if this page is ever added to the recorded set, it will ship
  broken to the public dashboard too unless fixed first.

Smallest fix: extend whatever script produces the Pages `/app/` recorded demo to also snapshot
Objectives, Swarm and Runs (three pages already confirmed functional in this audit), and fix the
heartbeat crash (Goal 7) before it is added to that set.

---

## Goal 5 — no ADR/gate reporting surfaced to the user; only real working stuff

**Verdict: MET on the pages actually asked about.**

None of the 11 navigable pages (`/`, `/objectives`, `/progress`, `/swarm`, `/heartbeat`, `/chat`,
`/runs`, `/models`, `/machines`, `/settings`, `/install`) mention ADRs, gate JSON, or card names
(L0–L5) anywhere in their rendered content — confirmed by reading every screenshot taken this
session. The Objectives page explicitly foregrounds the operator-facing distinction instead
("Every number here comes from a benchmark scored outside the engine" / "not this objective's
evidence" language about the internal sealed pool vs. the external scorer) — that is the right
audience-facing framing, not internal process artifacts. Gate/ADR machinery still exists under
`gates/`, `contracts/`, `docs/decisions/` for the maintainer's own use, correctly gitignored and
out of the shipped product surface.

---

## Goal 6 — robust 24x7 operation independent of paid-model session limits; coordination, memory, skills, hooks

**Verdict: NOT MET as currently evidenced.** The one live mechanism built for this — the hourly
heartbeat that finds a neglected objective and dispatches it — is failing more often than it
succeeds, right now, in production.

- `curl -s "http://127.0.0.1:8200/api/heartbeat?n=100"` returns 5 recorded beats. Of those: **3 of
  5 (60%) are rejected**, all with the identical, uninformative pair of reasons
  `["agent exited non-zero: no detail", "no change produced"]`, all against the same objective/step
  (`code-harness · candidate-evaluation`), all routed to `claude-code`. Only 2 of 5 beats — both
  from an earlier `code-harness · agents`/`baseline-evaluation` step — actually landed a change.
- This is not a fleet-availability problem: `/swarm` and `/settings` both show `claude-code` as
  `available`/`ready` at the moment of dispatch, and the same agent succeeded twice earlier the
  same day. The failure is inside whatever the dispatched `claude-code` invocation is doing for
  `candidate-evaluation` specifically, and the system has no way to tell the operator why — "no
  detail" is the literal string surfaced on the Swarm page's "Recent dispatches" panel and via the
  API. A system meant to run unattended 24x7 cannot self-diagnose its own most common failure mode.
- "Coordination is key": the swarm routing table on `/swarm` does show a real, evidence-based
  routing decision per tier (e.g., "sonnet has the best measured success rate at this tier (18/23)
  and nothing cheaper matches it") — that part is genuine bandit-style routing, not decorative.
  But coordination across *paid model session limits specifically* (the actual ask — falling back
  when Claude/Codex hit a rate or session limit) has no visible fallback path in the routing table:
  all four tiers (mechanical, standard, design, critical) route to either `hosted`/`qwen3-coder`
  or `claude-code`/`sonnet`, with no declared fallback agent shown when the primary is unavailable.
  I did not find, and did not have budget to fully trace, any explicit retry-on-different-agent
  logic — the registry files (`src/pravrudhi/agents/registry.py`, `hosts/fleet.py`) would be the
  place to check next.
- Memory/skills/hooks: the objectives page does list a real recipe catalog (23 named recipes across
  9 categories — corpus, finetune, pretrain, performance, rl, evaluate, retrieval, safety, agents),
  each tagged `available`, and each objective card links "Subagent routing" and "The plan as Loom"
  detail panels. That is a genuine skills/plan surface. What's absent is any visible *memory*
  surface — nothing in the 11 pages shows what the engine remembers between nights beyond the
  ledger itself (which is evidence storage, not working memory), and nothing shows a hooks
  configuration surface at all.

Smallest fix: make the heartbeat dispatcher capture and surface the actual subprocess stderr (the
desktop app's own `command()` function in `app/desktop/main.js` already does exactly this — captures
and truncates stderr on non-zero exit — the engine-side dispatcher should do the same instead of
recording `"no detail"`).

---

## Goal 7 — intent/goal/objective menu; tools/skills/plugins/multi-agent orchestration; no superficiality

**Verdict: PARTIAL, with one confirmed, currently-live page crash.**

**The intent/objective menu is real and functional; I proved this by using it, not reading it.**
Clicked "State an objective" on the live `/objectives` page, filled Intent/Short name/Domain/
Track/Benchmark metric, clicked "Record it." Network trace showed `POST
http://127.0.0.1:8200/api/objectives → 200`, followed by a re-fetch `GET /api/objectives → 200`.
Confirmed server-side: the object was written to a real file,
`.pravrudhi/objectives/audit-test-obj.yaml`, and returned in the API's objective list. This is not
a mock form — it is a genuine write path from UI to persisted engine state. (Deleted the test
artifact after confirming; `.pravrudhi/objectives/` now shows only `code-harness`, `math-reasoning`,
`prabhasa-nyaya` again.)

**The Swarm/routing page is real, not display-only** — contrary to what I expected going in. The
routing table gives a specific, falsifiable reason per tier ("sonnet costs 2.9 against astra's 10
and their intervals overlap (10/14 against 5/5), so the extra spend is not yet justified"), which
only makes sense if it is computed from real trial counts, not templated copy.

**But `/heartbeat` — the page that is supposed to be the clearest evidence of "real, not
superficial" 24x7 orchestration — is currently broken for every visitor**, and this is the exact
failure mode the audit brief warned about: HTTP 200, but a client-side crash.

```
curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8200/heartbeat   → 200
npx playwright screenshot ... http://127.0.0.1:8200/heartbeat            → renders "This page couldn't load / Reload to try again, or go back."
```

Root cause, found via Playwright's console capture (`browser_console_messages`, not guesswork):

```
TypeError: Cannot read properties of undefined (reading 'toFixed')
    at .../chunks/3e-490ab65q31.js:1:2690  (minified HeartbeatTimeline render)
```

`git log -p` on `app/frontend/src/components/heartbeat/HeartbeatTimeline.tsx` shows the bug was
introduced in the same commit that added the whole heartbeat feature (`928b934`, 16:16:58 BST):
`{beat.result.wall_s.toFixed(1)}s` with no null guard. The engine process serving this page was
started at 16:35:56 — i.e., it has been serving this crash for the entire ~4 hours it has been up.
A fix already exists **uncommitted, unbuilt, undeployed** in the working tree right now
(`git status --short` shows `M app/frontend/src/components/heartbeat/HeartbeatTimeline.tsx`,
already patched to `(beat.result.wall_s ?? 0).toFixed(1)`), alongside other uncommitted,
apparently-unfinished work (`A app/frontend/e2e/deployed.spec.ts`, `M
app/frontend/src/lib/heartbeat.ts`, `M app/frontend/src/lib/demo.ts`, `?? app/frontend/audit.mjs`)
— evidence of an earlier attempt at exactly this kind of check that stalled before finishing,
consistent with what I was told about a prior review dying mid-task.

A second, not-yet-triggered instance of the same anti-pattern exists in
`src/components/swarm/DispatchesTable.tsx:74` (`d.wall_s.toFixed(1)}s` — unguarded) and
`src/components/swarm/RoutingTable.tsx:11` (`record.mean_wall_s.toFixed(1)` — unguarded). The
swarm page happens not to be crashing right now only because every `wall_s`/`mean_wall_s` value
currently present is defined; the same class of bug is one null field away from taking Swarm down
too.

Smallest fix: rebuild and redeploy the frontend (the fix is already written), then audit every
`.toFixed(` call across the six files listed by `grep -rln "toFixed" src/` for the same missing
null-guard, and add an automated check (a Playwright smoke test that visits every nav-listed route
and asserts no console error / no "This page couldn't load" text) to CI so this class of defect
cannot ship silently again — which is precisely what the operator's brief predicted would happen
if only HTTP status were checked.

---

## Goal 8 — demo shows real clicks; desktop app updates and produces real results

**Verdict: PARTIAL.**

Desktop app (`app/desktop`, Electron, 316 lines across `main.js`/`lib/`/`renderer/`):

```
cd app/desktop && npm install --no-audit --no-fund --silent   → exit 0
node --test test/                                             → 12/12 pass, 0 fail
```

This is not a stub shell. Read `main.js` in full: on start it discovers the installed engine
binary, launches it as a real subprocess (`spawn(status.binary, ['app','--no-browser','--port',...])`),
polls `/api/health`, and once healthy **loads the actual running engine's web UI directly into the
Electron window** (`w.loadURL(origin)`) — i.e., the desktop app is not a separate reimplementation
of the UI, it is a native chrome around the same product surface audited above, so every defect
and every working feature found in this report (including the heartbeat crash) will appear
identically inside the desktop app. It has a real update path too: an `updates()` function that
runs `pravrudhi update --json`, offers to apply via `pravrudhi update --apply --channel release`,
and reports the result — wired to a "Check for updates" menu item, not decorative. Window bounds
persistence, doctor-check parsing with per-failure recovery commands (copy-to-clipboard, "never
executed by the shell" — a real security boundary I confirmed by reading `app.js`'s
`copyButton`), and a CSP-locked `index.html` (`script-src 'self'`, `connect-src 'none'`) are all
real, non-trivial engineering, not window dressing. I did not launch the actual GUI (per
instructions) so I cannot confirm the visual result, only the code path and the 12 passing unit
tests covering discovery, port selection, health polling, doctor parsing, navigation policy, and
state persistence.

What's unverified/absent: there is no recorded video or GIF of the desktop app actually opening,
updating, and showing a result — which is what "the demo should show real clicks... and how the
desktop app updates" asks for. Nothing in `docs/`, the Pages site, or the repo root packages such
a recording. The GH Pages `/app/` "Recorded demo" covers the web Improve flow only, not the
desktop shell.

Smallest fix: record a short screen capture of the desktop app cold-starting against a real
engine, showing the update-check dialog firing a real `pravrudhi update --apply`, and add it next
to the existing "Live app (recorded demo)" link on the Pages root.

---

## Ranked list of misses (most severe first)

1. **`/heartbeat` is crashing right now, in production, for the page whose entire job is to prove
   the system runs unattended.** Root cause identified precisely (`HeartbeatTimeline.tsx`,
   unguarded `.toFixed()` on `beat.result.wall_s`), a fix already exists uncommitted in the working
   tree, and it has simply never been rebuilt/redeployed since the bug was introduced ~4 hours
   before this audit. This is exactly the "HTTP 200 but broken" failure the audit brief predicted.
   (Goals 4, 7.)

2. **The autonomous heartbeat dispatcher is failing 3 of its last 5 attempts** with a bare
   `"agent exited non-zero: no detail"` — the system cannot explain its own most common failure,
   which directly undercuts the "robust 24x7, no hand-waving" goal. The desktop app's own code
   already shows the fix pattern (capture stderr on non-zero exit); the engine-side dispatcher
   doesn't do it. (Goal 6.)

3. **Zero adoption of openclaw/nemoclaw anywhere in the product**, despite being asked for
   explicitly and by name, twice. Orca got a real 185-line integration; openclaw/nemoclaw got
   nothing — not a stub, not a rejected-ADR note, nothing. (Goal 2.)

4. **The release-channel updater logs "switched to 0.2.3" on every single 30-minute check for 8+
   consecutive runs on the same version**, on both the RTX box and the Mac mini — either it is
   silently redeploying every cycle (a real risk for a system meant to run 24x7) or its status
   reporting is simply wrong; either way it hasn't been checked. (Goal 1.)

5. **No visibility surface for the two end-user test installs' update state inside the product
   itself** — the only way to see whether RTX/Mac mini are current is SSH + log-reading, which is
   what this audit had to do. The stated purpose of those two installs ("make it the end user
   testing") implies the product itself should show this. (Goal 1.)

6. **The public GitHub Pages demo covers one page (Improve) out of eleven**, and no recording of
   the desktop app exists anywhere, so "full-fledged user demo of the features" and "the demo
   should show real clicks... how the desktop app updates" are both only partially satisfied even
   though the underlying features being asked to demo mostly work. (Goals 4, 8.)

7. **Latent, not-yet-triggered copies of the same unguarded-`.toFixed()` bug** exist in
   `DispatchesTable.tsx` and `RoutingTable.tsx` on the Swarm page — currently silent only because
   no null value has hit them yet, and with no test in place to catch it when one does. (Goal 7.)

What is genuinely solid and should not be re-litigated without new evidence: the objectives
create-and-persist flow (proved by writing and reading back real state), the progress dashboard's
ledger-sourced charts, the GitHub Pages site's auto-updating commit history and honest "recorded
demo" framing, the absence of any ADR/gate language from user-facing pages, and the desktop app's
subprocess-and-loadURL architecture with its 12 passing unit tests.
