import { expect, test } from "@playwright/test";

/**
 * The two editions are one interface with an edition filter over it (ADR-0049 names the split that ends this).
 * Until the product ships its own interface, this is the check that the filter is complete on a LIVE engine of
 * either edition: the operator opened both desktop apps on 2026-09-11 and saw almost the same thing, because seven
 * Studio pages had shipped after the filter was written. Point LOCAL_ENGINE_URL at a running engine.
 */

const STUDIO_ONLY = ["/appetite", "/inbox", "/candidates", "/swarm", "/diffs", "/heartbeat", "/trace",
  "/parity", "/search", "/system", "/requests", "/machines", "/tour", "/desktop"];
const EVERY_EDITION: Array<[string, string]> = [["/objectives", "Objectives"], ["/runs", "Runs"], ["/models", "Models"],
  ["/settings", "Settings"], ["/chat", "Chat"], ["/nyaya", "Nyaya"]];

async function editionOf(request: Parameters<Parameters<typeof test>[2]>[0]["request"]): Promise<string> {
  const me = await request.get("/api/me");
  expect(me.ok(), "/api/me must answer on a live engine").toBe(true);
  return String(((await me.json()) as { edition?: string }).edition ?? "");
}

test("the engine names its edition", async ({ request }) => {
  const edition = await editionOf(request);
  expect(["Pravrudhi", "Pravrudhi Studio"]).toContain(edition);
});

test("pages every edition keeps render their heading", async ({ page }) => {
  for (const [path, heading] of EVERY_EDITION) {
    const response = await page.goto(path);
    expect(response?.ok(), `${path} must load`).toBe(true);
    await expect(page.locator("main").getByRole("heading", { name: heading, exact: true }).first()).toBeVisible();
  }
});

test("the sidebar offers Studio's pages only to Studio", async ({ page, request }) => {
  const studio = (await editionOf(request)) === "Pravrudhi Studio";
  await page.goto("/settings");
  await page.locator("main").getByRole("heading", { name: "Settings", exact: true }).waitFor();
  // The sidebar learns the edition from /api/me after mount; give it the same beat the banner gets.
  await page.waitForTimeout(1500);
  const offered = new Set(
    await page.locator("nav a[href]").evaluateAll((links) => links.map((a) => new URL((a as HTMLAnchorElement).href).pathname)),
  );
  const leaked = STUDIO_ONLY.filter((p) => offered.has(p));
  if (studio) expect(leaked.length, "Studio must keep its own pages in the sidebar").toBeGreaterThan(0);
  else expect(leaked, "a product install must not offer Studio's pages").toEqual([]);
});

test("a typed Studio URL on a product install is answered, not broken", async ({ page, request }) => {
  const studio = (await editionOf(request)) === "Pravrudhi Studio";
  test.skip(studio, "Studio serves these pages; the gate is the product's");
  for (const path of ["/machines", "/parity", "/system"]) {
    await page.goto(path);
    await expect(page.getByRole("heading", { name: "Not part of this edition" })).toBeVisible({ timeout: 10_000 });
  }
});

test("Studio's admin routes answer 404 on a product install and JSON on Studio", async ({ request }) => {
  const studio = (await editionOf(request)) === "Pravrudhi Studio";
  const parity = await request.get("/api/parity");
  if (studio) expect([200, 401, 403]).toContain(parity.status());
  else expect(parity.status(), "a product install does not have this surface").toBe(404);
});

test("a product install's pages make no request the engine refuses", async ({ page, request }) => {
  const studio = (await editionOf(request)) === "Pravrudhi Studio";
  test.skip(studio, "Studio may ask for its own surfaces");
  const failed: string[] = [];
  page.on("response", (r) => {
    if (r.status() >= 400) failed.push(`${r.status()} ${new URL(r.url()).pathname}`);
  });
  for (const [path, heading] of EVERY_EDITION) {
    await page.goto(path);
    await expect(page.locator("main").getByRole("heading", { name: heading, exact: true }).first()).toBeVisible();
    await page.waitForTimeout(800);
  }
  // Found 2026-09-11 by the product repository's own e2e: the sidebar asked /api/inbox and /api/requests on every
  // page and settings asked /api/agents, all 404 on a product install, so every product page logged errors.
  expect(failed, "no request a product page makes may be refused by a product engine").toEqual([]);
});
