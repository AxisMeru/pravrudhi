import { expect, test } from "@playwright/test";

/**
 * The BYOK provider-key surface on /settings. Before this test existed, the frontend's `ProviderKeyResult` type
 * declared `{ok, reason}` -- a shape the engine never sent. `set_provider_key` (src/pravrudhi/api/server.py)
 * returns `{provider, configured: true, validated: bool, reason: str}`, so `result.ok` was always `undefined`,
 * and EVERY successful key save rendered as a failure -- with the probe's own success marker (`reason: "ok"`)
 * shown as the literal error text. `delete_provider_key` never had a `reason` field at all, so every successful
 * removal showed "remove failed". pravrudhi-app 3673b63 found and fixed the identical defect in the product
 * edition first; this ports the same fix and the same test to Studio.
 *
 * "openai-compatible" (credentials.py's PROVIDERS) has an empty `base_url` and the frontend has no field to
 * supply one, so validation for it deterministically fails with a real, specific reason ("no base_url
 * configured for this provider; pass one explicitly") -- no network call, no external credential, and no
 * mocking: a real request against the real running engine's real validation logic.
 *
 * Run against a live engine of either edition (this repository's own local-engine project convention):
 *   LOCAL_ENGINE_URL=http://127.0.0.1:8300 npx playwright test --project local-engine-chromium settings-byok
 */

const PROVIDER_ROW_LABEL = "OpenAI-compatible endpoint";

test.describe("BYOK provider keys", () => {
  test("a key that fails real validation still shows as configured, with the real reason -- not the string 'ok'", async ({
    page,
  }) => {
    await page.goto("/settings");
    const row = page.locator("tr", { has: page.getByText(PROVIDER_ROW_LABEL, { exact: true }) });
    await row.getByPlaceholder(/key/i).fill("sk-test-key-that-will-not-validate");
    await row.getByRole("button", { name: "Save" }).click();

    // The real, specific reason from credentials.validate(), never the bare marker "ok" a stale type once let
    // through, and never the generic fallback "key rejected" the old `if (result.ok)` branch always took.
    await expect(row.getByText(/no base_url configured for this provider/i)).toBeVisible({ timeout: 15_000 });
    await expect(row.getByText("ok", { exact: true })).toHaveCount(0);
    // The engine stores the key regardless of whether it validated (server.py: "store.put runs
    // unconditionally") -- the UI must say so truthfully rather than hiding a real stored state.
    await expect(row.getByText("configured", { exact: true })).toBeVisible();
  });

  test("removing a configured key never shows 'remove failed'", async ({ page }) => {
    await page.goto("/settings");
    const row = page.locator("tr", { has: page.getByText(PROVIDER_ROW_LABEL, { exact: true }) });
    // Leaves a key configured from the previous test's run (a fresh workspace has none) -- configure one here
    // too, so this test is independent of run order.
    await row.getByPlaceholder(/key/i).fill("sk-test-key-for-removal");
    await row.getByRole("button", { name: "Save" }).click();
    await expect(row.getByText("configured", { exact: true })).toBeVisible({ timeout: 15_000 });

    await row.getByRole("button", { name: "Remove" }).click();

    await expect(row.getByText("not configured", { exact: true })).toBeVisible({ timeout: 15_000 });
    await expect(row.getByText(/remove failed/i)).toHaveCount(0);
  });
});
