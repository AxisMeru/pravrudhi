# Handover checklist

Everything that must happen for the team account to own this project, split by who can do it. Work top to
bottom; the order matters in a few places and those are called out.

Nothing in this file is a secret. Where a credential is needed, it is named by location and the operator hands
over the value out of band.

---

## A. Operator only

These cannot be done by an agent — they need a human with the accounts.

- [ ] **Grant the team account access to `SharathSPhD/pravrudhi`.** Write access, since routines push
      `claude/`-prefixed branches and open pull requests.
- [ ] **Install the [Claude GitHub App](https://github.com/apps/claude) on the repository.** Required for the
      PR-review routine's GitHub trigger. `/web-setup` grants cloning but does **not** install the app or enable
      webhooks — these are separate, and the trigger silently never fires without the app.
- [ ] **Hand over the six credential files** in `docs/handover/README.md` §4. Transfer out of band, never in a
      chat log or a commit. Each is 0600 and lives outside the repository.
- [ ] **Decide machine access.** The 5090 (`/home/ss/projects/pravrudhi`) runs the studio engine and the GPU
      nights; the Mac mini (`192.168.0.201`) is the end-user test bed. Cloud routines do **not** need either —
      they work from the repository and the published snapshot. Only give SSH if the team account is to operate
      the hardware rather than review the work.
- [ ] **Turn on Routines for the organisation** if it is a Team or Enterprise plan: an Owner controls the
      Routines toggle at `claude.ai/admin-settings/claude-code`. When off, routines do not run and `/schedule` is
      hidden in the CLI.
- [ ] **Add the Claude tag / Slack connector** to the team account if the team is to be notified there. Then add
      it as a connector on the routines that should post — routines reach connectors through Anthropic's servers,
      so no network allowlist change is needed.
- [ ] **Rotate the `@prabhasa_bot` token.** It was pasted into a chat transcript during setup, so treat it as
      exposed: `/revoke` in BotFather, then update `~/.config/pravrudhi/telegram-product.env`.

## B. Team account, first session

- [ ] Read `docs/handover/README.md` end to end, then `CHARTER.md` §6.
- [ ] `git clone`, then **`make init` before anything else** — it installs `.githooks/commit-msg`, which fails
      any commit whose author is not `SharathSPhD <qbz506@york.ac.uk>` or which carries an attribution trailer.
      Discovering this after writing a commit message is annoying; discovering it after ten is worse.
- [ ] `uv sync && make smoke`. Expect **1837 passing** in about five minutes. If it is not green, stop and find
      out why before changing anything — this is the gate every commit in this repository has passed.
- [ ] `cd app/frontend && npm ci && NEXT_PUBLIC_DEMO=1 npm run build`.
- [ ] Create the five routines in `docs/handover/routines.md`. Start with **stall watch** — it is the one that
      catches this project's characteristic failure.
- [ ] Run each routine once with **Run now** and read the transcript. A green run status means the session
      started and exited, not that the task succeeded; that distinction is the whole subject of this project.

## C. Verify the handover actually took

Do these on the machine, or ask the operator to. Each one has caught a real failure here.

- [ ] `systemctl --user list-timers | grep pravrudhi` — nine units, all with a recent `LAST`.
- [ ] `systemd-run --user --wait --pipe .venv/bin/pravrudhi agents` — **not** `pravrudhi agents` in your shell.
      A unit's PATH is not yours: `opencode` and `codex` are npm globals under nvm, invisible to `systemd --user`,
      and that difference cost five hours of hourly rejected dispatches while every unit reported success.
- [ ] `pravrudhi doctor` — seven checks. `routing` is the one that catches an agent the units cannot see.
- [ ] `pravrudhi watch --root .` and `--root ~/pravrudhi-release`.
- [ ] Message both Telegram bots. The studio bot answers `[Pravrudhi Studio]`, `@prabhasa_bot` answers
      `[Pravrudhi]`. If either says the wrong one, `PRAVRUDHI_EDITION` is unset on that unit.
- [ ] Open `https://sharathsphd.github.io/pravrudhi/app` and confirm `recorded` in the snapshot is under an hour
      old. If it is stale, the publish unit has stopped — check it is not pointed at a disposable worktree.

## D. Handing over the two loops

The studio and product engines are independent and are handed over separately.

**Studio** improves Pravrudhi itself. Its heartbeat works the request backlog: `pravrudhi requests` lists what
the operator has asked for, and the loop dispatches the oldest unmet criterion. A criterion that fails three
paid attempts is reported stalled and left for a person — that budget exists because one criterion was
dispatched nine times in a day, refused every time, and exhausted a paid quota.

**Product** improves the user's own work, from `~/pravrudhi-release`. It currently has no objectives of its own,
so its heartbeat falls to a diagnostic. Giving it real work is a matter of `pravrudhi objectives` in that
workspace, not of code.

## E. What is deliberately not handed over

- **`pravrudhi_kernel/`.** Sealed. Changes go through an ADR request under `docs/decisions/`, which is gitignored
  and lives on the machine. `ADR-0034` is open and is a good example of the form.
- **The ledger.** `research/ledger.jsonl` is local and is the only source of evidence. It is not in the
  repository and must not be committed.
- **The operator's local operating notes** — `CLAUDE.md`, `HANDOFF.md`, `RESTART.md`, `docs/blueprint/`,
  `docs/superpowers/`. Gitignored on purpose. Everything a developer needs from them that can be public is in
  `docs/handover/README.md`.

## F. First week, suggested

1. Let the stall-watch routine run for two days and read every report. It will teach the failure modes faster
   than the documentation does.
2. Take the two open bot items in `README.md` §8 — acting on a message, and tool coverage for candidates, the
   agent trace and the seats table. Both are extensions of a pattern that already exists.
3. Leave the Orca parity gaps alone until the above land. `embedded-terminal` in particular is a deliberate
   refusal, not a backlog item.
