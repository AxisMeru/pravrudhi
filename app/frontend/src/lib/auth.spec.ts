import assert from "node:assert/strict";
import { test } from "node:test";
import { accessToken, clearSession, completeMagicLink, refreshSession, sessionStale, setSession } from "./auth";

// The operator's first hour on the hosted door (2026-09-12): the access token expired, sessionStale() had no
// call site to renew before, and refreshSession() had never been exercised, so every engine call died with a 401
// while the sidebar still showed a signed-in address. These are the three functions that fix that, tested
// against Node's fetch directly (no browser needed for what is pure token bookkeeping).

const URL = "https://example.supabase.co";
const ANON_KEY = "test-anon-key";
const USER = { id: "u1", email: "operator@example.com" };

function withEnv<T>(fn: () => T): T {
  process.env.NEXT_PUBLIC_SUPABASE_URL = URL;
  process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY = ANON_KEY;
  return fn();
}

function mockFetch(handler: (url: string) => Response): () => void {
  const original = globalThis.fetch;
  globalThis.fetch = (async (input: RequestInfo | URL) => handler(String(input))) as typeof fetch;
  return () => { globalThis.fetch = original; };
}

test("sessionStale: no session is not stale (unknown expiry counts as fresh)", () => {
  clearSession();
  assert.equal(sessionStale(), false);
});

test("sessionStale: a grant expiring within the hour is not stale until the last minute", () => {
  clearSession();
  setSession("tok", USER, { expires_in: 3600 });
  assert.equal(sessionStale(), false);
});

test("sessionStale: a token past its expiry is stale", () => {
  clearSession();
  setSession("tok", USER, { expires_in: -120 }); // already 2 minutes past
  assert.equal(sessionStale(), true);
});

test("refreshSession: exchanges the refresh token and stores the new grant", async () => {
  clearSession();
  setSession("old-token", USER, { refresh_token: "r1", expires_in: -120 });
  const restore = mockFetch((url) => {
    assert.match(url, /grant_type=refresh_token/);
    return new Response(JSON.stringify({ access_token: "new-token", user: USER, refresh_token: "r2", expires_in: 3600 }), { status: 200 });
  });
  const ok = await withEnv(refreshSession);
  restore();
  assert.equal(ok, true);
  assert.equal(accessToken(), "new-token");
  assert.equal(sessionStale(), false);
});

test("refreshSession: a refused exchange clears the session rather than leaving a dead one", async () => {
  clearSession();
  setSession("old-token", USER, { refresh_token: "r1", expires_in: -120 });
  const restore = mockFetch(() => new Response(JSON.stringify({ error: "invalid_grant" }), { status: 400 }));
  const ok = await withEnv(refreshSession);
  restore();
  assert.equal(ok, false);
  assert.equal(accessToken(), null, "a failed renewal must clear the session, not leave a stale token behind");
});

test("refreshSession: no refresh token on record means no exchange is attempted", async () => {
  clearSession();
  setSession("old-token", USER, { expires_in: -120 }); // no refresh_token in the grant
  let called = false;
  const restore = mockFetch(() => { called = true; return new Response("{}", { status: 200 }); });
  const ok = await withEnv(refreshSession);
  restore();
  assert.equal(called, false);
  assert.equal(ok, false);
});

test("completeMagicLink: stores the refresh token and expiry carried in the URL fragment", async () => {
  clearSession();
  const globalAsAny = globalThis as unknown as { window?: unknown };
  const originalWindow = globalAsAny.window;
  globalAsAny.window = {
    location: { hash: "#access_token=fresh-token&refresh_token=r3&expires_in=3600", pathname: "/signin" },
    history: { replaceState: () => {} },
  };
  const restore = mockFetch(() => new Response(JSON.stringify(USER), { status: 200 }));
  const result = await withEnv(completeMagicLink);
  restore();
  globalAsAny.window = originalWindow;

  assert.equal(result?.ok, true);
  assert.equal(accessToken(), "fresh-token");
  // The point of the fix: a grant with a refresh token and a real expiry leaves the session refreshable and not
  // immediately stale, rather than the accessToken-only path that expired with no way back.
  assert.equal(sessionStale(), false);
  const restoreRefresh = mockFetch((url) => {
    assert.match(url, /grant_type=refresh_token/);
    return new Response(JSON.stringify({ access_token: "next-token", user: USER, refresh_token: "r4", expires_in: 3600 }), { status: 200 });
  });
  setSession(accessToken() ?? "", USER, { expires_in: -1 }); // force staleness to prove refreshToken carried over
  const refreshed = await withEnv(refreshSession);
  restoreRefresh();
  assert.equal(refreshed, true, "the refresh token captured from the magic link must actually be usable");
});
