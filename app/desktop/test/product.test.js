'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const {createProduct} = require('../lib/product');

function makeApi(overrides = {}) {
  return {
    me: async () => ({authenticated: true, id: 'user-1'}),
    workspaces: async () => ({workspaces: [{slug: 'mine', path: '/ws/mine'}]}),
    createWorkspace: async ({slug}) => ({slug}),
    objectives: async () => ({objectives: [], problems: []}),
    createObjective: async () => ({}),
    ...overrides,
  };
}
function makeAuth(user = {id: 'user-1', email: 'user@example.com'}) {
  return {status: () => ({configured: true, user})};
}

test('every product call is refused before a signed-in session exists', async () => {
  const product = createProduct({api: makeApi(), auth: makeAuth(null), selectWorkspace: async () => {}});
  await assert.rejects(product.workspaces(), /Sign in/);
  await assert.rejects(product.createWorkspace('mine'), /Sign in/);
  await assert.rejects(product.choose('mine'), /Sign in/);
  await assert.rejects(product.artifacts(), /Sign in/);
  await assert.rejects(product.create({id: 'a', intent: 'i', location: 'l', metric: 'm', direction: 'up'}), /Sign in/);
  await assert.rejects(product.chooseBand('a', 'one_time'), /Sign in/);
});

test('a local session the engine does not recognise is refused, not trusted', async () => {
  const product = createProduct({
    api: makeApi({me: async () => ({authenticated: false, id: null})}),
    auth: makeAuth(), selectWorkspace: async () => {},
  });
  await assert.rejects(product.workspaces(), /Supabase identity/);
});

test('workspaces project only the slug, dropping the filesystem path', async () => {
  const product = createProduct({
    api: makeApi({workspaces: async () => ({workspaces: [{slug: 'mine', path: '/secret/path'}]})}),
    auth: makeAuth(), selectWorkspace: async () => {},
  });
  assert.deepEqual(await product.workspaces(), [{slug: 'mine'}]);
});

test('createWorkspace validates the slug before ever calling the engine', async () => {
  let called = false;
  const product = createProduct({
    // The client takes {body} now, since a route may also carry an id or a workspace.
    api: makeApi({createWorkspace: async ({body}) => { called = true; return {slug: body.slug}; }}),
    auth: makeAuth(), selectWorkspace: async () => {},
  });
  await assert.rejects(product.createWorkspace('Not A Slug'), /lowercase/);
  assert.equal(called, false);
  assert.deepEqual(await product.createWorkspace('new-ws'), {slug: 'new-ws'});
  assert.equal(called, true);
});

test('choosing an unknown workspace is refused; a known one is opened and selected', async () => {
  let opened;
  const product = createProduct({api: makeApi(), auth: makeAuth(), selectWorkspace: async path => { opened = path; }});
  await assert.rejects(product.choose('nope'), /Choose one of your workspaces/);
  assert.deepEqual(await product.choose('mine'), {slug: 'mine'});
  assert.equal(opened, '/ws/mine');
});

test('artifacts and starting something new both require a chosen workspace first', async () => {
  const product = createProduct({api: makeApi(), auth: makeAuth(), selectWorkspace: async () => {}});
  await assert.rejects(product.artifacts(), /Choose a workspace first/);
  await assert.rejects(product.create({id: 'a', intent: 'i', location: 'l', metric: 'm', direction: 'up'}), /Choose a workspace first/);
});

test('artifacts project id, intent, location and the real state of every benchmark', async () => {
  const product = createProduct({
    api: makeApi({objectives: async () => ({
      objectives: [{id: 'a1', intent: 'Ship it', notes: 'github.com/x/y', internal: 'do-not-leak',
        progress: [{benchmark: 'gsm8k', state: 'running', baseline: {value: 10}, latest: {value: 12}, delta: 2}]}],
      problems: ['p1', 'p2'],
    })}),
    auth: makeAuth(), selectWorkspace: async () => {},
  });
  await product.choose('mine');
  assert.deepEqual(await product.artifacts(), {
    workspace: 'mine',
    artifacts: [{id: 'a1', intent: 'Ship it', location: 'github.com/x/y',
      progress: [{benchmark: 'gsm8k', state: 'running', baseline: 10, latest: 12, delta: 2}]}],
    problems: 2,
  });
});

test('starting something new validates its fields and refuses a name already in use', async () => {
  const product = createProduct({
    api: makeApi({objectives: async () => ({objectives: [{id: 'exists'}], problems: []})}),
    auth: makeAuth(), selectWorkspace: async () => {},
  });
  await product.choose('mine');
  await assert.rejects(product.create({id: 'aa', intent: '', location: 'l', metric: 'm', direction: 'up'}), /Supply a name/);
  await assert.rejects(product.create({id: 'aa', intent: 'i', location: 'l', metric: 'm', direction: 'sideways'}), /metric direction/);
  await assert.rejects(product.create({id: 'exists', intent: 'i', location: 'l', metric: 'm', direction: 'up'}), /already in use/);
  assert.deepEqual(await product.create({id: 'new-one', intent: 'i', location: 'l', metric: 'm', direction: 'up'}), {id: 'new-one'});
});

test('bandLevels renders the band policy in the user\'s terms, matching the four documented levels', async () => {
  const product = createProduct({api: makeApi(), auth: makeAuth(), selectWorkspace: async () => {}});
  const levels = product.bandLevels();
  assert.deepEqual(levels.map(l => l.id), ['one_time', 'critical', 'self_healing', 'continuous']);
  assert.deepEqual(levels.map(l => l.spendCeilingUsd), [10, 50, 150, 500]);
  for (const level of levels) {
    assert.equal(typeof level.mayActWithoutAsking, 'string');
    assert.equal(typeof level.mustAskFirst, 'string');
    assert.equal(typeof level.runFrequency, 'string');
  }
  // A fresh copy every call: a caller mutating what it got back cannot corrupt the shared policy.
  levels[0].name = 'tampered';
  assert.notEqual(product.bandLevels()[0].name, 'tampered');
});

test('chooseBand requires a chosen workspace, a real artifact id and an offered level; bandFor reads it back', async () => {
  const product = createProduct({api: makeApi(), auth: makeAuth(), selectWorkspace: async () => {}});
  await assert.rejects(product.chooseBand('a1', 'one_time'), /Choose a workspace first/);
  await product.choose('mine');
  await assert.rejects(product.chooseBand('Not A Slug', 'one_time'), /Choose an artifact/);
  await assert.rejects(product.chooseBand('a1', 'not-a-level'), /Choose one of the offered levels/);
  assert.equal(await product.bandFor('a1'), null);
  assert.deepEqual(await product.chooseBand('a1', 'self_healing'), {id: 'a1', level: 'self_healing'});
  assert.equal(await product.bandFor('a1'), 'self_healing');
});

test('a band choice does not leak across workspaces and does not survive reset', async () => {
  const product = createProduct({
    api: makeApi({workspaces: async () => ({workspaces: [{slug: 'mine', path: '/ws/mine'}, {slug: 'other', path: '/ws/other'}]})}),
    auth: makeAuth(), selectWorkspace: async () => {},
  });
  await product.choose('mine');
  await product.chooseBand('a1', 'critical');
  await product.choose('other');
  assert.equal(await product.bandFor('a1'), null);
  await product.choose('mine');
  assert.equal(await product.bandFor('a1'), 'critical');
  product.reset();
  await assert.rejects(product.artifacts(), /Choose a workspace first/);
  await product.choose('mine');
  assert.equal(await product.bandFor('a1'), null);
});

// --- The renderer half: renderer/product.js runs in the sandboxed window with no `require`, so it is
// exercised the same way integration.test.js exercises renderer/app.js -- loading the real file into a
// bare vm context and driving it through a fake window.desktop.product bridge.
function element() {
  const listeners = {};
  return {
    textContent: '', className: '', value: '', hidden: false, disabled: false, dataset: {}, children: [],
    addEventListener(type, fn) { listeners[type] = fn; },
    fire(type) { return listeners[type] && listeners[type](); },
    replaceChildren(...items) { this.children = items; },
    append(...items) { this.children.push(...items); },
  };
}
function makeDocument() {
  const elements = new Map();
  return {
    getElementById: id => { if (!elements.has(id)) elements.set(id, element()); return elements.get(id); },
    createElement: () => element(),
    _elements: elements,
  };
}
async function settle() { for (let i = 0; i < 6; i++) await new Promise(resolve => setImmediate(resolve)); }
function loadProduct(document, product) {
  const fs = require('node:fs'), vm = require('node:vm');
  vm.runInNewContext(fs.readFileSync(require.resolve('../renderer/product'), 'utf8'), {document, window: {desktop: {product}}});
}

test('with no bridge wired through preload, the renderer says so instead of failing on first click', async () => {
  const fs = require('node:fs'), vm = require('node:vm');
  const document = makeDocument();
  vm.runInNewContext(fs.readFileSync(require.resolve('../renderer/product'), 'utf8'), {document, window: {desktop: {}}});
  assert.match(document.getElementById('product-error').textContent, /not connected/);
});

test('the renderer carries a user from sign-in through workspace, artifacts, a running benchmark, a pending change and a band choice', async () => {
  const document = makeDocument();
  const calls = [];
  let signedIn = false;
  const bridge = {
    authStatus: async () => ({configured: true, user: signedIn ? {id: 'u1', email: 'user@example.com'} : null}),
    signIn: async creds => { calls.push(['signIn', creds]); signedIn = true; },
    signOut: async () => { calls.push(['signOut']); signedIn = false; },
    workspaces: async () => { calls.push(['workspaces']); return [{slug: 'mine'}]; },
    createWorkspace: async slug => calls.push(['createWorkspace', slug]),
    chooseWorkspace: async slug => calls.push(['chooseWorkspace', slug]),
    artifacts: async () => ({workspace: 'mine', artifacts: [{id: 'a1', intent: 'Ship it', location: 'repo', progress: [
      {benchmark: 'gsm8k', state: 'running', baseline: 10, latest: 12, delta: 2},
    ]}]}),
    createArtifact: async input => calls.push(['createArtifact', input]),
    bandLevels: async () => [{id: 'one_time', name: 'One-time build', mayActWithoutAsking: 'Build it.', mustAskFirst: 'None.', spendCeilingUsd: 10, runFrequency: 'Once.'}],
    bandFor: async () => null,
    chooseBand: async (id, level) => calls.push(['chooseBand', id, level]),
  };
  loadProduct(document, bridge);
  await settle();
  assert.equal(document.getElementById('signin-status').textContent, 'Not signed in.');
  assert.equal(document.getElementById('workspace-section').hidden, true);

  document.getElementById('signin-email').value = 'user@example.com';
  document.getElementById('signin-password').value = 'secret';
  await document.getElementById('signin-submit').fire('click');
  await settle();
  // calls[0][1] is a plain object built by code running inside the vm's own realm, so it fails
  // deepStrictEqual's prototype check against an outer-realm literal even when every field matches;
  // comparing the primitive fields sidesteps that without weakening what's actually being checked.
  assert.equal(calls[0][0], 'signIn');
  assert.equal(calls[0][1].email, 'user@example.com');
  assert.equal(calls[0][1].password, 'secret');
  assert.equal(document.getElementById('signin-password').value, '');
  assert.equal(document.getElementById('signin-status').textContent, 'Signed in as user@example.com');
  assert.equal(document.getElementById('workspace-section').hidden, false);
  const workspaceList = document.getElementById('workspace-list');
  assert.equal(workspaceList.children.length, 1);
  assert.equal(workspaceList.children[0].textContent, 'mine');

  await workspaceList.children[0].fire('click');
  await settle();
  assert.ok(calls.some(c => c[0] === 'chooseWorkspace' && c[1] === 'mine'));
  assert.equal(document.getElementById('current-workspace').textContent, 'mine');
  assert.equal(document.getElementById('artifact-section').hidden, false);
  const artifactList = document.getElementById('artifact-list');
  assert.equal(artifactList.children.length, 1);
  const card = artifactList.children[0];
  assert.equal(card.children[0].textContent, 'a1 — Ship it');
  assert.equal(card.children[1].textContent, 'repo');
  const row = card.children[2].children[0];
  assert.equal(row.children[0].textContent, 'gsm8k: running');
  assert.equal(row.children[1].textContent, '10 → 12 (Δ 2)');
  const [keep, discard, diffNote, stop, stopNote] = row.children.slice(2);
  assert.equal(keep.disabled, true); assert.equal(discard.disabled, true);
  assert.match(diffNote.textContent, /Reviewing a change/);
  assert.equal(stop.disabled, true);
  assert.match(stopNote.textContent, /Stopping a run/);
  const band = card.children[3];
  const select = band.children[0], description = band.children[1];
  assert.equal(select.children[0].textContent, 'One-time build');
  assert.equal(select.value, 'one_time');
  assert.equal(description.textContent, 'May act without asking: Build it. Must ask first: None. Spend ceiling: $10. Runs: Once.');

  await select.fire('change');
  await settle();
  assert.ok(calls.some(c => c[0] === 'chooseBand' && c[1] === 'a1' && c[2] === 'one_time'));

  document.getElementById('workspace-slug').value = 'new-ws';
  await document.getElementById('workspace-create').fire('click');
  await settle();
  assert.ok(calls.some(c => c[0] === 'createWorkspace' && c[1] === 'new-ws'));
  assert.equal(document.getElementById('workspace-slug').value, '');

  document.getElementById('artifact-id').value = 'a2';
  document.getElementById('artifact-intent').value = 'Second artifact';
  document.getElementById('artifact-location').value = 'repo2';
  document.getElementById('artifact-metric').value = 'accuracy';
  document.getElementById('artifact-direction').value = 'up';
  await document.getElementById('artifact-create').fire('click');
  await settle();
  assert.ok(calls.some(c => c[0] === 'createArtifact' && c[1].id === 'a2' && c[1].intent === 'Second artifact'
    && c[1].location === 'repo2' && c[1].metric === 'accuracy' && c[1].direction === 'up'));
  assert.equal(document.getElementById('artifact-id').value, '');

  await document.getElementById('signout').fire('click');
  await settle();
  assert.ok(calls.some(c => c[0] === 'signOut'));
  assert.equal(document.getElementById('signin-status').textContent, 'Not signed in.');
  assert.equal(document.getElementById('workspace-section').hidden, true);
  assert.equal(document.getElementById('artifact-section').hidden, true);
});

test('an objective, its runs and stopping one all name the selected workspace', async () => {
  // The engine reads `workspace` to decide whose project a request is about. Omitting it means the operator's
  // own project, which is right for an operator and would be another user's work for anyone else.
  const seen = [];
  const api = makeApi({
    objective: async (o) => { seen.push(['objective', o]); return {id: o.id, intent: 'x', benchmarks: []}; },
    runs: async (o) => { seen.push(['runs', o]); return []; },
    startRun: async (o) => { seen.push(['startRun', o]); return {id: 'r1', status: 'running'}; },
    stopRun: async (o) => { seen.push(['stopRun', o]); return {id: 'r1', status: 'stopping'}; },
  });
  const product = createProduct({api, auth: makeAuth(), selectWorkspace: async () => {}});
  await product.choose('mine');

  await product.objective('prabhasa-nyaya');
  await product.runs();
  await product.startWork({target: 'model'});
  await product.stopWork('r1');

  assert.deepEqual(seen.map(([name]) => name), ['objective', 'runs', 'startRun', 'stopRun']);
  for (const [, options] of seen) assert.equal(options.workspace, 'mine');
  assert.equal(seen[0][1].id, 'prabhasa-nyaya');
  assert.deepEqual(seen[2][1].body, {target: 'model'});
});

test('a run started against a workspace the user has since left is not reported as theirs', async () => {
  let release;
  const gate = new Promise(resolve => { release = resolve; });
  const api = makeApi({startRun: async () => { await gate; return {id: 'r1', status: 'running'}; }});
  const product = createProduct({api, auth: makeAuth(), selectWorkspace: async () => {}});
  await product.choose('mine');

  const pending = product.startWork({target: 'model'});
  product.reset();
  release();

  await assert.rejects(pending, /Workspace changed/);
});
