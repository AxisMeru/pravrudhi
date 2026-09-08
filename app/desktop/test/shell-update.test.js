'use strict';
// Replacing the running application is the most destructive thing this app does, so the parts that decide
// *what* to install are pure and tested here: which asset belongs to this install, and whether the bytes that
// arrived are the bytes the release published.
//
// The checksum is not decoration. This downloads an executable over the network and then runs it; a build that
// cannot be matched to the release's own SHA256SUMS must never be applied. Same discipline the engine's
// updater already uses (application/update_apply.py::verify_digest).
const test = require('node:test');
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const {pickAsset, verifyDigest, stagedNames} = require('../lib/shell-update');

const ASSETS = [
  {name: 'pravrudhi-product-0.4.0-linux-x64.AppImage', browser_download_url: 'u/1'},
  {name: 'pravrudhi-studio-0.4.0-linux-x64.AppImage', browser_download_url: 'u/2'},
  {name: 'pravrudhi-product-0.4.0-mac-arm64.zip', browser_download_url: 'u/3'},
  {name: 'pravrudhi-product-0.4.0-mac-arm64.dmg', browser_download_url: 'u/4'},
  {name: 'pravrudhi-studio-0.4.0-mac-arm64.zip', browser_download_url: 'u/5'},
  {name: 'pravrudhi-product-0.4.0-win-x64.exe', browser_download_url: 'u/6'},
  {name: 'SHA256SUMS', browser_download_url: 'u/sums'},
  {name: 'pravrudhi-0.4.0-py3-none-any.whl', browser_download_url: 'u/whl'},
];

test('each install picks its own edition and platform, never the other edition', () => {
  assert.equal(pickAsset(ASSETS, {edition: 'product', platform: 'linux', arch: 'x64'}).name,
    'pravrudhi-product-0.4.0-linux-x64.AppImage');
  assert.equal(pickAsset(ASSETS, {edition: 'studio', platform: 'linux', arch: 'x64'}).name,
    'pravrudhi-studio-0.4.0-linux-x64.AppImage');
});

test('macOS takes the zip, not the dmg', () => {
  // A dmg needs hdiutil to attach and detach; a zip can be verified and unpacked in process. Both are
  // published because a person double-clicks the dmg.
  assert.equal(pickAsset(ASSETS, {edition: 'product', platform: 'darwin', arch: 'arm64'}).name,
    'pravrudhi-product-0.4.0-mac-arm64.zip');
  assert.equal(pickAsset(ASSETS, {edition: 'studio', platform: 'darwin', arch: 'arm64'}).name,
    'pravrudhi-studio-0.4.0-mac-arm64.zip');
});

test('a platform or edition with no matching asset yields nothing, rather than something close', () => {
  assert.equal(pickAsset(ASSETS, {edition: 'studio', platform: 'win32', arch: 'x64'}), null);
  assert.equal(pickAsset(ASSETS, {edition: 'product', platform: 'darwin', arch: 'x64'}), null);
  assert.equal(pickAsset(ASSETS, {edition: 'product', platform: 'freebsd', arch: 'x64'}), null);
  assert.equal(pickAsset([], {edition: 'product', platform: 'linux', arch: 'x64'}), null);
});

test('the engine wheel and the checksum file are never mistaken for an application', () => {
  const picked = pickAsset(ASSETS, {edition: 'product', platform: 'linux', arch: 'x64'});
  assert.ok(!picked.name.endsWith('.whl') && picked.name !== 'SHA256SUMS');
});

test('bytes that match the published digest verify', () => {
  const bytes = Buffer.from('an application');
  const digest = crypto.createHash('sha256').update(bytes).digest('hex');
  const sums = `${digest}  pravrudhi-product-0.4.0-linux-x64.AppImage\nffff  other\n`;
  assert.equal(verifyDigest(bytes, 'pravrudhi-product-0.4.0-linux-x64.AppImage', sums), true);
});

test('bytes that do not match are refused', () => {
  const sums = `${'0'.repeat(64)}  pravrudhi-product-0.4.0-linux-x64.AppImage\n`;
  assert.equal(verifyDigest(Buffer.from('an application'), 'pravrudhi-product-0.4.0-linux-x64.AppImage', sums), false);
});

test('an asset the checksum file does not mention is refused, not waved through', () => {
  // The dangerous default: "no digest recorded, assume fine" would accept anything an attacker could serve.
  const bytes = Buffer.from('an application');
  const digest = crypto.createHash('sha256').update(bytes).digest('hex');
  assert.equal(verifyDigest(bytes, 'not-in-the-list.AppImage', `${digest}  something-else\n`), false);
  assert.equal(verifyDigest(bytes, 'anything', ''), false);
});

test('a checksum line for a path is matched by its basename', () => {
  // sha256sum run in a directory writes bare names, but a release built elsewhere may carry a path.
  const bytes = Buffer.from('an application');
  const digest = crypto.createHash('sha256').update(bytes).digest('hex');
  assert.equal(verifyDigest(bytes, 'app.AppImage', `${digest}  ./dist/app.AppImage\n`), true);
});

test('staging names never collide with the thing being replaced', () => {
  // The new build is written beside the installed one and swapped in; if the staged name equalled the live
  // name the download would overwrite the running application before it had been verified.
  const {staged, backup} = stagedNames('/home/x/.local/bin/pravrudhi-desktop');
  assert.notEqual(staged, '/home/x/.local/bin/pravrudhi-desktop');
  assert.notEqual(backup, '/home/x/.local/bin/pravrudhi-desktop');
  assert.notEqual(staged, backup);
  assert.ok(staged.startsWith('/home/x/.local/bin/'), 'staging beside the target keeps the swap on one filesystem');
});

// --- which file on this machine actually gets replaced -------------------------------------------------------

const {installedPath} = require('../lib/shell-update-io');

test('a Linux AppImage knows its own path, and a source checkout has none to replace', () => {
  assert.equal(installedPath({platform: 'linux', env: {APPIMAGE: '/home/x/.local/bin/pravrudhi-desktop'}}),
    '/home/x/.local/bin/pravrudhi-desktop');
  // Running `electron .` from a checkout: there is no installed application, and replacing something would be
  // replacing the developer's own tree.
  assert.equal(installedPath({platform: 'linux', env: {}}), null);
});

test('a macOS bundle is found from the executable inside it', () => {
  assert.equal(
    installedPath({platform: 'darwin', appPath: '/Users/x/Applications/Pravrudhi Studio.app/Contents/MacOS/Pravrudhi Studio'}),
    '/Users/x/Applications/Pravrudhi Studio.app');
  assert.equal(installedPath({platform: 'darwin', appPath: '/Users/x/src/app/main.js'}), null);
});

test('a platform with no in-place update returns nothing rather than a guess', () => {
  assert.equal(installedPath({platform: 'win32', env: {}}), null);
});

// --- the arch a build is named after is not the arch Node reports ---------------------------------------------

test('an asset named x86_64 is found by a process reporting x64', () => {
  // electron-builder's ${arch} macro writes x86_64 on Linux; Node's process.arch says x64. Matched literally,
  // every Linux install would look for a build that is never published and report "no build for this install"
  // forever — a safe failure, and a feature that never works. Caught by comparing a real artifact name against
  // what the code would ask for, not by reading the config.
  const assets = [{name: 'pravrudhi-product-0.3.1-linux-x86_64.AppImage', browser_download_url: 'u/1'}];
  assert.equal(pickAsset(assets, {edition: 'product', platform: 'linux', arch: 'x64'}).name,
    'pravrudhi-product-0.3.1-linux-x86_64.AppImage');
});

test('arm64 is found under either spelling', () => {
  const aarch = [{name: 'pravrudhi-studio-0.3.1-linux-aarch64.AppImage', browser_download_url: 'u/1'}];
  assert.ok(pickAsset(aarch, {edition: 'studio', platform: 'linux', arch: 'arm64'}));
  const arm = [{name: 'pravrudhi-product-0.3.1-mac-arm64.zip', browser_download_url: 'u/2'}];
  assert.ok(pickAsset(arm, {edition: 'product', platform: 'darwin', arch: 'arm64'}));
});

test('an alias never crosses architectures', () => {
  // x64 and arm64 are not interchangeable, however they are spelled.
  const assets = [{name: 'pravrudhi-product-0.3.1-linux-x86_64.AppImage', browser_download_url: 'u/1'}];
  assert.equal(pickAsset(assets, {edition: 'product', platform: 'linux', arch: 'arm64'}), null);
});
