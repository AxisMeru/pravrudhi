'use strict';
// The sign-in module carries the desktop app's only credentials, and shipped without a test. Its properties are
// the kind that break silently: a session that is never written anywhere, a refresh that cannot resurrect a
// signed-out session, and errors that say what went wrong without repeating what the user typed.
const test = require('node:test');
const assert = require('node:assert/strict');
const {createAuth} = require('../lib/auth');

const SESSION = {access_token: 'at-1', refresh_token: 'rt-1', expires_in: 3600, user: {id: 'u-1', email: 'a@b.c'}};
const CONFIG = {url: 'https://project.supabase.co', key: 'anon-key'};

function fetcher(responses) {
  const calls = [];
  const queue = [...responses];
  const fetchFn = async (url, options) => {
    calls.push({url, options, body: JSON.parse(options.body)});
    const next = queue.shift();
    if (next instanceof Error) throw next;
    return {ok: next.ok !== false, json: async () => next.body ?? next};
  };
  return {fetchFn, calls};
}

test('an unconfigured launch environment says so rather than attempting a request', async () => {
  const {fetchFn, calls} = fetcher([SESSION]);
  const auth = createAuth({url: 'http://project.supabase.co', key: 'anon-key', fetchFn});
  assert.equal(auth.status().configured, false, 'plain http is not a place to send a password');
  await assert.rejects(auth.signIn({email: 'a@b.c', password: 'pw'}), /SUPABASE_URL/);
  assert.equal(calls.length, 0);
  assert.equal(createAuth({...CONFIG, key: '', fetchFn}).status().configured, false);
});

test('signing in stores the session in memory and nowhere a caller can read it back', async () => {
  const {fetchFn, calls} = fetcher([SESSION]);
  const auth = createAuth({...CONFIG, fetchFn});

  const result = await auth.signIn({email: 'a@b.c', password: 'pw'});

  assert.deepEqual(result, {user: {id: 'u-1', email: 'a@b.c'}});
  assert.deepEqual(auth.status(), {configured: true, user: {id: 'u-1', email: 'a@b.c'}});
  assert.equal(await auth.token(), 'at-1');
  // The tokens reach the caller only through `token()`; nothing on the returned object exposes the refresh token.
  assert.equal(JSON.stringify(auth.status()).includes('rt-1'), false);
  assert.deepEqual(Object.keys(auth).sort(),
    ['beginBrowserSignIn', 'completeBrowserSignIn', 'restore', 'signIn', 'signOut', 'status', 'token']);
  assert.equal(calls[0].url, 'https://project.supabase.co/auth/v1/token?grant_type=password');
});

test('a failed sign-in does not repeat the password back, whatever the server said', async () => {
  const {fetchFn} = fetcher([{ok: false, body: {msg: 'Invalid login credentials'}}]);
  const auth = createAuth({...CONFIG, fetchFn});

  await assert.rejects(auth.signIn({email: 'a@b.c', password: 'hunter2'}), (error) => {
    assert.match(error.message, /Sign-in failed/);
    assert.equal(error.message.includes('hunter2'), false, 'the password reached an error message');
    return true;
  });
  assert.equal(auth.status().user, null);
  assert.equal(await auth.token(), null);
});

test('a malformed response is treated as a failure rather than a session', async () => {
  for (const body of [{access_token: 'at-1'}, {access_token: 'at-1', refresh_token: 'rt-1'}, {}]) {
    const {fetchFn} = fetcher([{body}]);
    const auth = createAuth({...CONFIG, fetchFn});
    await assert.rejects(auth.signIn({email: 'a@b.c', password: 'pw'}), /Sign-in failed/);
    assert.equal(auth.status().user, null);
  }
});

test('input that is not an email and a password is refused before any request', async () => {
  const {fetchFn, calls} = fetcher([SESSION]);
  const auth = createAuth({...CONFIG, fetchFn});
  for (const input of [undefined, {}, {email: 'a@b.c'}, {email: 1, password: 'pw'},
    {email: 'a'.repeat(321), password: 'pw'}, {email: 'a@b.c', password: 'p'.repeat(4097)}]) {
    await assert.rejects(auth.signIn(input), /Enter your email and password/);
  }
  assert.equal(calls.length, 0, 'an unusable credential was still sent to the server');
});

test('an expiring token is refreshed once, however many callers ask at the same time', async () => {
  const {fetchFn, calls} = fetcher([SESSION, {body: {...SESSION, access_token: 'at-2', refresh_token: 'rt-2'}}]);
  let clock = 0;
  const auth = createAuth({...CONFIG, fetchFn, now: () => clock});
  await auth.signIn({email: 'a@b.c', password: 'pw'});

  clock = 3_600_000;  // inside the sixty-second margin before expiry
  const tokens = await Promise.all([auth.token(), auth.token(), auth.token()]);

  assert.deepEqual(tokens, ['at-2', 'at-2', 'at-2']);
  assert.equal(calls.length, 2, 'three concurrent callers caused more than one refresh');
  assert.equal(calls[1].body.refresh_token, 'rt-1');
});

test('a refresh that fails clears the session rather than leaving a stale token usable', async () => {
  const {fetchFn} = fetcher([SESSION, {ok: false}]);
  let clock = 0;
  const auth = createAuth({...CONFIG, fetchFn, now: () => clock});
  await auth.signIn({email: 'a@b.c', password: 'pw'});

  clock = 3_600_000;
  await assert.rejects(auth.token(), /Sign-in failed or expired/);
  assert.equal(auth.status().user, null);
  assert.equal(await auth.token(), null);
});

test('signing out during a refresh does not let the refresh sign the user back in', async () => {
  let release;
  const pending = new Promise((resolve) => { release = resolve; });
  let call = 0;
  const fetchFn = async () => {
    if (call++ === 0) return {ok: true, json: async () => SESSION};
    await pending;
    return {ok: true, json: async () => ({...SESSION, access_token: 'at-2'})};
  };
  let clock = 0;
  const auth = createAuth({...CONFIG, fetchFn, now: () => clock});
  await auth.signIn({email: 'a@b.c', password: 'pw'});

  clock = 3_600_000;
  const inFlight = auth.token();
  auth.signOut();
  release();
  await inFlight;

  assert.equal(auth.status().user, null, 'a refresh in flight restored a session the user ended');
  assert.equal(await auth.token(), null);
});

test('a second sign-in started while the first is in flight wins', async () => {
  let release;
  const pending = new Promise((resolve) => { release = resolve; });
  let call = 0;
  const fetchFn = async () => {
    if (call++ === 0) { await pending; return {ok: true, json: async () => SESSION}; }
    return {ok: true, json: async () => ({...SESSION, user: {id: 'u-2', email: 'second@b.c'}})};
  };
  const auth = createAuth({...CONFIG, fetchFn});

  const first = auth.signIn({email: 'a@b.c', password: 'pw'});
  const second = await auth.signIn({email: 'second@b.c', password: 'pw'});
  release();

  await assert.rejects(first, /cancelled/);
  assert.equal(second.user.id, 'u-2');
  assert.equal(auth.status().user.id, 'u-2', 'the abandoned sign-in overwrote the session that replaced it');
});

// The password grant used to be the only door in. A user who never types a password into this application opens
// a system browser instead: Supabase's authorization-code + PKCE flow, with the redirect handled by main.js.
test('beginning a browser sign-in produces a Supabase authorize URL carrying a PKCE challenge, not the verifier', async () => {
  let call = 0;
  const randomBytes = (n) => Buffer.alloc(n, ++call);  // deterministic but distinct per call (verifier, then state)
  const auth = createAuth({...CONFIG, randomBytes, redirectUri: 'pravrudhi-desktop://oauth-callback'});

  const {url} = await auth.beginBrowserSignIn('google');
  const parsed = new URL(url);

  assert.equal(parsed.origin, 'https://project.supabase.co');
  assert.equal(parsed.pathname, '/auth/v1/authorize');
  assert.equal(parsed.searchParams.get('provider'), 'google');
  assert.equal(parsed.searchParams.get('redirect_to'), 'pravrudhi-desktop://oauth-callback');
  assert.equal(parsed.searchParams.get('code_challenge_method'), 's256');
  assert.ok(parsed.searchParams.get('code_challenge').length > 0);
  assert.notEqual(parsed.searchParams.get('code_challenge'), Buffer.alloc(32, 1).toString('base64url'),
    'the raw verifier bytes must never appear as the challenge');
  assert.ok(parsed.searchParams.get('state').length > 0);
});

test('a browser sign-in requires SUPABASE_URL/KEY and a configured redirect URI before opening anything', async () => {
  await assert.rejects(createAuth({url: 'http://project.supabase.co', key: 'anon-key'}).beginBrowserSignIn(), /SUPABASE_URL/);
  await assert.rejects(createAuth({...CONFIG}).beginBrowserSignIn(), /redirect/);
});

test('completing a browser sign-in exchanges the code and verifier it started with, over grant_type=pkce', async () => {
  const {fetchFn, calls} = fetcher([SESSION]);
  let call = 0;
  const randomBytes = (n) => Buffer.alloc(n, ++call);
  const auth = createAuth({...CONFIG, fetchFn, randomBytes, redirectUri: 'pravrudhi-desktop://oauth-callback'});
  const {url} = await auth.beginBrowserSignIn();
  const state = new URL(url).searchParams.get('state');

  const result = await auth.completeBrowserSignIn(`pravrudhi-desktop://oauth-callback?code=auth-code-1&state=${state}`);

  assert.deepEqual(result, {user: {id: 'u-1', email: 'a@b.c'}});
  assert.equal(calls[0].url, 'https://project.supabase.co/auth/v1/token?grant_type=pkce');
  assert.equal(calls[0].body.auth_code, 'auth-code-1');
  assert.equal(typeof calls[0].body.code_verifier, 'string');
  assert.ok(calls[0].body.code_verifier.length >= 43, 'PKCE verifiers must be at least 43 characters (RFC 7636)');
});

test('a callback with the wrong state, no pending flow, or no code is refused without ever calling the server', async () => {
  const {fetchFn, calls} = fetcher([SESSION, SESSION, SESSION]);
  const auth = createAuth({...CONFIG, fetchFn, redirectUri: 'pravrudhi-desktop://oauth-callback'});

  await assert.rejects(auth.completeBrowserSignIn('pravrudhi-desktop://oauth-callback?code=x&state=y'), /Sign-in failed/,
    'no browser sign-in had been started');

  const {url} = await auth.beginBrowserSignIn();
  const state = new URL(url).searchParams.get('state');
  await assert.rejects(auth.completeBrowserSignIn(`pravrudhi-desktop://oauth-callback?code=x&state=${state}wrong`), /Sign-in failed/);
  assert.equal(calls.length, 0, 'a forged or stale redirect reached the token endpoint');
});

test('every session change is handed to onSession so a caller can persist and clear it, but never the access token', async () => {
  const {fetchFn} = fetcher([SESSION, {ok: false}]);
  const seen = [];
  let clock = 0;
  const auth = createAuth({...CONFIG, fetchFn, now: () => clock, onSession: (v) => seen.push(v)});

  await auth.signIn({email: 'a@b.c', password: 'pw'});
  assert.deepEqual(seen, [{refresh: 'rt-1', user: {id: 'u-1', email: 'a@b.c'}}]);

  clock = 3_600_000;
  await assert.rejects(auth.token(), /Sign-in failed or expired/);
  assert.deepEqual(seen[1], null, 'a failed refresh must clear whatever was persisted');
});

test('a session restored from a previous launch is unusable until it proves itself with a refresh', async () => {
  const {fetchFn, calls} = fetcher([{body: {...SESSION, access_token: 'at-2', refresh_token: 'rt-2'}}]);
  let clock = 0;
  const auth = createAuth({...CONFIG, fetchFn, now: () => clock});

  auth.restore({refresh: 'rt-1', user: {id: 'u-1', email: 'a@b.c'}});
  assert.deepEqual(auth.status(), {configured: true, user: {id: 'u-1', email: 'a@b.c'}}, 'restore should show who was signed in immediately');

  assert.equal(await auth.token(), 'at-2', 'the restored refresh token should silently mint a fresh access token');
  assert.equal(calls[0].url, 'https://project.supabase.co/auth/v1/token?grant_type=refresh_token');
  assert.equal(calls[0].body.refresh_token, 'rt-1');
});

test('restoring garbage is ignored rather than crashing the launch', () => {
  const auth = createAuth({...CONFIG});
  for (const bad of [null, undefined, {}, {refresh: 'rt-1'}, {user: {id: 'u-1'}}, {refresh: 1, user: {id: 'u-1'}}]) {
    auth.restore(bad);
    assert.equal(auth.status().user, null);
  }
});
