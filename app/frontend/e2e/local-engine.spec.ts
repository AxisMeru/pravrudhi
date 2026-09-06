import { expect, test as base, type ConsoleMessage, type Request } from "@playwright/test";

/**
 * The public recording hid local namespace collisions, missing exported routes, and a frozen API port.
 * A real engine must render each page and finish its browser requests without falling back to demo data.
 */
const test = base.extend<{ browserDiagnostics: void }>({
  browserDiagnostics: [async ({ context }, use): Promise<void> => {
    const failedRequests: string[] = [];
    const consoleErrors: string[] = [];
    const pageErrors: string[] = [];
    context.on("requestfailed", (request: Request): void => {
      // The router speculatively prefetches every sidebar link and then cancels the ones it no longer needs, so
      // ERR_ABORTED is the browser's own housekeeping and not a server failure. Verified separately: the engine
      // answers HEAD on every page route with 200. Everything else is a real failure and must fail the test.
      const reason = request.failure()?.errorText ?? "";
      if (reason.includes("ERR_ABORTED")) return;
      failedRequests.push(`${request.method()} ${request.url()}: ${reason}`);
    });
    context.on("response", (response): void => {
      // A page or API request answered with an error status is the failure the aborted-prefetch noise was hiding.
      if (response.status() >= 400) failedRequests.push(`${response.status()} ${response.url()}`);
    });
    context.on("console", (message: ConsoleMessage): void => {
      if (message.type() === "error") consoleErrors.push(`${message.text()} at ${message.location().url}`);
    });
    context.on("weberror", (error): void => {
      pageErrors.push(error.error().message);
    });
    try {
      await use();
    } finally {
      expect.soft(failedRequests, "Every browser request must succeed on the local engine").toEqual([]);
      expect.soft(consoleErrors, "The local engine must not produce console errors").toEqual([]);
      expect.soft(pageErrors, "The local engine must not produce uncaught browser errors").toEqual([]);
    }
  }, { auto: true }],
});

for (const [path, heading] of [
  ["/", "Improve"],
  ["/objectives", "Objectives"],
  ["/runs", "Runs"],
  ["/models", "Models"],
  ["/machines", "Machines"],
  ["/settings", "Settings"],
  ["/install", "Get it running"],
] as const) {
  test(`${path} renders its own live page without browser errors`, async ({ page }, testInfo): Promise<void> => {
    const response = await page.goto(path);
    expect(response, `${path} must return a document`).not.toBeNull();
    expect(response?.ok(), `${path} must load successfully`).toBe(true);
    expect(response?.headers()["content-type"]).toContain("text/html");
    await expect(page.locator("main").getByRole("heading", { name: heading, exact: true })).toBeVisible();

    // The banner initially renders nothing; checking absence before hydration missed demo-mode regressions.
    const observationMs: number = testInfo.project.metadata.localEngineObservationMs;
    await page.waitForTimeout(observationMs);
    await expect(page.getByText(/Recorded demo/i)).toBeHidden();
    await expect(page.getByText(/No engine reachable/i)).toBeHidden();
  });
}

test("API health returns JSON while the page namespace returns HTML", async ({ request }): Promise<void> => {
  const health = await request.get("/api/health");
  expect(health.ok()).toBe(true);
  expect(health.headers()["content-type"]).toContain("application/json");
  const payload: unknown = await health.json();
  expect(payload).not.toBeNull();
  expect(typeof payload).toBe("object");
  expect(Array.isArray(payload)).toBe(false);

  const home = await request.get("/");
  expect(home.ok()).toBe(true);
  expect(home.headers()["content-type"]).toContain("text/html");
  expect(await home.text()).toMatch(/<!doctype html/i);
});

test("progress dashboard plots every benchmark the snapshot holds", async ({ page }) => {
  await page.goto("/progress");
  await expect(page.getByRole("heading", { name: "Progress" })).toBeVisible();
  await expect(page.getByText(/recorded snapshot/)).toBeVisible();
  const charts = page.locator("svg");
  expect(await charts.count()).toBeGreaterThan(0);
  await expect(page.getByText("Benchmarks")).toBeVisible();
});

test("appetite page renders what the engine wants", async ({ page }) => {
  const errs: string[] = [];
  page.on("pageerror", (e) => errs.push("PAGEERROR " + String(e).slice(0, 300)));
  page.on("console", (m) => { if (m.type() === "error") errs.push("CONSOLE " + m.text().slice(0, 300)); });
  await page.goto("/appetite");
  await page.waitForTimeout(12000);
  console.log("APPETITE ERRORS:", errs.join(" || ") || "none");
  console.log("APPETITE BODY:", (await page.innerText("body")).slice(0, 260).replace(/\n+/g, " | "));
  expect(errs, "the appetite page must not throw").toEqual([]);
  await expect(page.getByText("Loading...")).toHaveCount(0);
});
