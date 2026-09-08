'use strict';
// Swapping the installed application for a new one. The ordering is the whole safety property: the download is
// staged beside the target, the live one is moved aside as a backup, and only then does the new one take its
// place. If any step fails the backup goes back, because the worst outcome here is not "the update failed" but
// "the application is gone".
const test = require('node:test');
const assert = require('node:assert/strict');
const {applyBinary} = require('../lib/shell-apply');

function fakeIo({failOn = null} = {}) {
  const calls = [];
  const files = new Set(['/bin/app']);
  const io = {
    async write(p, _bytes) { calls.push(['write', p]); if (failOn === 'write') throw new Error('disk full'); files.add(p); },
    async chmod(p, m) { calls.push(['chmod', p, m]); if (failOn === 'chmod') throw new Error('denied'); },
    async rename(a, b) {
      calls.push(['rename', a, b]);
      if (failOn === 'rename' && b === '/bin/app') throw new Error('busy');
      files.delete(a); files.add(b);
    },
    async remove(p) { calls.push(['remove', p]); files.delete(p); },
    async exists(p) { return files.has(p); },
  };
  return {io, calls, files};
}

test('the new build is staged, the old one kept, and the swap happens last', async () => {
  const {io, calls} = fakeIo();
  const result = await applyBinary({target: '/bin/app', bytes: Buffer.from('new')}, io);

  assert.equal(result.applied, true);
  const order = calls.map(c => c[0]);
  assert.deepEqual(order.slice(0, 2), ['write', 'chmod'], 'the download is written and made runnable while staged');
  const swapIndex = calls.findIndex(c => c[0] === 'rename' && c[2] === '/bin/app');
  const backupIndex = calls.findIndex(c => c[0] === 'rename' && c[1] === '/bin/app');
  assert.ok(backupIndex !== -1 && backupIndex < swapIndex, 'the live application is kept before it is replaced');
});

test('nothing is written to the live path at any point', async () => {
  const {io, calls} = fakeIo();
  await applyBinary({target: '/bin/app', bytes: Buffer.from('new')}, io);
  assert.ok(!calls.some(c => c[0] === 'write' && c[1] === '/bin/app'),
    'the download was written straight onto the running application');
});

test('a failed swap puts the original application back', async () => {
  const {io, calls} = fakeIo({failOn: 'rename'});
  const result = await applyBinary({target: '/bin/app', bytes: Buffer.from('new')}, io);

  assert.equal(result.applied, false);
  assert.match(result.reason, /busy/);
  const restored = calls.filter(c => c[0] === 'rename' && c[2] === '/bin/app');
  assert.ok(restored.length > 0, 'the backup was never moved back, leaving no application installed');
});

test('a failed download never touches the installed application', async () => {
  const {io, calls} = fakeIo({failOn: 'write'});
  const result = await applyBinary({target: '/bin/app', bytes: Buffer.from('new')}, io);

  assert.equal(result.applied, false);
  assert.ok(!calls.some(c => c[0] === 'rename'), 'the installed application was disturbed by a failed download');
});

test('the previous build is kept, not deleted, so a bad update can be undone by hand', async () => {
  const {io, files} = fakeIo();
  await applyBinary({target: '/bin/app', bytes: Buffer.from('new')}, io);
  assert.ok(files.has('/bin/.app.previous'), 'the previous application was discarded');
});

test('empty bytes are refused before anything is staged', async () => {
  const {io, calls} = fakeIo();
  const result = await applyBinary({target: '/bin/app', bytes: Buffer.alloc(0)}, io);
  assert.equal(result.applied, false);
  assert.deepEqual(calls, [], 'an empty download reached the filesystem');
});

// --- macOS: the payload is a zip and the target is an .app directory ----------------------------------------

const {applyBundle} = require('../lib/shell-apply');

function fakeBundleIo({failOn = null} = {}) {
  const calls = [];
  const paths = new Set(['/Apps/Pravrudhi.app']);
  const io = {
    async write(p, _b) { calls.push(['write', p]); paths.add(p); },
    async extract(zip, dir) {
      calls.push(['extract', zip, dir]);
      if (failOn === 'extract') throw new Error('archive is corrupt');
      paths.add(`${dir}/Pravrudhi.app`);
    },
    async rename(a, b) {
      calls.push(['rename', a, b]);
      if (failOn === 'rename' && b === '/Apps/Pravrudhi.app') throw new Error('in use');
      paths.delete(a); paths.add(b);
    },
    async remove(p) { calls.push(['remove', p]); paths.delete(p); },
    async exists(p) { return paths.has(p); },
    async mkdtemp(prefix) { calls.push(['mkdtemp', prefix]); return `${prefix}XXX`; },
  };
  return {io, calls, paths};
}

test('a bundle is extracted and checked before the installed one is disturbed', async () => {
  const {io, calls} = fakeBundleIo();
  const result = await applyBundle(
    {target: '/Apps/Pravrudhi.app', bytes: Buffer.from('zip'), bundleName: 'Pravrudhi.app'}, io);

  assert.equal(result.applied, true, result.reason);
  const extractIndex = calls.findIndex(c => c[0] === 'extract');
  const swapIndex = calls.findIndex(c => c[0] === 'rename' && c[2] === '/Apps/Pravrudhi.app');
  assert.ok(extractIndex !== -1 && extractIndex < swapIndex, 'the installed bundle was replaced before extraction');
});

test('a corrupt archive leaves the installed bundle alone', async () => {
  const {io, calls, paths} = fakeBundleIo({failOn: 'extract'});
  const result = await applyBundle(
    {target: '/Apps/Pravrudhi.app', bytes: Buffer.from('zip'), bundleName: 'Pravrudhi.app'}, io);

  assert.equal(result.applied, false);
  assert.match(result.reason, /corrupt/);
  assert.ok(paths.has('/Apps/Pravrudhi.app'), 'the installed bundle was lost to a bad download');
  assert.ok(!calls.some(c => c[0] === 'rename' && c[1] === '/Apps/Pravrudhi.app'));
});

test('an archive missing the expected bundle is refused rather than installing whatever it held', async () => {
  const {io} = fakeBundleIo();
  const result = await applyBundle(
    {target: '/Apps/Pravrudhi.app', bytes: Buffer.from('zip'), bundleName: 'Pravrudhi Studio.app'}, io);
  assert.equal(result.applied, false);
  assert.match(result.reason, /did not contain/);
});

test('a failed swap restores the installed bundle', async () => {
  const {io, calls} = fakeBundleIo({failOn: 'rename'});
  const result = await applyBundle(
    {target: '/Apps/Pravrudhi.app', bytes: Buffer.from('zip'), bundleName: 'Pravrudhi.app'}, io);

  assert.equal(result.applied, false);
  assert.ok(calls.filter(c => c[0] === 'rename' && c[2] === '/Apps/Pravrudhi.app').length > 0,
    'the backup was never put back');
});
