// Static facts about the desktop shell — what app/desktop/main.js, app/desktop/lib/connection.js and
// app/desktop/README.md actually describe, not invented. The shell's own behaviour does not vary by install,
// so unlike lib/system.ts there is no engine to ask and no demo/live split: this is documentation, typed.

export interface Fact {
  title: string;
  detail: string;
}

// From main.js's start()/selectConnection() and the README's discovery-order paragraph.
export const WHAT_IT_DOES: Fact[] = [
  {
    title: "Finds your installed engine, or lets you point at one",
    detail:
      "It checks PRAVRUDHI_BIN, then pravrudhi on your PATH, then a release install under ~/pravrudhi-release, " +
      "then ~/.local/bin/pravrudhi, then whatever you last chose with Engine → Locate engine…. If a loopback " +
      "engine is already answering — one you or the shell remembered — it attaches to that instead of starting " +
      "a second one.",
  },
  {
    title: "Starts it and waits for it to actually answer",
    detail:
      "A free loopback port is chosen and the engine is launched against your workspace. The connection screen " +
      "stays up until /api/health succeeds, with a 30 second deadline, rather than loading a page that isn't " +
      "ready yet.",
  },
  {
    title: "Shows what's wrong instead of a blank window",
    detail:
      "On failure it runs the engine's own doctor checks and lists each one by its own name and detail, with a " +
      "copyable recovery or diagnostic command per failure — never a single generic “unhealthy”.",
  },
  {
    title: "Keeps the engine's lifecycle under a menu and tray",
    detail:
      "Engine → Restart, Stop and Choose workspace…, plus a tray icon that reflects engine status and can focus " +
      "the window or restart the engine from outside the browser tab it would otherwise live in.",
  },
  {
    title: "Opens external links in your browser, not inside the app",
    detail:
      "Documentation, releases, and any other external link the interface produces opens in your system " +
      "browser. Only pages served by the connected engine itself load inside the window.",
  },
  {
    title: "Terminates the engine it started when it closes",
    detail:
      "Closing every window ends the engine process group the shell launched. An engine it only attached to — " +
      "one already running before the shell opened — is left running; Stop just disconnects from it.",
  },
];

export interface PlatformInstall {
  id: "linux" | "macos";
  label: string;
  assetSuffix: string;
  steps: Fact[];
  commands: { label: string; command: string }[];
}

// From the README's "Installing the packaged app" section, verbatim command sequences included.
export const PLATFORM_INSTALLS: PlatformInstall[] = [
  {
    id: "linux",
    label: "Linux — AppImage",
    assetSuffix: "AppImage",
    steps: [
      {
        title: "Download and run",
        detail: "Make the AppImage executable and run it directly — no installer, no package to unpack by hand.",
      },
      {
        title: "If it exits with “error loading libfuse.so.2”",
        detail:
          "An AppImage mounts itself through FUSE, and many current distributions no longer ship the version 2 " +
          "library it needs. That failure reads like a broken download; it isn't. Either install libfuse2 " +
          "(libfuse2t64 on newer releases), or tell the AppImage to unpack itself instead with the environment " +
          "variable below.",
      },
    ],
    commands: [
      {
        label: "Make it executable and run it",
        command: "chmod +x Pravrudhi-*.AppImage\n./Pravrudhi-*.AppImage",
      },
      { label: "Install the missing library (Debian/Ubuntu)", command: "sudo apt install libfuse2" },
      {
        label: "Or run without installing anything",
        command: "APPIMAGE_EXTRACT_AND_RUN=1 ./Pravrudhi-*.AppImage",
      },
    ],
  },
  {
    id: "macos",
    label: "macOS — disk image (unsigned)",
    assetSuffix: "dmg",
    steps: [
      {
        title: "This build is unsigned and not notarized",
        detail:
          "There is no Apple Developer identity behind it. Gatekeeper refuses a plain double-click and reports " +
          "the app as damaged or from an unidentified developer — that is expected, not a bad download.",
      },
      {
        title: "Open it anyway",
        detail:
          "Right-click (or Control-click) Pravrudhi.app in Finder and choose Open, then confirm Open in the " +
          "dialog. This is required once. Alternatively, clear the quarantine attribute from a terminal — only " +
          "for a DMG you built yourself or otherwise trust.",
      },
    ],
    commands: [
      {
        label: "Clear the quarantine attribute",
        command: "xattr -dr com.apple.quarantine /Applications/Pravrudhi.app",
      },
    ],
  },
];

export const RELEASES_URL = "https://github.com/AxisMeru/pravrudhi/releases";
export const DOCS_URL = "https://github.com/AxisMeru/pravrudhi#readme";

// The window this page shows a real capture of. Absent from the repository until someone runs the shell with
// PRAVRUDHI_DESKTOP_SHOT set and commits the result — see main.js's capturePage handler — so callers must
// render correctly with it missing, not assume it is there.
export const SCREENSHOT_SRC = "/desktop/shell-connected.png";
