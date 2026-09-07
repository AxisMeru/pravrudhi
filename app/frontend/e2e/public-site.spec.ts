import { expect, test } from "@playwright/test";

/**
 * What a visitor to the public site receives.
 *
 * These assert usefulness, not merely that a page loaded: a visitor must see the actual result, be able to watch a
 * real run, and reach the instructions. The console-error test exists because an earlier version of this site
 * loaded perfectly while emitting a growing stream of failed requests to an address it could never reach.
 */

test("the landing page leads with the measured result, not an error", async ({ page }) => {
  await page.goto("/");
  await expect(page).toHaveTitle(/Pravrudhi/);
  await expect(page.getByText(/Recorded demo/i)).toBeVisible();
  await expect(page.getByText(/No engine reachable/i)).toHaveCount(0);

  // the headline improvement, read from the engine's own external-benchmark record
  await expect(page.getByText(/scored by an independent benchmark tool/i)).toBeVisible();
  const gain = page.getByText(/^\+\d+\.\d+ points$/);
  await expect(gain).toBeVisible();
  expect(parseFloat((await gain.innerText()).replace(/[^\d.]/g, ""))).toBeGreaterThan(0);
});

test("a real run replays, and its numbers move", async ({ page }) => {
  await page.goto("/");
  const panel = page.getByText(/A real run, replayed/i);
  await expect(panel).toBeVisible();
  await expect(page.getByText(/Every line is from the engine's record/i)).toBeVisible();

  // the replay advances on its own: the "tried" counter must climb without any interaction
  const tried = page.locator("text=tried").locator("xpath=preceding-sibling::div[1]");
  await expect.poll(async () => parseInt((await tried.innerText()) || "0", 10), { timeout: 30_000 }).toBeGreaterThan(0);
  await expect(page.getByRole("button", { name: /replay/i })).toBeVisible();
});

test("the site does not call an address it cannot reach", async ({ page }) => {
  const failures: string[] = [];
  page.on("console", (m) => {
    if (m.type() === "error") failures.push(m.text());
  });
  page.on("requestfailed", (r) => failures.push(`requestfailed ${r.url()}`));
  await page.goto("/");
  await page.waitForTimeout(6000);
  const loopback = failures.filter((f) => /localhost|127\.0\.0\.1|loopback/i.test(f));
  expect(loopback, `the public build must not call a local engine:\n${loopback.join("\n")}`).toHaveLength(0);
});

// Every page the site serves, which is the same list the publish check knows. It used to be five, and the
// thirteen that were not here shipped unexercised: the parity page rendered whole objects as React children and
// died with "This page couldn't load", and nothing noticed, because the crash happens in the browser after the
// HTML has already been served and the publish check reads the HTML.
const EVERY_PAGE = [
  "/", "/tour", "/appetite", "/objectives", "/progress", "/inbox", "/requests", "/candidates",
  "/swarm", "/diffs", "/memory", "/heartbeat", "/catalogue", "/runs", "/search", "/models",
  "/machines", "/parity", "/settings",
] as const;

// What a page says when it has failed. A page can answer 200, render its heading and still be broken, so the
// check has to be for these rather than for a status code.
const FAILED = [
  "this page couldn't load",
  "could not reach",
  "couldn't load",
  "failed to load",
  "no engine reachable",
  "application error",
];

test("every page opens, renders content and does not crash", async ({ page }) => {
  const broken: string[] = [];

  for (const path of EVERY_PAGE) {
    const crashes: string[] = [];
    const onError = (e: Error) => crashes.push(e.message);
    page.on("pageerror", onError);

    await page.goto(path);
    // The data arrives from a client-side fetch after navigation resolves, so this has to be a retrying
    // assertion rather than a single read.
    await expect(page.locator("body"), `${path} rendered nothing at all`).not.toBeEmpty();
    await page.waitForTimeout(1500);

    const text = ((await page.locator("body").innerText()) || "").toLowerCase();
    const hit = FAILED.find((m) => text.includes(m));
    if (hit) broken.push(`${path} shows "${hit}"`);
    if (crashes.length) broken.push(`${path} threw: ${crashes[0]}`);

    page.off("pageerror", onError);
  }

  expect(broken, `pages broken on the public build:\n${broken.join("\n")}`).toHaveLength(0);
});

// Both of these read the page once, immediately after navigation, and both failed against a site that renders
// correctly: the content arrives from a client-side fetch that had not resolved yet. `toContainText` retries until
// the expectation holds or the timeout expires, which is the question these tests meant to ask.
test("what the loop produced is shown with a real before and after", async ({ page }) => {
  await page.goto("/models");
  await expect(page.locator("main")).toContainText(/c-0045|adapter|harness/i);
});

test("the machines page shows measured hardware", async ({ page }) => {
  await page.goto("/machines");
  await expect(page.locator("main")).toContainText(/cuda|metal/i);
});
