import assert from "node:assert/strict";
import { test } from "node:test";
import { ApiError } from "./api";
import { heartbeat } from "./heartbeat";

// heartbeat() used to catch every failure — a 500, a network error, anything — and return [], indistinguishable
// from "the engine genuinely has no heartbeats yet." The Heartbeat page trusted that and showed "No heartbeats
// recorded yet" during a real outage (2026-09-12), the same shape of bug the CORS/auth fixes closed for other
// pages. A caller must be able to tell "empty" from "failed."

function mockFetch(status: number, body: unknown): () => void {
  const original = globalThis.fetch;
  globalThis.fetch = (async () => new Response(JSON.stringify(body), { status })) as typeof fetch;
  return () => { globalThis.fetch = original; };
}

test("heartbeat: a genuinely empty log resolves to an empty array, not a rejection", async () => {
  const restore = mockFetch(200, { beats: [] });
  const rows = await heartbeat(100);
  restore();
  assert.deepEqual(rows, []);
});

test("heartbeat: a server error rejects rather than silently returning an empty array", async () => {
  const restore = mockFetch(500, { error: "boom" });
  await assert.rejects(() => heartbeat(100), ApiError);
  restore();
});
