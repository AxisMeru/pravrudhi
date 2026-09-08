'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const {createUpdateOffer} = require('../lib/updates');
const {restartForUpdate} = require('../lib/lifecycle');

function fakeApi(responses) {
  const calls = [];
  let i = 0;
  return {
    calls,
    update: async () => {
      calls.push('update');
      const next = responses[Math.min(i, responses.length - 1)];
      i++;
      if (next instanceof Error) throw next;
      return next;
    },
  };
}
const NONE = {current: {version: '1.0.0'}, latest: null, update_available: false, how: ''};
const AVAILABLE = tag => ({current: {version: '1.0.0'}, latest: {tag, url: `https://example.com/${tag}`}, update_available: true, how: ''});

test('no release waiting leaves the offer at none and calls nothing but update()', async () => {
  const api = fakeApi([NONE]);
  const offer = createUpdateOffer({apiClient: api});
  await offer.check();
  assert.deepEqual(offer.getState(), {status: 'none'});
  assert.deepEqual(api.calls, ['update']);
});

test('a waiting release surfaces its version and notes without installing anything', async () => {
  const api = fakeApi([AVAILABLE('v1.1.0')]);
  const offer = createUpdateOffer({apiClient: api});
  await offer.check();
  assert.deepEqual(offer.getState(), {status: 'available', version: 'v1.1.0', notes: 'https://example.com/v1.1.0'});
});

test('a dismissal survives repeated checks for that version and clears for the next one', async () => {
  const api = fakeApi([AVAILABLE('v1.1.0'), AVAILABLE('v1.1.0'), AVAILABLE('v1.2.0')]);
  const offer = createUpdateOffer({apiClient: api});
  await offer.check();
  assert.equal(offer.getState().status, 'available');
  offer.dismiss();
  assert.deepEqual(offer.getState(), {status: 'none'});
  await offer.check(); // still v1.1.0 — must stay dismissed, not reappear on the very next poll
  assert.deepEqual(offer.getState(), {status: 'none'});
  await offer.check(); // v1.2.0 — a genuinely new release comes back
  assert.equal(offer.getState().status, 'available');
  assert.equal(offer.getState().version, 'v1.2.0');
});

test('dismiss is a no-op unless an offer is actually waiting', async () => {
  const api = fakeApi([NONE]);
  const offer = createUpdateOffer({apiClient: api});
  await offer.check();
  offer.dismiss();
  assert.deepEqual(offer.getState(), {status: 'none'});
});

test('nothing moves past "available" without accept() — polling alone never starts a download', async () => {
  const api = fakeApi([AVAILABLE('v1.1.0'), AVAILABLE('v1.1.0'), AVAILABLE('v1.1.0')]);
  const offer = createUpdateOffer({apiClient: api});
  await offer.check(); await offer.check(); await offer.check();
  assert.equal(offer.getState().status, 'available');
  assert.deepEqual(api.calls, ['update', 'update', 'update']);
  assert.equal('apply' in api, false);
  assert.equal('rollback' in api, false);
});

test('accept() moves to downloading and a later poll reporting the install finished moves to ready', async () => {
  const api = fakeApi([AVAILABLE('v1.1.0'), AVAILABLE('v1.1.0'), NONE]);
  const offer = createUpdateOffer({apiClient: api});
  await offer.check();
  const accepted = offer.accept();
  assert.deepEqual(accepted, {status: 'downloading', version: 'v1.1.0', notes: 'https://example.com/v1.1.0'});
  await offer.check(); // still installing autonomously — offer holds steady
  assert.equal(offer.getState().status, 'downloading');
  await offer.check(); // engine reports no update pending anymore — the install landed
  assert.deepEqual(offer.getState(), {status: 'ready', version: 'v1.1.0', notes: 'https://example.com/v1.1.0'});
});

test('accept() is a no-op without a waiting offer', async () => {
  const api = fakeApi([NONE]);
  const offer = createUpdateOffer({apiClient: api});
  await offer.check();
  assert.deepEqual(offer.accept(), {status: 'none'});
});

test('a download that never lands times out into failed with a reason', async () => {
  let clock = 0;
  const api = fakeApi([AVAILABLE('v1.1.0'), AVAILABLE('v1.1.0'), AVAILABLE('v1.1.0')]);
  const offer = createUpdateOffer({apiClient: api, now: () => clock, downloadTimeoutMs: 1000});
  await offer.check();
  offer.accept();
  clock += 500;
  await offer.check();
  assert.equal(offer.getState().status, 'downloading');
  clock += 600;
  await offer.check();
  assert.equal(offer.getState().status, 'failed');
  assert.match(offer.getState().reason, /did not finish/);
});

test('an accepted update that fails to verify reports the real reason, not a silent nothing', async () => {
  const api = fakeApi([AVAILABLE('v1.1.0'), new Error('network unreachable')]);
  const offer = createUpdateOffer({apiClient: api});
  await offer.check();
  offer.accept();
  await offer.check();
  assert.deepEqual(offer.getState(), {status: 'failed', version: 'v1.1.0', notes: 'https://example.com/v1.1.0', reason: 'network unreachable'});
});

test('a background check that fails while merely offering (not yet accepted) does not scare the user into an error state', async () => {
  const api = fakeApi([AVAILABLE('v1.1.0'), new Error('rate limited')]);
  const offer = createUpdateOffer({apiClient: api});
  await offer.check();
  await offer.check();
  assert.equal(offer.getState().status, 'available'); // last-known-good offer, not a failure banner
});

test('ready and failed are terminal for the user, not silently reset by the next background poll', async () => {
  const readyApi = fakeApi([AVAILABLE('v1.1.0'), NONE, AVAILABLE('v1.1.0')]);
  const readyOffer = createUpdateOffer({apiClient: readyApi});
  await readyOffer.check(); readyOffer.accept(); await readyOffer.check();
  assert.equal(readyOffer.getState().status, 'ready');
  await readyOffer.check();
  assert.equal(readyOffer.getState().status, 'ready');
});

test('concurrent checks share one in-flight request', async () => {
  let resolve;
  const api = {calls: [], update: () => { api.calls.push('update'); return new Promise(r => { resolve = r; }); }};
  const offer = createUpdateOffer({apiClient: api});
  const first = offer.check();
  const second = offer.check();
  resolve(NONE);
  await Promise.all([first, second]);
  assert.deepEqual(api.calls, ['update']);
});

test('start() checks immediately and again on the injected interval; stop() cancels it', async () => {
  const api = fakeApi([NONE]);
  const timers = [];
  let handler;
  const offer = createUpdateOffer({
    apiClient: api,
    setTimer: (fn, ms) => { handler = fn; timers.push(['set', ms]); return 'timer-1'; },
    clearTimer: id => timers.push(['clear', id]),
  });
  offer.start();
  await new Promise(setImmediate); // flush the microtask chain behind the fire-and-forget check() start() kicks off
  assert.deepEqual(api.calls, ['update']);
  offer.start(); // already running — must not double-schedule
  assert.deepEqual(timers, [['set', 6 * 60 * 60 * 1000]]);
  await handler();
  assert.deepEqual(api.calls, ['update', 'update']);
  offer.stop();
  assert.deepEqual(timers, [['set', 6 * 60 * 60 * 1000], ['clear', 'timer-1']]);
  offer.stop(); // idempotent
  assert.equal(timers.length, 2);
});

test('subscribers hear every state change with the current state', async () => {
  const api = fakeApi([AVAILABLE('v1.1.0')]);
  const offer = createUpdateOffer({apiClient: api});
  const seen = [];
  const unsubscribe = offer.subscribe(state => seen.push(state.status));
  await offer.check();
  unsubscribe();
  offer.dismiss();
  assert.deepEqual(seen, ['available']);
});

test('createUpdateOffer refuses to run without an apiClient', () => {
  assert.throws(() => createUpdateOffer({}), /apiClient/);
});

test('restartForUpdate saves state, waits for a clean engine shutdown, then relaunches and exits in order', async () => {
  const order = [];
  const app = {};
  const processOwner = {shutdown: async () => { order.push('shutdown'); }};
  await restartForUpdate(app, processOwner, {
    beforeRestart: async () => order.push('saved'),
    relaunch: a => { assert.equal(a, app); order.push('relaunch'); },
    exit: a => { assert.equal(a, app); order.push('exit'); },
  });
  assert.deepEqual(order, ['saved', 'shutdown', 'relaunch', 'exit']);
});

test('restartForUpdate proceeds even if saving window state fails — losing position must not block a restart', async () => {
  const order = [];
  const processOwner = {shutdown: async () => order.push('shutdown')};
  await restartForUpdate({}, processOwner, {
    beforeRestart: () => { throw new Error('disk full'); },
    relaunch: () => order.push('relaunch'),
    exit: () => order.push('exit'),
  });
  assert.deepEqual(order, ['shutdown', 'relaunch', 'exit']);
});

test('restartForUpdate never relaunches if the engine could not be confirmed stopped — no engine left running', async () => {
  const calls = [];
  const processOwner = {shutdown: async () => { throw new Error('could not terminate engine'); }};
  await assert.rejects(
    restartForUpdate({}, processOwner, {relaunch: () => calls.push('relaunch'), exit: () => calls.push('exit')}),
    /could not terminate engine/
  );
  assert.deepEqual(calls, []);
});

test('restartForUpdate works with its real default relaunch/exit hooks against an app-shaped object', async () => {
  const calls = [];
  const app = {relaunch: () => calls.push('relaunch'), exit: code => calls.push(['exit', code])};
  const processOwner = {shutdown: async () => calls.push('shutdown')};
  await restartForUpdate(app, processOwner);
  assert.deepEqual(calls, ['shutdown', 'relaunch', ['exit', 0]]);
});

// --- a check that could not run is not proof the install finished --------------------------------------------

test('a failed check during a download does not report the update as installed', async () => {
  // `update_available: false` answered two questions: "nothing newer" and "could not reach GitHub". While an
  // install was in flight the second read as the first, and the offer flipped to "ready" — telling the operator
  // a version had been installed because the engine had briefly lost the network.
  const {createUpdateOffer} = require('../lib/updates');
  const answers = [
    {update_available: true, checked: true, latest: {tag: 'v9.9.9'}},
    {update_available: false, checked: false, latest: null},
  ];
  let i = 0;
  const offer = createUpdateOffer({
    apiClient: {update: async () => answers[Math.min(i++, answers.length - 1)]},
    setTimer: () => 0, clearTimer: () => {},
  });
  const seen = [];
  offer.subscribe((s) => seen.push(s.status));

  await offer.check();
  offer.accept();
  await offer.check();

  assert.ok(!seen.includes('ready'), `an unreachable check was treated as a finished install: ${seen.join(' -> ')}`);
});
