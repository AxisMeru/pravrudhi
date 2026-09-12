'use strict';
// Studio and the product are one codebase and two installs, told apart by a file the build puts beside the
// packaged resources. The default direction matters: Studio is the operator's edition and is explicitly not for
// any other user or public release, so anything that cannot say what it is must be the product.
const test = require('node:test');
const assert = require('node:assert/strict');
const {editionOf, readEdition, PRODUCT, STUDIO, engineEnv} = require('../lib/edition');

test('a build that declares studio is Studio', () => {
  assert.equal(editionOf('studio'), STUDIO);
  assert.equal(editionOf('Studio'), STUDIO, 'the declaration is not case-sensitive');
  assert.equal(editionOf(' studio '), STUDIO);
});

test('anything else is the product', () => {
  for (const value of ['product', '', undefined, null, 'stduio', 'admin', 'Studio Tools']) {
    assert.equal(editionOf(value), PRODUCT, `${JSON.stringify(value)} was treated as Studio`);
  }
});

test('the edition is read from the file the build placed beside the resources', () => {
  const files = {'/res/edition.json': '{"edition": "studio"}'};
  assert.equal(readEdition('/res', p => files[p] ?? (() => { throw new Error('ENOENT'); })()), STUDIO);
});

test('running from source, where no such file exists, is the product', () => {
  // resourcesPath points into Electron's own directory then, and nothing is there to read.
  assert.equal(readEdition('/electron', () => { throw new Error('ENOENT'); }), PRODUCT);
});

test('a malformed or empty edition file is the product, not a crash and not Studio', () => {
  assert.equal(readEdition('/res', () => '{not json'), PRODUCT);
  assert.equal(readEdition('/res', () => '{}'), PRODUCT);
  assert.equal(readEdition('/res', () => 'null'), PRODUCT);
});

test('the edition travels to the engine as the variable the engine reads', () => {
  // src/pravrudhi/api/edition.py::EDITION_ENV. Without it the packaged product would call itself Studio
  // whenever it ran with authentication off, because a local caller with nobody to identify is the operator.
  assert.equal(engineEnv({}, STUDIO).PRAVRUDHI_EDITION, 'studio');
  assert.equal(engineEnv({}, PRODUCT).PRAVRUDHI_EDITION, 'product');
});

test('the surrounding environment is carried through, not replaced', () => {
  const env = engineEnv({PATH: '/usr/bin', HOME: '/home/x'}, PRODUCT);
  assert.equal(env.PATH, '/usr/bin');
  assert.equal(env.HOME, '/home/x');
});

test('a build overrides an inherited variable, so one install cannot impersonate the other', () => {
  // Otherwise PRAVRUDHI_EDITION=studio in a user's shell would turn their product install into Studio.
  assert.equal(engineEnv({PRAVRUDHI_EDITION: 'studio'}, PRODUCT).PRAVRUDHI_EDITION, 'product');
});

test('the two editions keep their settings apart', () => {
  // Both builds package the same `name`, so Electron would hand them one userData directory: the same saved
  // workspace, the same window state, each overwriting the other. That is not two installs.
  const {userDataName} = require('../lib/edition');
  assert.notEqual(userDataName(STUDIO), userDataName(PRODUCT));
  assert.equal(userDataName(PRODUCT), 'pravrudhi-desktop', 'the product keeps the directory it already had');
  assert.equal(userDataName(STUDIO), 'pravrudhi-studio');
  assert.equal(userDataName(undefined), 'pravrudhi-desktop', 'an unknown build is the product, here too');
});

// Operator instruction: "the desktop app will not have access to any api/routing for any model provider..they
// have to bring theirs, configure it etc..you have to enable..but for admin it uses the one as the core
// pravrudhi". An unlabelled build is the product edition (`editionOf(undefined) === PRODUCT`, asserted above),
// so this is the edition every released install runs as. Three names mark the operator's own seat, spent by
// the engine on Studio's behalf and never on a product user's: the file the operator's raw Alibaba credential
// lives in (agents/alibaba_agent.py::credential_path, under `~/.config/llm/`), the function that hands a
// `claude` invocation the operator's own account (agents/account.py::claude_env), and the registry name for
// the operator's paid Lite Plan seat (agents/registry.py::"opencode:alibaba-plan"). None is a JavaScript API
// this shell could call directly; what it asserts is that the shell's own source never names them, and that
// its route budget carries no path that could run model work before a user has stored a key of their own.
test('the product path never reaches the operator\'s account key store or alibaba-plan seat', () => {
  const fs = require('node:fs');
  const os = require('node:os');
  const path = require('node:path');
  assert.equal(editionOf(undefined), PRODUCT, 'a released, unlabelled build is the product edition');

  const FORBIDDEN = Object.freeze(['.config/llm', 'claude_env', 'opencode:alibaba-plan']);

  function scan(dir) {
    const hits = [];
    for (const entry of fs.readdirSync(dir, {withFileTypes: true})) {
      if (entry.name === 'test' || entry.name === 'node_modules') continue;
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) { hits.push(...scan(full)); continue; }
      if (!entry.name.endsWith('.js')) continue;
      const source = fs.readFileSync(full, 'utf8');
      for (const needle of FORBIDDEN) if (source.includes(needle)) hits.push(`${full}: ${needle}`);
    }
    return hits;
  }

  // A check that cannot fail is worth nothing: prove `scan` actually catches a planted violation before
  // trusting it to report the real tree clean.
  const fixture = fs.mkdtempSync(path.join(os.tmpdir(), 'edition-boundary-'));
  try {
    const planted = path.join(fixture, 'planted.js');
    fs.writeFileSync(planted, "module.exports = 'opencode:alibaba-plan';");
    assert.deepEqual(scan(fixture), [`${planted}: opencode:alibaba-plan`]);
  } finally {
    fs.rmSync(fixture, {recursive: true, force: true});
  }

  assert.deepEqual(scan(path.join(__dirname, '..')), []);

  // The other half of "you have to enable": the route budget itself carries no path that could run model
  // work, and does carry the one that lets a user store a key of their own.
  const {ROUTES} = require('../lib/api');
  const templates = Object.values(ROUTES).map(([, template]) => template);
  assert.ok(!templates.some(t => t.includes('chat')),
    'the product route budget must not carry a path that could run model work before a user has a key stored');
  assert.ok(templates.includes('/api/providers/:id/key'),
    'bringing and configuring a user\'s own key is the route that enables model work at all');
});
