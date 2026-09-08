// Record what actually happens when someone uses this, rather than describing it.
//
// The tour was eight static PNGs paged with Prev/Next. The completion review's words: "a single frozen
// screenshot with a caption — there is no captured click, no before/after state change, no video. It shows
// what pages look like, not what happens when you click something." That is fair, and it is what the operator
// asked for twice.
//
// So this drives the real interface against a real engine and records it. Every frame is a consequence of an
// action taken here; nothing is posed. The recording is written to the site's own public directory so the
// published demo carries it.
import { test, expect } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

const OUT = path.resolve(__dirname, "../public/walkthrough");

// Publishing is conditional on the session having actually worked. Playwright records video for a failed test
// too, and the first run of this copied the recording of a failed session into the site's public directory —
// a demo of the product not working, presented as a demo of it working. Set only when every step passed.
let sessionSucceeded = false;

test.use({ video: { mode: "on", size: { width: 1280, height: 800 } }, viewport: { width: 1280, height: 800 } });

test("a real session: navigate by keyboard, open the loop's own record, read a result", async ({ page }) => {
  await page.goto("/");
  await expect(page.locator("main").getByRole("heading", { name: "Pravrudhi", exact: true })).toBeVisible();

  // The command palette, opened the way a person opens it, not by navigating to a URL.
  await page.keyboard.press("Control+k");
  await expect(page.getByPlaceholder(/search|type a command/i).first()).toBeVisible({ timeout: 10_000 });
  await page.keyboard.type("objectives");
  // Click the result by name. Typing and pressing Enter depends on which result ranked first, which is a
  // property of the search rather than of the navigation this is meant to show — and it ranked something else.
  await page.getByRole("option", { name: /objectives/i }).first()
    .or(page.getByText("Objectives", { exact: true }).last()).click({ timeout: 10_000 });
  await expect(page.locator("main").getByRole("heading", { name: "Objectives", exact: true })).toBeVisible();

  // A digit shortcut, which is the other half of the keyboard claim.
  await page.keyboard.press("6");
  await expect(page.locator("main").getByRole("heading", { name: "Progress", exact: true })).toBeVisible();

  // The engine's own record of what it did, reached by clicking rather than by URL.
  await page.keyboard.press("Control+k");
  await page.keyboard.type("models");
  await page.getByRole("option", { name: /models/i }).first()
    .or(page.getByText("Models", { exact: true }).last()).click({ timeout: 10_000 });
  await expect(page.locator("main").getByRole("heading", { name: "Models", exact: true })).toBeVisible();
  await page.waitForTimeout(1200);
  sessionSucceeded = true;
});

test.afterAll(async () => {
  if (!sessionSucceeded) {
    // Leave whatever is published alone. A recording of a failed session is worse than an old one: it shows
    // the product not working while claiming to show it working.
    process.stdout.write("the session did not complete; the published recording was left untouched\n");
    return;
  }
  fs.mkdirSync(OUT, { recursive: true });
  // Playwright writes one .webm per test into its results directory; publish the newest.
  const results = path.resolve(__dirname, "../test-results");
  const videos: string[] = [];
  const walk = (dir: string): void => {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) walk(full);
      else if (entry.name.endsWith(".webm")) videos.push(full);
    }
  };
  if (fs.existsSync(results)) walk(results);
  if (videos.length === 0) throw new Error("no recording was produced; the demo would be stale and silent");
  const newest = videos.sort((a, b) => fs.statSync(b).mtimeMs - fs.statSync(a).mtimeMs)[0];
  fs.copyFileSync(newest, path.join(OUT, "session.webm"));
  process.stdout.write(`recorded ${path.join(OUT, "session.webm")} (${fs.statSync(newest).size} bytes)\n`);
});
