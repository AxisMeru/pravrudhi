'use strict';
// The whole sequence: notice a newer release, fetch the build for this install, prove it is the build the
// release published, and only then replace the application. Every refusal below leaves the installed
// application exactly as it was.
const test = require('node:test');
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const {updateShell} = require('../lib/shell-updater');

const BYTES = Buffer.from('a newer application');
const DIGEST = crypto.createHash('sha256').update(BYTES).digest('hex');
const NAME = 'pravrudhi-product-0.4.0-linux-x64.AppImage';

function release({sums = `${DIGEST}  ${NAME}\n`, assets} = {}) {
  return {
    tag_name: 'v0.4.0',
    assets: assets ?? [
      {name: NAME, browser_download_url: 'https://x/app'},
      {name: 'SHA256SUMS', browser_download_url: 'https://x/sums'},
    ],
    _sums: sums,
  };
}

function deps(rel, {bytes = BYTES, applied = true} = {}) {
  const seen = {applied: null, downloads: []};
  return {
    seen,
    latestRelease: async () => rel,
    download: async url => { seen.downloads.push(url); return url.endsWith('sums') ? Buffer.from(rel._sums) : bytes; },
    apply: async payload => { seen.applied = payload; return {applied, reason: applied ? 'installed' : 'refused'}; },
  };
}

const HERE = {edition: 'product', platform: 'linux', arch: 'x64', currentVersion: '0.3.1', target: '/bin/app'};

test('a newer release is fetched, verified and installed', async () => {
  const d = deps(release());
  const result = await updateShell(HERE, d);

  assert.equal(result.applied, true, result.reason);
  assert.equal(d.seen.applied.target, '/bin/app');
  assert.ok(d.seen.applied.bytes.equals(BYTES));
});

test('a shell already current does nothing and downloads nothing', async () => {
  const d = deps(release());
  const result = await updateShell({...HERE, currentVersion: '0.4.0'}, d);

  assert.equal(result.applied, false);
  assert.match(result.reason, /already/i);
  assert.deepEqual(d.seen.downloads, [], 'a current shell still went to the network for a build');
});

test('bytes that do not match the published digest are never installed', async () => {
  // The property this whole module exists for: it downloads an executable and then runs it.
  const d = deps(release(), {bytes: Buffer.from('something else entirely')});
  const result = await updateShell(HERE, d);

  assert.equal(result.applied, false);
  assert.match(result.reason, /checksum|digest|verify/i);
  assert.equal(d.seen.applied, null, 'an unverified build was handed to the installer');
});

test('a release with no build for this install is left alone', async () => {
  const d = deps(release({assets: [{name: 'SHA256SUMS', browser_download_url: 'https://x/sums'}]}));
  const result = await updateShell(HERE, d);

  assert.equal(result.applied, false);
  assert.match(result.reason, /no build/i);
  assert.equal(d.seen.applied, null);
});

test('a release with no checksum file is refused rather than trusted', async () => {
  const d = deps(release({assets: [{name: NAME, browser_download_url: 'https://x/app'}]}));
  const result = await updateShell(HERE, d);

  assert.equal(result.applied, false);
  assert.match(result.reason, /checksum/i);
  assert.equal(d.seen.applied, null);
});

test('an unreachable release check fails quietly and changes nothing', async () => {
  const d = deps(release());
  d.latestRelease = async () => { throw new Error('network down'); };
  const result = await updateShell(HERE, d);

  assert.equal(result.applied, false);
  assert.match(result.reason, /network down/);
  assert.equal(d.seen.applied, null);
});

test('the edition asked for is the edition installed', async () => {
  const studioName = 'pravrudhi-studio-0.4.0-linux-x64.AppImage';
  const studioBytes = Buffer.from('studio build');
  const studioDigest = crypto.createHash('sha256').update(studioBytes).digest('hex');
  const rel = {
    tag_name: 'v0.4.0',
    assets: [
      {name: NAME, browser_download_url: 'https://x/product'},
      {name: studioName, browser_download_url: 'https://x/studio'},
      {name: 'SHA256SUMS', browser_download_url: 'https://x/sums'},
    ],
    _sums: `${DIGEST}  ${NAME}\n${studioDigest}  ${studioName}\n`,
  };
  const d = deps(rel, {bytes: studioBytes});
  const result = await updateShell({...HERE, edition: 'studio'}, d);

  assert.equal(result.applied, true, result.reason);
  assert.ok(d.seen.downloads.includes('https://x/studio'), 'Studio was updated from the product build');
});
