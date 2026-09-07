# Pravrudhi Desktop

An Electron desktop application that finds, starts, and supervises your installed Pravrudhi engine. It ships no Python and requires the engine to be installed separately, including its built frontend. See the [engine installation instructions](https://github.com/SharathSPhD/pravrudhi#readme).

From `app/desktop/`, run development with:

```sh
npm install && npm start
```

Choose your project with **Engine → Choose workspace…**, or set `PRAVRUDHI_WORKSPACE` before launch. The selection persists; the initial default is the installed engine's source project when its built frontend is present, otherwise your home folder. The shell does not initialise or modify the workspace itself.

Build the current platform's distributable with:

```sh
npm run dist
```

Linux produces an AppImage (`npm run dist:linux`, or `npm run dist -- --linux`). For macOS arm64, run `bash build-macos.sh` on Linux or macOS to produce an **unsigned ZIP containing Pravrudhi.app**. Run `bash install-macos.sh /path/to/Pravrudhi-0.3.1-mac-arm64.zip` on the Mac to install in `~/Applications`, smoke-test and launch it. See [macOS build and verification evidence](../../docs/DESKTOP.md). The default macOS builder target remains DMG and requires macOS. Packaging downloads Electron and builder tools. No engine or Python runtime is included; the engine and its frontend must already be installed.

## Installing the packaged app

**Linux (AppImage):** download `Pravrudhi-<version>.AppImage`, make it executable, and run it:

```sh
chmod +x Pravrudhi-*.AppImage
./Pravrudhi-*.AppImage
```

An AppImage mounts itself through FUSE, and many current distributions no longer ship the version 2 library it
needs. Without it the app exits immediately on `dlopen(): error loading libfuse.so.2`, which reads like a broken
download and is not one. Either install that library, or tell the AppImage to unpack itself instead:

```bash
sudo apt install libfuse2          # Debian and Ubuntu; the package is libfuse2t64 on newer releases
# or, with no installation at all:
APPIMAGE_EXTRACT_AND_RUN=1 ./Pravrudhi-*.AppImage
```

No installation step is required beyond that; the AppImage is self-contained. It still finds the engine using the same discovery order as the source app — `PRAVRUDHI_BIN`, then `pravrudhi` on `PATH`, then `~/pravrudhi-release/.pravrudhi/releases/current/.venv/bin/pravrudhi`, then `~/.local/bin/pravrudhi`, then the path remembered by **Locate the engine…** — independent of where the AppImage itself is mounted or run from.

**macOS (DMG):** this build is **unsigned and not notarized** — there is no Apple Developer identity behind it. Gatekeeper will refuse to open it with a plain double-click, reporting the app as damaged or from an unidentified developer. To open it anyway: right-click (or Control-click) `Pravrudhi.app` in Finder and choose **Open**, then confirm **Open** in the dialog; this is required once. Alternatively, clear the quarantine attribute from a terminal: `xattr -dr com.apple.quarantine /Applications/Pravrudhi.app`. Only do this for a DMG you built yourself or otherwise trust.

Run the packaged Linux build's headless smoke check (after `npm run dist:linux`) with:

```sh
npm run smoke:dist
```

It launches the built AppImage the same way `npm run smoke` launches the source app — passing `--no-sandbox` because the SUID sandbox helper cannot be used in this environment, without changing the renderer's own `sandbox: true` setting — and additionally passes `--appimage-extract-and-run` since the AppImage's own FUSE mount is unavailable in a sandbox. It writes the same `launched`/`engine_found`/`engine_url`/`page_title`/`health_ok`/`errors` report shape as `npm run smoke`, to `.smoke-dist/.smoke/report.json` rather than next to the app itself, since the packaged app's own directory is the read-only `app.asar`.

The discovery order is `PRAVRUDHI_BIN`, `pravrudhi` on PATH, `~/pravrudhi-release/.pravrudhi/releases/current/.venv/bin/pravrudhi`, `~/.local/bin/pravrudhi`, the executable remembered by **Locate the engine…**, then `~/pravrudhi/.pravrudhi/releases/current/.venv/bin/pravrudhi`. This last fallback supports the Mac release install even when Finder supplies a minimal PATH. Invalid/non-executable candidates are skipped. A picked binary does not override an earlier valid candidate; change `PRAVRUDHI_BIN` to explicitly override discovery.

The shell first probes `PRAVRUDHI_ENGINE_URL`, its remembered engine URL, and the engine default `http://127.0.0.1:8008`. Only healthy loopback HTTP endpoints are attached. An attached engine is externally owned: Stop disconnects and Restart reconnects without killing it. Otherwise, a free loopback port is selected and the shell launches `pravrudhi app --no-browser --port N --root <workspace>`. A real connection screen remains visible until `/api/health` succeeds, with a deadline of 30 seconds. A missing frontend, failed process, or failed health check leads to recovery. The first-run screen reads `/api/health`, `/api/update`, `/api/requests` (the open backlog), and `/api/inbox` (unsigned packs) through named invoke calls, displays errors per endpoint, and runs doctor. **Open engine interface** loads the engine page; **Engine → Connection and diagnostics** returns to the desktop screen. Doctor runs automatically on failure when a binary exists, and can be rerun. Each check displays its engine-provided name and detail with a copyable recovery or diagnostic command. There is no universal command to install Docker on every OS or repair a corrupted ledger; these checks link to installation guidance or advise restoring a verified backup rather than fabricate a repair. Commands are never executed from the renderer.

Native File, View, Engine, and Help menus support additional windows, engine location/restart/stop, workspace access, updates, reload, zoom, developer tools, docs, and version information. A tray menu reflects engine status and can focus the app or restart the engine. Windows share one engine. The first window's focus is restored on a second application launch. Window bounds and maximized state persist in Electron userData, with off-screen recovery. Closing all windows quits on both platforms, terminating the engine process group; SIGINT/SIGTERM also clean up. Graceful termination escalates to SIGKILL if needed.

Update checks read `GET /api/update`; applying requires the native prompt and invokes `pravrudhi update --apply --channel release --json`. The engine's ApplyResult reason is shown verbatim. The engine owns all update safeguards. Restart via the Engine menu after an applied update. The check has a bounded command timeout; applying has a longer bounded timeout. The shell itself is updated by installing a new desktop package.

The renderer is sandboxed with context isolation and no Node integration. Its preload exposes only status, health, update state, backlog and inbox counts, open interface, locate, restart, stop, doctor, updates, and workspace access through explicitly named invoke calls. IPC validates the sender window, main frame, and origin. New-window requests and navigations send external HTTP(S) links to the system browser; non-web schemes are blocked. Permissions and webviews are denied.

Run the headless logic tests (no GUI is launched):

```sh
node --test test/
```

Tests exercise executable discovery and fallbacks, actual loopback port binding, health retries/deadlines/cancellation, doctor parsing, navigation policy, state persistence, and recovery command quoting. Run the real desktop smoke check without a display:

```sh
npm run smoke
```

The launcher uses `ELECTRON_ENABLE_LOGGING=1` and `xvfb-run -a`. Smoke passes `--no-sandbox` to disable the process-level Chromium sandbox because this environment cannot chown the SUID helper to root and set mode 4755. The renderer keeps `sandbox: true`, `contextIsolation: true`, and `nodeIntegration: false`. When Xvfb is unavailable, it uses Electron headless/offscreen rendering with `ELECTRON_DISABLE_GPU=1` and hardware acceleration disabled. It installs no system packages. Electron and its system runtime libraries must already be available after `npm install`.

The main process writes `.smoke/report.json` containing `launched`, `engine_found`, `engine_url`, `page_title`, `health_ok`, and `errors`. It waits for the first-run API screen, loads the actual engine frontend, records its title after loading, rechecks health, and terminates its owned engine group before exiting. A failed launch, failed endpoint, missing frontend, or timeout exits nonzero. If Electron cannot initialize, the launcher writes a failure report using the same reporter module. Each run removes the previous report first. Smoke uses isolated `.smoke/user-data`; set `PRAVRUDHI_WORKSPACE` or `PRAVRUDHI_ENGINE_URL` to target a particular installation.

Tests also cover API errors, attachment without spawning, menu and tray handlers, process ownership and group termination, single-instance focusing, preload restrictions, first-run rendering, and smoke JSON success/failure paths. No GUI is launched by unit tests.
