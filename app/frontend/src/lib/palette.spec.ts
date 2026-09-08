// A product install does not serve the surfaces of Pravrudhi improving itself — the engine refuses them by
// edition rather than by role. The interface has to agree, or it offers doors that answer 404.

import { strict as assert } from "node:assert";
import test from "node:test";

import { PALETTE_PAGES, STUDIO_ONLY_PAGES, pagesFor } from "./palette";

test("Studio keeps every page", () => {
  assert.equal(pagesFor(true).length, PALETTE_PAGES.length);
});

test("the product drops the pages the engine will not serve it", () => {
  const ids = new Set(pagesFor(false).map((p) => p.id));
  for (const hidden of STUDIO_ONLY_PAGES) {
    assert.ok(!ids.has(hidden), `${hidden} is still offered by the product`);
  }
});

test("the product keeps what belongs to its own user", () => {
  // Removing the engine's self-improvement surfaces must not strip the product of its own work.
  const ids = new Set(pagesFor(false).map((p) => p.id));
  for (const kept of ["start", "objectives", "progress", "memory", "chat", "runs", "models", "settings"]) {
    assert.ok(ids.has(kept), `${kept} vanished from the product`);
  }
});

test("every hidden page is a real page", () => {
  // A typo in the hidden set would silently hide nothing at all.
  const all = new Set(PALETTE_PAGES.map((p) => p.id));
  for (const hidden of STUDIO_ONLY_PAGES) {
    assert.ok(all.has(hidden), `${hidden} is not a page, so hiding it does nothing`);
  }
});
