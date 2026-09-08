import { strict as assert } from "node:assert";
import test from "node:test";

import { toneFor } from "./trace";

test("each outcome reads differently at a glance", () => {
  const kinds = ["accepted", "rejected", "limited", "fallback"];
  const tones = new Set(kinds.map(toneFor));
  assert.equal(tones.size, kinds.length, "two outcomes share a colour, so a wave cannot be read at a glance");
});

test("an outcome nobody anticipated still renders", () => {
  // A new kind added to the engine must not produce an unstyled or blank row.
  assert.ok(toneFor("something-new").length > 0);
  assert.ok(toneFor("").length > 0);
});
