import { expect, test } from "@playwright/test";

/**
 * Two of the 2026-09-12 defects were exactly the shape a heading-and-status-code check cannot see: the sign-in
 * buttons rendered with no visible text (a Tailwind theme token neither light nor dark mode ever defined, so the
 * button's own text colour matched its own background), and a page whose auth-gated fetch failed showed a
 * "Could not reach the engine's X API" paragraph instead of the sign-in redirect the engine actually intended.
 * Runs against a live engine of either edition (LOCAL_ENGINE_URL, same as edition.spec.ts) and is agnostic to
 * whether that engine has PRAVRUDHI_AUTH on: a page is only wrong here if it shows the broken-text paragraph.
 */

// Mirrors deployed.spec.ts's own anchor: the interface's failure states are short paragraphs beginning with one
// of these phrases, matched at the start of a `main p` so recorded content that merely mentions "the engine" is
// never mistaken for a failure.
const BROKEN_TEXT = /^(Could not reach|could not load|failed to|No engine reachable)/i;

test("the sign-in buttons render with visible text, not matching text-on-background", async ({ page }) => {
  await page.goto("/signin");
  // A build with no NEXT_PUBLIC_SUPABASE_URL renders "Sign-in is not configured for this installation" and
  // neither button exists at all — that read as "element(s) not found" in CI (2026-09-12) until this said so
  // plainly instead.
  const unconfigured = await page.getByText("Sign-in is not configured for this installation").count();
  expect(unconfigured, "sign-in page is unconfigured in this build").toBe(0);
  for (const name of ["Sign in", "Email me a sign-in link instead"]) {
    const button = page.getByRole("button", { name, exact: true });
    await expect(button).toBeVisible();
    const [color, background] = await button.evaluate((el) => {
      const style = getComputedStyle(el);
      return [style.color, style.backgroundColor];
    });
    expect(color, `"${name}" button text colour must differ from its background (got ${color} on ${background})`).not.toBe(background);
  }
});

test("every offered page is signed-in content or the sign-in redirect, never a failure paragraph", async ({ page }) => {
  await page.goto("/settings");
  await page.locator("main").getByRole("heading", { name: "Settings", exact: true }).waitFor();
  // The same beat the banner/sidebar edition check gets elsewhere in this suite.
  await page.waitForTimeout(1500);
  const offered = new Set(
    await page.locator("nav a[href]").evaluateAll((links) => links.map((a) => new URL((a as HTMLAnchorElement).href).pathname)),
  );
  const brokenPages: string[] = [];
  const stuckLoadingPages: string[] = [];
  for (const path of offered) {
    await page.goto(path);
    await page.waitForLoadState("networkidle");
    if (new URL(page.url()).pathname === "/signin") continue; // the engine's own auth gate, not a failure
    const broken = await page.locator("main p").filter({ hasText: BROKEN_TEXT }).count();
    if (broken > 0) brokenPages.push(path);
    // Mirrors deployed.spec.ts's own check: a page stuck on "Loading…" after networkidle settled is a page
    // whose error path never resolves the state — a poll or fetch that fails silently and leaves the spinner
    // running forever, the same shape as /runs on 2026-09-12.
    const loading = await page.getByText(/^Loading(\.\.\.|…)$/).count();
    if (loading > 0) stuckLoadingPages.push(path);
  }
  expect(brokenPages, "no page may show its own \"could not reach\" text; it must render or redirect to /signin").toEqual([]);
  expect(stuckLoadingPages, "no page may stay on \"Loading…\" once the network has gone idle").toEqual([]);
});
