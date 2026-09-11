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
  assert.deepEqual(Object.keys(auth).sort(), ['signIn', 'signOut', 'status', 'token']);
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
