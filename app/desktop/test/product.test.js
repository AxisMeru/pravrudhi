'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const {createApiClient} = require('../lib/api');
const {createProviderSurface} = require('../renderer/product');
const response = value => ({ok: true, json: async () => value});

// The engine already draws the spending boundary (tests/test_byok_boundary.py): a signed-in user's
// `/api/providers` calls resolve against their own workspace store, never the engine's. This surface
// only has to call the three routes the engine classifies as the product's (src/pravrudhi/api/roles.py)
// and hand back what each one answered; it carries no provider id or key of its own to guard.
test('surface lists, adds a key that validates, checks its status, then removes it', async () => {
  const calls = [];
  let configured = false;
  const api = createApiClient(() => 'http://127.0.0.1:8008', {fetchFn: async (url, options) => {
    const {pathname} = new URL(url);
    calls.push([options.method, pathname]);
    if (pathname === '/api/app-token') return response({token: 't'});
    if (pathname === '/api/providers' && options.method === 'GET') {
      return response([{id: 'alibaba', title: 'Alibaba (Qwen, Singapore)', configured, key_prefix: 'sk-'}]);
    }
    if (pathname === '/api/providers/alibaba/key' && options.method === 'POST') {
      assert.deepEqual(JSON.parse(options.body), {key: 'sk-mine'});
      configured = true;
      return response({provider: 'alibaba', configured: true, validated: true, reason: ''});
    }
    if (pathname === '/api/providers/alibaba/key' && options.method === 'DELETE') {
      configured = false;
      return response({provider: 'alibaba', configured: false});
    }
    throw new Error(`unexpected ${options.method} ${pathname}`);
  }});
  const surface = createProviderSurface(api);
  assert.equal((await surface.list())[0].configured, false);
  const added = await surface.add('alibaba', 'sk-mine');
  assert.equal(added.validated, true);
  assert.equal((await surface.validate('alibaba')).configured, true);
  assert.equal((await surface.remove('alibaba')).configured, false);
  // Every non-GET call fetches a fresh local app token first (see lib/api.js::request), so add and remove
  // each show up as two: the token fetch, then the write.
  assert.deepEqual(calls.map(c => c[0]), ['GET', 'GET', 'POST', 'GET', 'GET', 'DELETE']);
});

test('adding an empty key is refused before it reaches the engine', async () => {
  const surface = createProviderSurface(createApiClient(() => 'http://127.0.0.1:8008',
    {fetchFn: async () => { throw new Error('must not be called'); }}));
  await assert.rejects(surface.add('alibaba', ''), /key is required/);
});

test('validating an id the engine does not list names it as unknown', async () => {
  const api = createApiClient(() => 'http://127.0.0.1:8008', {fetchFn: async (url, options) => {
    const {pathname} = new URL(url);
    if (pathname === '/api/app-token') return response({token: 't'});
    if (pathname === '/api/providers') return response([]);
    throw new Error('unexpected call');
  }});
  await assert.rejects(createProviderSurface(api).validate('unknown'), /unknown provider/);
});
