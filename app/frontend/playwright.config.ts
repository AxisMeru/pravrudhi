import { defineConfig, devices } from "@playwright/test";
import path from "node:path";

const localEngineHost: string = "127.0.0.1";
const localEnginePort: number = 8137;
// An engine that is already running (a product install on its own port, the studio's systemd app) is named in
// LOCAL_ENGINE_URL; then no engine is started here and the `edition` project can be run against either edition.
const externalEngineURL: string | undefined = process.env.LOCAL_ENGINE_URL?.replace(/\/+$/, "");
const localEngineURL: string = externalEngineURL ?? `http://${localEngineHost}:${localEnginePort}`;
const localEngineObservationMs: number = 6_000;

// Which --project values this invocation asked for, so the local engine is only spun up when local-engine
// tests might actually run. Neither public-site nor deployed needs it, and deployed CI has no Python venv.
function requestedProjects(argv: readonly string[]): string[] {
  const projects: string[] = [];
  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i];
    if (arg === "--project" && argv[i + 1]) projects.push(argv[i + 1]);
    else if (arg.startsWith("--project=")) projects.push(arg.slice("--project=".length));
  }
  return projects;
}
const requested = requestedProjects(process.argv);
const needsLocalEngine =
  requested.length === 0 || requested.some((name) => name.startsWith("local-engine") || name === "capture");

// The three rendering engines behind every major desktop browser: Chromium is Chrome and Edge, WebKit is
// Safari, Gecko is Firefox. Running one of them and calling the result "works in browsers" is the claim this
// dimension exists to stop being a guess — a CSS or API difference in Safari is invisible to a Chromium-only
// suite, and Safari is the one that most often differs.
const ENGINES = [
  { suffix: "chromium", device: devices["Desktop Chrome"] },
  { suffix: "firefox", device: devices["Desktop Firefox"] },
  { suffix: "webkit", device: devices["Desktop Safari"] },
] as const;

function acrossEngines<T extends { name: string; use?: object }>(project: T) {
  return ENGINES.map((engine) => ({
    ...project,
    name: `${project.name}-${engine.suffix}`,
    use: { ...engine.device, ...(project.use ?? {}) },
  }));
}

/**
 * The deployed recording could pass while the engine served JSON in place of pages or called the wrong port.
 * The public-site project preserves that coverage; local-engine exercises the real CLI and static export;
 * deployed exercises the actual published GitHub Pages build. Build the frontend before running local-engine
 * so the engine has the actual pages to serve.
 */
export default defineConfig({
  testDir: "./e2e",
  // The recording is not part of any ordinary run: it takes a minute, writes into the site's public directory
  // and would make every suite slower for no assurance. Asked for by name, it runs.
  testIgnore: process.argv.some((a) => a.includes("capture")) ? [] : ["capture-demo.spec.ts"],
  timeout: 45_000,
  expect: { timeout: 15_000 },
  retries: 1,
  reporter: [["list"]],
  use: {
    // The recorded demo lives on Pages under the AxisMeru organisation (ADR-0051, addendum 2); the Vercel apps are
    // real installs and carry no recording.
    baseURL: process.env.BASE_URL ?? "https://axismeru.github.io/pravrudhi/app/",
    trace: "retain-on-failure",
  },
  projects: [
    ...acrossEngines({
      name: "public-site",
      testMatch: "public-site.spec.ts",
    }),
    // The recording of a real session, kept out of the ordinary suites: it starts an engine, drives it, and
    // writes a video into the site's public directory, which is a build step rather than a test.
    {
      name: "capture",
      testMatch: "capture-demo.spec.ts",
      use: { baseURL: localEngineURL },
    },
    ...acrossEngines({
      name: "local-engine",
      testMatch: "local-engine.spec.ts",
      use: { baseURL: localEngineURL },
      metadata: { localEngineObservationMs },
    }),
    // Edition separation on a live engine of either edition (ADR-0049; the 2026-09-11 desktop review): the
    // product must not offer or render Studio's pages, and Studio must keep them. Run against a running engine:
    // LOCAL_ENGINE_URL=http://127.0.0.1:8300 npx playwright test --project edition-chromium
    ...acrossEngines({
      name: "edition",
      testMatch: ["edition.spec.ts", "edition-signin-guards.spec.ts"],
      use: { baseURL: localEngineURL },
    }),
    ...acrossEngines({
      name: "deployed",
      testMatch: "deployed.spec.ts",
      // A trailing slash is load-bearing: goto() resolves a relative path against this base, and a bare
      // leading-slash path would resolve against the origin instead, dropping the GitHub Pages /pravrudhi/app
      // prefix entirely (the exact class of bug this suite exists to catch).
      use: { baseURL: `${(process.env.DEPLOYED_URL ?? "https://axismeru.github.io/pravrudhi/app").replace(/\/+$/, "")}/` },
    }),
    // The nightly's Studio half (pravrudhi-e2e-nightly.service): the real hosted door
    // (deploy/gateway/README.md), signed in as the same non-admin account pravrudhi-app's own live-chromium
    // uses, proving PRAVRUDHI_ADMINS actually holds on the live engine rather than just in roles.py's tests.
    {
      name: "live-chromium",
      testMatch: "live-admin-boundary.spec.ts",
      timeout: 60_000,
      use: { baseURL: (process.env.LIVE_URL ?? "https://pravrudhi.vercel.app").replace(/\/+$/, ""), ...devices["Desktop Chrome"] },
    },
  ],
  webServer: needsLocalEngine && !externalEngineURL
    ? {
        // `python` is not on PATH here; the venv interpreter is the one the engine is installed into.
        command: `.venv/bin/python -m pravrudhi app --no-browser --port ${localEnginePort} --host ${localEngineHost}`,
        cwd: path.resolve(__dirname, "../.."),
        url: `${localEngineURL}/api/health`,
        reuseExistingServer: false,
      }
    : undefined,
});
