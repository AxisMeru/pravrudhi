'use strict';
// The two installs must be able to sit side by side on one machine, and only one of them may be Studio.
// Electron derives the per-user data directory from productName and the OS treats appId as identity, so if
// these two ever converged the second install would overwrite the first's settings and window state.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {editionOf, PRODUCT, STUDIO} = require('../lib/edition');

const read = name => JSON.parse(fs.readFileSync(path.join(__dirname, '..', name), 'utf8'));
const product = read('electron-builder.product.json');
const studio = read('electron-builder.studio.json');

test('the two builds are distinct installs, not one overwriting the other', () => {
  assert.notEqual(product.appId, studio.appId);
  assert.notEqual(product.productName, studio.productName);
  assert.notEqual(product.directories.output, studio.directories.output);
});

test('each build ships the edition file that says what it is', () => {
  // This file, copied beside the packaged resources, is the only thing that makes a build Studio.
  const declared = config => JSON.parse(
    fs.readFileSync(path.join(__dirname, '..', config.extraResources[0].from), 'utf8')
  ).edition;
  assert.equal(product.extraResources[0].to, 'edition.json');
  assert.equal(studio.extraResources[0].to, 'edition.json');
  assert.equal(editionOf(declared(product)), PRODUCT);
  assert.equal(editionOf(declared(studio)), STUDIO);
});

test('running from source ships no edition file, so it is never Studio by accident', () => {
  assert.ok(!fs.existsSync(path.join(__dirname, '..', 'edition.json')));
});

test('no build uses extraMetadata, which rewrites this repository\'s own package.json', () => {
  // Not a style preference. With the app directory equal to the project directory, electron-builder writes the
  // transformed package.json back over the source: one build left this package.json with seven lines, no
  // scripts and no devDependencies. The edition comes from productName instead, which needs no stamping.
  assert.equal(product.extraMetadata, undefined);
  assert.equal(studio.extraMetadata, undefined);
});

test('the source package.json still has everything a build needs', () => {
  // The canary for the failure above: if a build ever clobbers this file again, this goes red rather than the
  // next build failing with "script not found" and no explanation.
  const pkg = read('package.json');
  assert.ok(pkg.scripts && Object.keys(pkg.scripts).length > 3, 'package.json lost its scripts');
  assert.ok(pkg.devDependencies?.['electron-builder'], 'package.json lost its build dependencies');
  assert.equal(pkg.main, 'main.js');
});

test('both builds target the three desktop operating systems', () => {
  for (const [name, config] of [['product', product], ['studio', studio]]) {
    assert.ok(config.linux?.target, `${name} has no linux target`);
    assert.ok(config.mac?.target, `${name} has no mac target`);
    assert.ok(config.win?.target, `${name} has no windows target`);
  }
});

test('the windows installer does not demand an administrator', () => {
  // An end user downloading this should not need to elevate, and a per-machine install would also collide
  // with the other edition in Program Files.
  assert.equal(product.nsis.perMachine, false);
  assert.equal(product.nsis.oneClick, false, 'a silent one-click install is not what a download should do');
});

test('every build script names a configuration, so a bare electron-builder cannot ship an unstamped app', () => {
  const scripts = read('package.json').scripts;
  for (const [name, command] of Object.entries(scripts)) {
    if (!name.startsWith('dist')) continue;
    assert.match(command, /--config electron-builder\.(product|studio)\.json/, `${name} names no configuration`);
  }
});
