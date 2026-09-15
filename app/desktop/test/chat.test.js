'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const {createApiClient, ROUTES, CHAT_ROUTE} = require('../lib/api');
const {createChatSurface} = require('../lib/chat');

// Studio's own conversational surface: the operator's one route into the engine's chat turn, kept out of
// `lib/api.js`'s `ROUTES` (test/edition.test.js proves that budget stays chat-free) and merged in only by
// whoever assembles the Studio client. This exercises the merge and the surface the same way
// test/product.test.js exercises the bring-your-own-key surface.
test('CHAT_ROUTE is not part of the shared route budget', () => {
  assert.equal(ROUTES.chat, undefined);
  assert.deepEqual(CHAT_ROUTE.chat, ['POST', '/api/chat']);
});

test('surface sends a message, starts a thread, then continues it', async () => {
  const calls = [];
  const response = value => ({ok: true, json: async () => value});
  const api = createApiClient(() => 'http://127.0.0.1:8008', {
    routes: {...ROUTES, ...CHAT_ROUTE},
    fetchFn: async (url, options) => {
      const {pathname} = new URL(url);
      calls.push([options.method, pathname, options.body ? JSON.parse(options.body) : undefined]);
      if (pathname === '/api/app-token') return response({token: 't'});
      if (pathname === '/api/chat') {
        return response({thread_id: 'th1', reply: 'hello back', citations: [], tool_calls: [], refusals: []});
      }
      throw new Error(`unexpected ${options.method} ${pathname}`);
    },
  });
  const surface = createChatSurface(api);
  const first = await surface.send('hello there');
  assert.equal(first.reply, 'hello back');
  assert.deepEqual(calls.find(c => c[1] === '/api/chat')[2], {message: 'hello there'});
  await surface.send('again', 'th1');
  assert.deepEqual(calls.filter(c => c[1] === '/api/chat')[1][2], {message: 'again', thread_id: 'th1'});
});

test('sending an empty or blank message is refused before it reaches the engine', async () => {
  const surface = createChatSurface(createApiClient(() => 'http://127.0.0.1:8008',
    {routes: {...ROUTES, ...CHAT_ROUTE}, fetchFn: async () => { throw new Error('must not be called'); }}));
  await assert.rejects(surface.send(''), /message is required/);
  await assert.rejects(surface.send('   '), /message is required/);
});
