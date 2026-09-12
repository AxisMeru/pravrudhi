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

## The desktop nightly

A sibling check, `pravrudhi-e2e-desktop-nightly.service`/`.timer` (03:30, after the web nightly above), that
signs the same account into the *released, packaged* desktop shell rather than a browser —
`run_desktop_nightly.sh` runs `pravrudhi-app/desktop/nightly-dist.js`, which itself resolves the latest
release via the GitHub API, downloads the Linux AppImage and its `SHA256SUMS`, verifies the checksum, reads
that release's own pinned `engine/ENGINE_VERSION` (via `git show <tag>:...`, not this checkout's HEAD, which
may have moved since the release was cut) and installs it into a scratch venv, then launches the packaged
shell against a fresh local root with `PRAVRUDHI_AUTH=required` and the real Supabase project — sign-in,
default-workspace bootstrap and one run, driven inside the shell's own loaded page.

**Its exact claim, because it is easy to overstate: real account, real auth, released shell, fresh local
root — not the hosted engine's data.** `desktop/lib/connection.js`'s `loopbackOrigin` refuses any
non-loopback engine origin by design (the product is a shell over the user's own hardware, not a client for a
remote one), so the packaged shell cannot reach the hosted product engine at all. The web nightly above is
what proves the hosted engine; this proves the shell.

Install the same way as the web nightly's units, from this directory. It needs two more `EnvironmentFile`s
than the web nightly, both required: `~/.config/pravrudhi/supabase.env` (`SUPABASE_URL` — the engine's own
token verification needs nothing else; never a service key) alongside `~/.config/pravrudhi/e2e.env`. A third,
optional file, `~/.config/pravrudhi/github-axismeru.env` (`PRAVRUDHI_GITHUB_TOKEN_AXISMERU`), raises the
unauthenticated 60/hr GitHub API limit that the release lookup and the pinned-engine wheel install both call
against — safe to omit, but a scheduled run without it can fail on rate limiting alone. Reports land beside
the web nightly's, `~/.local/share/pravrudhi-hosted/e2e/desktop-<UTC timestamp>.log` (with `desktop-latest.log`
pointing at the newest), each carrying the path to `nightly-dist.js`'s own more detailed JSON report.
