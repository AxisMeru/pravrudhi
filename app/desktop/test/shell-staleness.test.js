'use strict';
// The engine inside the app updates itself: it checks its channel, verifies a download by checksum, installs
// into a versioned directory and switches. The *shell* — this Electron bundle — does not. Replacing it means
// downloading a new AppImage or .app and running the installer again.
//
// So a shell can sit months behind while its engine stays current, and nothing said so: the version was
// rendered in a status line and never compared to anything. That is how both machines ended up running a build
// whose editions shared one settings directory. This does not install anything; it notices.
const test = require('node:test');
const assert = require('node:assert/strict');
const {shellIsStale} = require('../lib/updates');

test('a shell older than the newest release is stale', () => {
  assert.equal(shellIsStale('0.3.1', 'v0.4.0'), true);
  assert.equal(shellIsStale('0.3.1', 'v0.3.2'), true);
  assert.equal(shellIsStale('0.9.9', 'v1.0.0'), true);
});

test('a shell at or ahead of the newest release is not', () => {
  assert.equal(shellIsStale('0.3.1', 'v0.3.1'), false);
  assert.equal(shellIsStale('0.4.0', 'v0.3.1'), false, 'a development build ahead of the tag is not stale');
  assert.equal(shellIsStale('0.3.10', 'v0.3.9'), false, 'compared as numbers, not as text');
});

test('components are compared numerically, which string order gets wrong', () => {
  // "0.3.9" > "0.3.10" as text, and that is the comparison this must not be.
  assert.equal(shellIsStale('0.3.9', 'v0.3.10'), true);
});

test('anything unparseable is not stale, so a strange tag never nags', () => {
  for (const [shell, tag] of [
    ['0.3.1', null], ['0.3.1', ''], ['0.3.1', 'nightly'], ['', 'v0.4.0'],
    [undefined, 'v0.4.0'], ['0.3.1', undefined], ['0.3.1', 'v0.4.0-rc1'],
  ]) {
    assert.equal(shellIsStale(shell, tag), false, `${shell} vs ${tag} should not be called stale`);
  }
});

test('a tag with or without its leading v reads the same', () => {
  assert.equal(shellIsStale('0.3.1', '0.4.0'), true);
  assert.equal(shellIsStale('0.3.1', 'V0.4.0'), true);
});
