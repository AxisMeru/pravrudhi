# macOS desktop build and installation

Status on 2026-09-07: **installed and running on the Mac mini**.

The scripts below were written by an agent that could not execute them: running
inside another agent's sandbox, it had neither SSH sockets nor npm downloads, and
it said so rather than implying otherwise. They were then run outside that sandbox
and both worked.

What was actually done and observed:

- `bash build-macos.sh` on the Linux box produced
  `dist/Pravrudhi-0.3.1-mac-arm64.zip`, 324 MB, code signing skipped as expected
  when cross-building.
- The archive and installer were copied to the Mac mini and
  `bash install-macos.sh Pravrudhi-0.3.1-mac-arm64.zip` was run there.
- The binary was confirmed `Mach-O 64-bit executable arm64`, quarantine was
  cleared, and the app was installed to `~/Applications/Pravrudhi.app`.
- Its smoke run reported `launched: true`, `engine_found: true`, `health_ok: true`
  and `errors: []`, against an engine it started itself on `127.0.0.1:58582`. The
  captured page carried the real interface, not a blank window.
- A screenshot is at `~/desktop-install/macos-evidence/interface.png` on that
  machine, and the application remained running afterwards.

One correction was needed before it worked. The installer pointed the engine at
`$HOME/pravrudhi`, which on this Mac is a llama.cpp directory rather than an
engine workspace. Starting there gives a running process with nothing behind it,
which reads as a working install until someone opens a page. It now prefers
`$HOME/pravrudhi-release`, the install the update channel maintains, and falls
back only when there is no release.

## Model provider credentials

The two editions answer "whose keys pay for a model call" differently, and both halves belong in one place so
neither is assumed from the other.

A product install brings none of its own. Its route budget (`app/desktop/lib/api.js`'s `ROUTES`) carries no path
that could run model work before a user has stored a key — `app/desktop/test/edition.test.js` asserts that no
route template contains `chat` — and the one route it does carry, `POST /api/providers/:id/key` /
`DELETE /api/providers/:id/key`, is exactly the step that lets a user bring and configure their own.

An admin caller — `roles.role_of` in `src/pravrudhi/api/roles.py` returning `ADMIN` — on the Studio edition skips
that step entirely. `store_for_project` in `src/pravrudhi/application/credentials.py` hands back the engine's own
credential store whenever the project asked for is the engine's own root, which is what an unauthenticated local
caller always asks for (the desktop operator: `role_of(None)` is `ADMIN` whenever `auth_mode()` is `DISABLED`) —
no key is brought or configured. The model work Studio runs on itself follows the same rule: the swarm that
drives Pravrudhi's own self-improvement loop draws its agent seats from the Lite Plan routing table
(`configs/routing.yaml`), including the subscribed Alibaba Lite Plan seats (`qwen-lite-max`, `qwen-lite-flash`),
without any bring-your-own step either.

## Repeatable build

Use Node and npm on the Linux build machine (or a Mac build machine):

```sh
cd app/desktop
bash build-macos.sh
```

The script uses `npm ci` with the existing lockfile, then the local
electron-builder with `--mac zip --arm64` and signing disabled. Expected output:
`app/desktop/dist/Pravrudhi-0.3.1-mac-arm64.zip`. A ZIP avoids the macOS-only DMG
creation step. No Node, npm or Cargo installation is needed on the target Mac.
Downloads require network access. Linux's existing AppImage configuration remains
available with `npm run dist:linux`.

This is **unsigned and not notarized**, intended for the operator's own machines.
It is not a signed, publicly distributable macOS release. The installer clears
quarantine only on the app extracted from the supplied trusted build.

## Mac installation and verification (not executed successfully here)

From `app/desktop`, after a successful build, transfer the artifact and installer:

```sh
ssh -F /dev/null sharath@192.168.0.201 'mkdir -p ~/desktop-install'
scp -F /dev/null dist/Pravrudhi-0.3.1-mac-arm64.zip install-macos.sh \
  sharath@192.168.0.201:desktop-install/
ssh -F /dev/null sharath@192.168.0.201 \
  'cd ~/desktop-install && bash install-macos.sh Pravrudhi-0.3.1-mac-arm64.zip'
```

Run with the operator logged into the Mac GUI. The installer checks Darwin/arm64,
extracts with native `ditto`, checks the executable architecture, and installs into
`~/Applications/Pravrudhi.app` without sudo. Quit any existing Pravrudhi application
before rerunning. It then runs that installed executable in smoke mode, using
`~/pravrudhi` as workspace and a separate smoke settings directory. The shell now
also discovers `~/pravrudhi/.pravrudhi/releases/current/.venv/bin/pravrudhi`, after
the existing discovery candidates, without relying on Finder's PATH. That
specific executable path has not been checked on the Mac; if its layout differs,
set `PRAVRUDHI_BIN` to the installed engine executable and use **Locate engine**
for the normal GUI launch. The desktop includes neither an engine nor its frontend.

Smoke verifies first-run API loading, loads the engine interface, checks for body
text, captures the engine page before quitting, and records health and title.
The installer bounds the smoke run to 120 seconds and checks its exit status and
report using the Mac's `/usr/bin/python3`. On success it opens the installed app
normally with `open`, leaving it available for visual inspection.

Evidence is written under `~/desktop-install/macos-evidence/`:

- `.smoke/report.json`: launch, engine URL, title, health and failures.
- `launch.log`: actual Electron process output.
- `interface.png`: captured engine page, not the initial diagnostics page.
- `interface.png.json`: observed URL, title and first 2,000 body-text characters.

Inspect the screenshot and normal desktop window before asserting that the UI is
usable; nonempty DOM text alone does not prove correct rendering. Copy evidence
back into the permitted desktop directory:

```sh
scp -F /dev/null -r sharath@192.168.0.201:desktop-install/macos-evidence ./
```

## Actual commands and output from this session

The original connection attempt exited 255:

```text
$ ssh -o BatchMode=yes -o ConnectTimeout=10 sharath@192.168.0.201 'printf "desktop connection ready\n"'
Bad owner or permissions on /etc/ssh/ssh_config.d/20-systemd-ssh-proxy.conf
```

Bypassing the system SSH configuration also exited 255:

```text
$ ssh -F /dev/null -o BatchMode=yes -o ConnectTimeout=10 sharath@192.168.0.201 'printf "desktop connection ready\n"'
socket: Operation not permitted
ssh: connect to host 192.168.0.201 port 22: failure
```

Dependency installation, from `app/desktop`, exited 1:

```text
$ npm install --no-audit --no-fund --loglevel=error --cache .npm-cache
npm error code EAI_AGAIN
npm error syscall getaddrinfo
npm error errno EAI_AGAIN
npm error request to https://registry.npmjs.org/yocto-queue/-/yocto-queue-0.1.0.tgz failed, reason: getaddrinfo EAI_AGAIN registry.npmjs.org
```

`bash build-macos.sh` exited 1 at `npm ci`, with the same EAI_AGAIN error for
yocto-queue; electron-builder was not reached.

The exact required acceptance command was run twice and exited 1 with no output
because npm was silent. Its `npm test` portion was not reached:

```sh
cd app/desktop && npm install --no-audit --no-fund --silent && npm test
```

Separate verification after the changes, from `app/desktop`:

```text
$ npm test
> pravrudhi-desktop@0.3.1 test
> node --test test/
✔ test (63.850873ms)
ℹ tests 1
ℹ suites 0
ℹ pass 1
ℹ fail 0
ℹ cancelled 0
ℹ skipped 0
ℹ todo 0
ℹ duration_ms 67.601349
```

Because this runner summarized only the test file, the suite entry point was also
run directly. Its real summary was:

```text
$ node test/index.js
ℹ tests 30
ℹ suites 0
ℹ pass 29
ℹ fail 0
ℹ cancelled 0
ℹ skipped 1
ℹ todo 0
ℹ duration_ms 42.015056
```

The skipped test reports: `Sandbox prohibits loopback sockets; selection contract
covered with an injected server.` The new minimal-PATH Mac discovery test passed.
`bash -n build-macos.sh install-macos.sh` and `node --check main.js` exited 0
without output. These checks do not establish a working packaged Mac application.

Other failed local steps: the initial read found `docs/DESKTOP.md` absent; an early
read accidentally used `app/desktop/` prefixes while already in that directory
and failed with `No such file or directory`. One `npm test` invocation accidentally
ran from the worktree root and failed with `ENOENT` for its nonexistent
`package.json`; it was rerun in `app/desktop` as shown above. No repository survey
or unnamed source-file read was needed.
