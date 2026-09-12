# Nightly: a real, signed-in check of both hosted doors

Everything else that checks the hosted doors (deploy/gateway) either runs unauthenticated or against a local
engine. This is the one check that signs in as a real, ordinary account and proves what a real user's session
actually sees — including that PRAVRUDHI_ADMINS holds on the live Studio engine, not just in `roles.py`'s own
tests.

## What it runs

Two Playwright projects, one per repository, both named `live-chromium`:

* `pravrudhi-app/frontend` (`e2e/live.spec.ts`) — signs in, clicks through every offered page (no failure
  paragraph, no stuck "Loading…"), then writes one real note and deletes it again, leaving nothing behind.
* `pravrudhi/app/frontend` (`e2e/live-admin-boundary.spec.ts`) — signs in with the *same* account against
  Studio's hosted door and asserts every admin-only route answers 403, while a user-facing one still answers
  normally — the account is deliberately not on `PRAVRUDHI_ADMINS`.

Both read `E2E_EMAIL`/`E2E_PASSWORD` from `~/.config/pravrudhi/e2e.env` (mode 600), passed in only through the
service's `EnvironmentFile=`. Neither the script nor either spec file ever prints, logs, or otherwise moves
either value off this box.

## Install

```bash
cp deploy/e2e-nightly/pravrudhi-e2e-nightly.service deploy/e2e-nightly/pravrudhi-e2e-nightly.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now pravrudhi-e2e-nightly.timer
```

Run it once by hand to check it before waiting for 03:00: `systemctl --user start pravrudhi-e2e-nightly.service`.

## Reports and failure

Each run writes a dated log to `~/.local/share/pravrudhi-hosted/e2e/<UTC timestamp>.log` (`latest.log` always
points at the newest one) and exits non-zero if either project failed, so a failed night shows up in
`systemctl --user --failed` and `journalctl --user -u pravrudhi-e2e-nightly` without anyone needing to go
looking for it.

## Known limits

* Assumes both repository checkouts already have their frontend `node_modules` reachable (`npm ci` runs each
  night to keep them in step with whatever the checkout is on) and a Chromium build cached by Playwright
  (`npx playwright install chromium` — browser only, no `--with-deps`: this unit has no way to run the
  interactive `apt-get` step that needs, so the host's Playwright system libraries are assumed already present,
  as they already are on this box).
* A rehearsal against a preview deployment can override either project's target: `LIVE_URL=<url> npx
  playwright test --project=live-chromium` in the relevant frontend directory.
