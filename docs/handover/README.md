# Handover: taking over Pravrudhi development

For a Claude team account picking up both editions of this project. Written to be read once, top to bottom,
before touching anything. It assumes you have the repository and nothing else.

Nothing here contains a secret. Credentials are named by **location and variable**, never by value; §4 says how
to obtain each one and who from.

---

## 1. What this is, in one paragraph

Pravrudhi is an installable recursive self-improvement engine. A **night** proposes candidate training recipes,
trains them on a local GPU, evaluates them against a sealed benchmark pool, and prunes or promotes each against
the standing incumbent — all of it recorded in an append-only ledger that every other surface replays rather
than caches. The **kernel** (`pravrudhi_kernel/`) computes all evidence and is not yours to edit. The **engine**
(`src/pravrudhi/`) proposes, orchestrates and serves. The **app** (`app/frontend/`) is how a person watches it.

Read `CHARTER.md` next — particularly §6, the hard rules. Then `docs/architecture.md`.

## 2. Two editions, one codebase

| | Studio | Product |
|---|---|---|
| Purpose | improves **Pravrudhi itself** | improves **the user's own work** |
| Workspace on the 5090 | `/home/ss/projects/pravrudhi` | `~/pravrudhi-release` |
| Engine | the development checkout | an installed release under `.pravrudhi/releases/<version>/` |
| Update channel | dev: `git pull --ff-only`, gated on `make smoke` | release: SHA256-verified wheel, versioned install, rollback |
| Telegram bot | the original bot | `@prabhasa_bot` |
| Surfaces | everything | self-improvement surfaces are 404 by design |

The edition is decided at build time for the desktop app (`resources/edition.json`) and by
`PRAVRUDHI_EDITION` for a running engine. `api/edition.py::engine_edition` infers PRODUCT from a release
install path, so **a development checkout is Studio by construction** — if you run dev code against the product
workspace you must set `PRAVRUDHI_EDITION=product` or it will introduce itself as Studio. That has already
happened once.

## 3. What runs unattended

Nine `systemctl --user` units on the 5090. `systemctl --user list-timers | grep pravrudhi` is the fastest check.

| Unit | Cadence | What it does |
|---|---|---|
| `pravrudhi-app` | always | serves the engine on :8200 |
| `pravrudhi-heartbeat` | hourly | studio: picks the largest eligible drive and does one thing about it |
| `pravrudhi-product-heartbeat` | hourly | the same, against the product workspace |
| `pravrudhi-publish` | 30 min | exports the snapshot and pushes, which is what updates the live site |
| `pravrudhi-update` | 15 min | dev channel: pull and re-install if `make smoke` passes |
| `pravrudhi-release-update` | 30 min | release channel for the product install |
| `pravrudhi-telegram` | 2 min | studio bot |
| `pravrudhi-product-telegram` | 2 min | product bot |
| `pravrudhi-watch` | 30 min | looks for a stall and messages Telegram only when it finds one |

**A green unit proves nothing.** Every one of these reported `success` through a five-hour outage in which every
dispatch was rejected. `Result=success` means the process exited 0, which an instant refusal also does. Read
what a beat *chose*, not whether it ran — see §7.

## 4. Credentials

All 0600, all outside the repository, none in git. You need them from the operator.

| File | Variables | For |
|---|---|---|
| `~/.config/llm/dashscope.env` | `DASHSCOPE_API_KEY` | Alibaba **free tier** |
| `~/.config/llm/dashscope-plan.env` | `DASHSCOPE_API_KEY` | Alibaba **Lite Plan** |
| `~/.config/pravrudhi/github.env` | `PRAVRUDHI_GITHUB_TOKEN` | release checks, CI queries |
| `~/.config/pravrudhi/telegram.env` | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | studio bot |
| `~/.config/pravrudhi/telegram-product.env` | `TELEGRAM_BOT_TOKEN` | product bot (`@prabhasa_bot`) |
| `~/.config/pravrudhi/chat.env` | `PRAVRUDHI_CHAT_ENDPOINT`, `PRAVRUDHI_CHAT_MODEL`, `PRAVRUDHI_CHAT_API_KEY` | the model the bots talk through |

**Both DashScope files use the same variable name and different endpoints.** The file decides which endpoint the
key belongs to, not the variable. A plan key sent to the free-tier endpoint returns `401 invalid_api_key`, which
reads like a bad key and is not. This has cost time twice; `agents/alibaba_agent.py` says so in its docstring.

## 5. House rules that will bite you

- **Commit as `SharathSPhD <qbz506@york.ac.uk>` with no attribution trailers.** Enforced by
  `.githooks/commit-msg`, which fails the commit rather than warning. Run `make init` to install the hook.
- **Never `git add` a gitignored local path** — `CLAUDE.md`, `HANDOFF.md`, `RESTART.md`, `docs/blueprint/`,
  `docs/superpowers/`, `docs/decisions/`, `contracts/`, `gates/`, `research/`, `.pravrudhi/`, `.worktrees/`.
  The ledger and the ADRs are deliberately local.
- **`pravrudhi_kernel/` is not yours to edit.** If a change seems to need it, write an ADR request under
  `docs/decisions/` and stop. See `docs/decisions/ADR-0034-*` for the shape.
- **Evidence comes only from the kernel.** State no number the ledger does not contain.
- **TDD.** A failing test that reproduces the real fault, then the fix. Every commit in this repository's recent
  history does this, and the commit message says what the fault was.
- `make smoke` (import guard, ruff, mypy strict, full suite) is the gate. It must be green before you push.

## 6. Getting to a working checkout

```bash
git clone git@github.com:AxisMeru/pravrudhi.git && cd pravrudhi
make init          # installs the commit hook — do this first
uv sync            # python 3.13
make smoke         # ~5 minutes, ~1835 tests; must be green before you change anything
```

Frontend: `cd app/frontend && npm ci && NEXT_PUBLIC_DEMO=1 npm run build`.

## 7. How to tell whether it is actually working

This is the part that matters, and it is not obvious.

```bash
pravrudhi watch --root .                       # the shape of a stall, in three checks
pravrudhi watch --root ~/pravrudhi-release
pravrudhi doctor                               # seven checks; `routing` is the one that catches an invisible CLI
pravrudhi routes                               # cost, record, and when a spent seat returns
systemd-run --user --wait --pipe .venv/bin/pravrudhi agents   # what a UNIT can see, not your shell
```

That last one is not paranoia. `systemd --user` gives a minimal PATH that includes `~/.local/bin` but **not**
`~/.nvm/versions/node/<version>/bin`, so `opencode` and `codex` — npm globals under nvm — are invisible to every
unit. The heartbeat rejected every dispatch hourly for five hours while `claude` sat ready and unused, and every
unit reported success throughout. There are wrapper scripts at `~/.local/bin/{opencode,codex}` that resolve the
newest nvm node at call time; if you rebuild a machine, recreate them.

To read what the loop is actually doing:

```bash
journalctl --user -u pravrudhi-heartbeat.service -o cat --since today | grep '^{' | tail -5
```

**If `chose` is identical across beats, it is stalled**, whatever the unit says. That signature has appeared
three times: a remedy proposed hourly for eight hours and never executed, an unwired `freshness` drive chosen
hourly after it, and one request criterion dispatched nine times in a day and refused every time.

## 8. State as of 2026-09-08

Working and demonstrable: both editions installed on the 5090 and the Mac mini; the Mac mini self-updated
0.3.1 → 0.4.0 unattended with rollback versions retained; night 18 ran twelve real GPU candidates end to end
(all pruned, nothing promoted, 0.892 of 3.0 GPU-h); the live site at
`https://axismeru.github.io/pravrudhi/app` updates itself every 30 minutes; both Telegram bots hold real
conversations through the engine's own `chat.converse`.

Known open, in the order I would take them:

1. **The bots answer but cannot act.** They read state; they cannot file a request or dispatch work from a
   message. That is what "bring me into the loop" actually requires.
2. **Bot tool coverage is partial** — nights, objectives, routing, appetite, recipes, memory. Not candidates,
   the agent trace, or the seats table, so those questions get an honest "no record".
3. **Lite Plan quota exhausted** until 2026-09-14 16:14 UTC. `chat.env` is pointed at the free tier meanwhile;
   switch it back after the reset (the file says how).
4. **ADR-0034** — a kernel guard against pooling deltas from different incumbents has never fired, because
   nothing writes the key it reads. Operator decision, not yours to patch.
5. **Parity with Orca**: ahead on five rows, behind on `github-integration` and `design-mode`.
   `embedded-terminal` is a deliberate refusal — a shell in a web page is a remote execution surface this
   engine's threat model excludes. Do not "close" it.

## 9. Routines

`docs/handover/routines.md` has ready-to-paste routine definitions for
[claude.ai/code/routines](https://claude.ai/code/routines) — cloud-run, so they survive any one session ending.
Create them under the team account.
