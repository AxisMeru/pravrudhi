import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { test } from "node:test";

// Every engine call carries the signed-in session (ADR-0051 addendum 3). A page module that calls fetch() on the
// engine base itself sends no token, and on a hosted engine that is "could not reach" for the page it serves —
// which is exactly how the first signed-in operator found the Requests page on 2026-09-12. engineFetch in api.ts
// is the one place the token, the renewal and the 401 handling live; nothing else may fetch the engine.
test("no module fetches the engine outside engineFetch", () => {
  const dir = __dirname;
  const offenders: string[] = [];
  for (const name of readdirSync(dir)) {
    if (!name.endsWith(".ts") || name.endsWith(".spec.ts")) continue;
    const text = readFileSync(join(dir, name), "utf8");
    for (const [i, line] of text.split("\n").entries()) {
      const bare = /(?<![A-Za-z_])fetch\(`\$\{(apiBase|detectBase)\(\)\}/.test(line);
      if (bare) offenders.push(`${name}:${i + 1}`);
    }
  }
  assert.deepEqual(offenders, [], `bare engine fetches: ${offenders.join(", ")}`);
});
