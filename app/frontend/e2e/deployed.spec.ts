import { expect, test as base, type ConsoleMessage, type Request } from "@playwright/test";

/**
 * The public-site project records against a fixed host and never notices when the deployed build itself is
 * broken: demo.ts once fetched "/demo.json" root-absolute, which 404s under GitHub Pages' /pravrudhi/app basePath
 * while passing every recorded-data assertion run against the root-served host. This suite runs against the
 * actual deployed URL (DEPLOYED_URL, defaulting to the live GitHub Pages site) so a broken deployment is caught
 * by CI rather than by a visitor staring at "Could not reach the engine's swarm API".
 */

const test = base.extend<{ originFailures: void }>({
  originFailures: [async ({ context, baseURL }, use): Promise<void> => {
    const siteOrigin = new URL(baseURL ?? "https://axismeru.github.io").origin;
    const badOriginRequests: string[] = [];
    context.on("response", (response): void => {
      if (new URL(response.url()).origin === siteOrigin && response.status() >= 400) {
        badOriginRequests.push(`${response.status()} ${response.url()}`);
      }
    });
    await use();
    expect(badOriginRequests, "every request to the site's own origin must return < 400").toEqual([]);
  }, { auto: true }],
});

// Every page the interface serves, with the heading that proves it rendered rather than fell back to the shell.
// A page added without being added here is a page nobody checks on the deployed site, which is how the home
// page's heading changed under a stale assertion and how ten newer pages went unchecked entirely.
const PAGES = [
  ["/", "Pravrudhi"],
  ["/start", "Start"],
  ["/tour", "Guided tour"],
  ["/appetite", "Appetite"],
  ["/objectives", "Objectives"],
  ["/progress", "Progress"],
  ["/inbox", "Inbox"],
  ["/requests", "Requests"],
  ["/candidates", "Candidates"],
  ["/swarm", "Swarm"],
  ["/diffs", "Diffs"],
  ["/memory", "Memory"],
  ["/heartbeat", "Heartbeat"],
  ["/catalogue", "Catalogue"],
  ["/system", "System"],
  ["/chat", "Chat"],
  ["/runs", "Runs"],
  ["/models", "Models"],
  ["/machines", "Machines"],
  ["/settings", "Settings"],
  ["/install", "Get it running"],
] as const;

// The interface's own failure states are short paragraphs that begin with one of these phrases. The match is
// anchored to the start of a paragraph because the recording also shows the operator's asks verbatim, and an
// ask about a broken page ("it still has no engine reachable") is content, not a failure state (2026-09-12).
const BROKEN_TEXT = /^(Could not reach|could not load|failed to|No engine reachable)/i;

// baseURL carries the GitHub Pages /pravrudhi/app prefix with a trailing slash; goto() resolves a relative path
// against it, but a leading-slash path resolves against the origin and drops that prefix entirely — exactly the
// class of bug this suite exists to catch. So navigation always uses the relative form.
function relative(path: (typeof PAGES)[number][0]): string {
  return path === "/" ? "" : path.slice(1);
}

for (const [path, heading] of PAGES) {
  test(`${path} renders usefully on the deployed site`, async ({ page }): Promise<void> => {
    const consoleErrors: string[] = [];
    const pageErrors: string[] = [];
    page.on("console", (message: ConsoleMessage): void => {
      if (message.type() === "error") consoleErrors.push(`${message.text()} at ${message.location().url}`);
    });
    page.on("pageerror", (error: Error): void => {
      pageErrors.push(error.message);
    });

    const response = await page.goto(relative(path));
    expect(response, `${path} must return a document`).not.toBeNull();
    expect(response?.status(), `${path} must respond 200`).toBe(200);

    await expect(
      page.locator("main").getByRole("heading", { name: heading, exact: true }),
    ).toBeVisible();

    // Let client-side fetches (demo.json, API calls) settle before checking for stale loading/error states.
    await page.waitForLoadState("networkidle");

    await expect(page.locator("main p").filter({ hasText: BROKEN_TEXT })).toHaveCount(0);
    await expect(page.getByText(/^Loading(\.\.\.|…)$/)).toHaveCount(0);

    expect(consoleErrors, `${path} must not emit console errors:\n${consoleErrors.join("\n")}`).toEqual([]);
    expect(pageErrors, `${path} must not emit uncaught page errors:\n${pageErrors.join("\n")}`).toEqual([]);
  });
}

test("the swarm page lists at least one agent row and one routing tier", async ({ page }): Promise<void> => {
  await page.goto("swarm");
  await page.waitForLoadState("networkidle");
  await expect(page.getByText(/Could not reach the engine's swarm API/i)).toHaveCount(0);

  const fleetSection = page.locator("section", { has: page.getByRole("heading", { name: "Fleet" }) });
  await expect(fleetSection.locator("table tbody tr").first()).toBeVisible();

  const routingSection = page.locator("section", { has: page.getByRole("heading", { name: "Routing" }) });
  await expect(routingSection.locator("table tbody tr").first()).toBeVisible();
});

test("the heartbeat page shows a beat or an explicit empty state, never a bare spinner", async ({ page }): Promise<void> => {
  await page.goto("heartbeat");
  await expect
    .poll(async () => {
      const loading = await page.getByText("Loading…").count();
      return loading;
    }, { timeout: 15_000 })
    .toBe(0);

  const hasEmptyState = await page.getByText(/No heartbeats recorded yet/i).count();
  const hasTimeline = await page.getByRole("heading", { name: "Timeline" }).count();
  expect(hasEmptyState + hasTimeline, "the heartbeat page must show either a beat or a named empty state").toBeGreaterThan(0);
});

test("the progress page shows at least one benchmark chart", async ({ page }): Promise<void> => {
  await page.goto("progress");
  await page.waitForLoadState("networkidle");
  await expect(page.getByRole("heading", { name: "Benchmarks" })).toBeVisible();
  const charts = page.locator("svg");
  expect(await charts.count()).toBeGreaterThan(0);
});
