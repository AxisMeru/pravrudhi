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
  // The front door's heading is "Pravrudhi"; the sidebar entry that points here is labelled "Improve". This
  // asserted the sidebar's label and went red when the front door was reworked (178e723), and stayed red,
  // because no CI job runs this project — pages.yml runs only --project=deployed and ci.yml has no Playwright.
  ["/", "Pravrudhi"],
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


test("home page bands render", async ({ page }) => {
  const errs: string[] = [];
  page.on("pageerror", (e) => errs.push("PAGEERROR " + String(e).slice(0, 300)));
  page.on("console", (m) => { if (m.type() === "error") errs.push("CONSOLE " + m.text().slice(0, 300)); });
  page.on("response", (r) => { if (r.url().includes("/api/") && r.status() >= 400) errs.push(`HTTP ${r.status()} ${r.url()}`); });
  await page.goto("/");
  await page.waitForTimeout(9000);
  console.log("HOME ERRORS:", errs.join(" || ") || "none");
  console.log("HOME TEXT:", (await page.innerText("body")).slice(0, 420).replace(/\n+/g, " | "));
});


test("system page renders", async ({ page }) => {
  const errs: string[] = [];
  page.on("pageerror", (e) => errs.push("PAGEERROR " + String(e).slice(0, 260)));
  page.on("console", (m) => { if (m.type() === "error") errs.push("CONSOLE " + m.text().slice(0, 260)); });
  await page.goto("/system");
  await page.waitForTimeout(9000);
  console.log("SYS ERRORS:", errs.join(" || ") || "none");
});

test("chat streaming endpoint is used and renders progressively", async ({ page, request }) => {
  await page.goto("/chat");
  await expect(page.getByRole("heading", { name: "Chat" })).toBeVisible();

  // Find the message input and send button
  const input = page.getByPlaceholder("Ask about an objective, a plan, or the evidence behind a number…");
  const sendButton = page.getByRole("button", { name: "Send" });

  // Start intercepting network requests to verify streaming endpoint is used
  let streamEndpointCalled = false;
  page.on("request", (request) => {
    if (request.url().includes("/api/chat/stream")) {
      streamEndpointCalled = true;
    }
  });

  await input.fill("What is happening?");

  // Collect text content over time to verify progressive rendering
  const textSnapshots: string[] = [];
  let lastText = "";

  const captureInterval = setInterval(async () => {
    try {
      // Find the last assistant message bubble
      const bubbles = page.locator('[class*="flex-1"] [class*="rounded-lg"] >> nth=-1');
      const text = await bubbles.innerText().catch(() => "");
      if (text && text !== lastText) {
        textSnapshots.push(text);
        lastText = text;
      }
    } catch {
      // Ignore errors
    }
  }, 100);

  await sendButton.click();

  // Wait for the response to complete
  await expect(sendButton).not.toBeDisabled({ timeout: 30_000 });

  clearInterval(captureInterval);

  // Verify streaming endpoint was called
  expect(streamEndpointCalled, "Frontend should use /api/chat/stream endpoint").toBe(true);

  // A blocking response produces exactly one snapshot: the finished answer, captured once. So "at least one"
  // is satisfied by the very behaviour this test exists to rule out, and the bar has to be two — the text was
  // observed partway through and again later, which only happens if it grew while the stream was open.
  expect(
    textSnapshots.length,
    "chat rendered its answer in one go; at least two observations of growing text are needed to call it streaming",
  ).toBeGreaterThanOrEqual(2);

  // And the growth has to be forward: the last observation must extend the first, not merely differ from it.
  expect(
    textSnapshots[textSnapshots.length - 1].length,
    "the last observation should be longer than the first, showing the answer accumulating",
  ).toBeGreaterThan(textSnapshots[0].length);

  expect(lastText.length, "Chat response should contain text").toBeGreaterThan(0);
});
