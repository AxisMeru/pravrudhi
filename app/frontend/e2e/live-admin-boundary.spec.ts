import { expect, test } from "@playwright/test";

/**
 * The nightly's Studio half: proves PRAVRUDHI_ADMINS actually holds on the real, live engine
 * (deploy/gateway/README.md), not just in src/pravrudhi/api/roles.py's own unit tests. E2E_EMAIL/E2E_PASSWORD
 * name the same account pravrudhi-app's live-chromium project signs in with — a real, ordinary Supabase user,
 * deliberately not on PRAVRUDHI_ADMINS. Every admin-only route (roles.py's ADMIN_ONLY set) must refuse this
 * account with 403; a route this account is meant to reach must still answer normally, so the boundary being
 * checked is precise, not "the account can't do anything at all."
 */

const E2E_EMAIL = process.env.E2E_EMAIL;
const E2E_PASSWORD = process.env.E2E_PASSWORD;

test.beforeAll(() => {
  if (!E2E_EMAIL || !E2E_PASSWORD) {
    throw new Error("E2E_EMAIL and E2E_PASSWORD must both be set (see ~/.config/pravrudhi/e2e.env).");
  }
});

async function signIn(page: import("@playwright/test").Page) {
  await page.goto("/signin");
  await page.getByLabel("Email", { exact: true }).fill(E2E_EMAIL!);
  await page.getByLabel("Password", { exact: true }).fill(E2E_PASSWORD!);
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await page.waitForURL((url) => url.pathname === "/", { timeout: 20_000 });
  await expect(page.getByText(E2E_EMAIL!, { exact: true })).toBeVisible();
}

// One representative admin-only route per page — roles.py's ADMIN_ONLY set is the full list; this samples
// across the surfaces Studio's own nav actually links to, rather than re-deriving that whole set here.
const ADMIN_ONLY_PAGES: readonly { path: string; apiPathSubstring: string }[] = [
  { path: "/candidates", apiPathSubstring: "/api/candidates" },
  { path: "/swarm", apiPathSubstring: "/api/swarm" },
  { path: "/inbox", apiPathSubstring: "/api/inbox" },
  { path: "/requests", apiPathSubstring: "/api/requests" },
  { path: "/heartbeat", apiPathSubstring: "/api/heartbeat" },
  { path: "/trace", apiPathSubstring: "/api/agent-trace" },
  { path: "/appetite", apiPathSubstring: "/api/appetite" },
  { path: "/parity", apiPathSubstring: "/api/parity" },
  { path: "/search", apiPathSubstring: "/api/search" },
  { path: "/machines", apiPathSubstring: "/api/svasthya" },
];

test("every admin-only page refuses this non-admin account with 403, on the real engine", async ({ page }) => {
  await signIn(page);
  const wrongStatus: string[] = [];
  for (const { path, apiPathSubstring } of ADMIN_ONLY_PAGES) {
    const response = page.waitForResponse((r) => r.url().includes(apiPathSubstring), { timeout: 20_000 });
    await page.goto(path);
    const status = (await response).status();
    if (status !== 403) wrongStatus.push(`${path} (${apiPathSubstring}) answered ${status}, not 403`);
  }
  expect(wrongStatus, "every admin-only route must refuse this non-admin account with 403").toEqual([]);
});

test("a user-facing route still answers this same account normally, not 403", async ({ page }) => {
  // /api/me: identity alone, no workspace to name (roles.py's own USER_FACING set has several routes that do
  // require one — /api/objectives among them — which a signed-in real account with no workspace selected yet
  // legitimately gets refused with 400 for, a separate and correct behaviour this test is not about).
  //
  // signIn() itself already provokes an /api/me call before the session exists (RequireIdentity's blanket 401
  // for any unauthenticated /api call, Sidebar's own edition() check) — waiting for a fresh one only *after*
  // signIn() has completed and asserted a real session is what proves this account specifically, not just that
  // /api/me exists.
  await signIn(page);
  const response = page.waitForResponse((r) => r.url().includes("/api/me"), { timeout: 20_000 });
  await page.reload();
  expect((await response).status(), "a user-facing route must not be caught by the admin-only boundary").toBe(200);
});
