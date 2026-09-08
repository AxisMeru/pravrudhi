'use strict';
// The real filesystem and network behind the update sequence. Kept apart from the logic so the logic can be
// tested without either, and so everything with a side effect is in one small file that can be read in full.
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const {execFile} = require('node:child_process');
const {promisify} = require('node:util');

const run = promisify(execFile);

const RELEASE_API = 'https://api.github.com/repos/SharathSPhD/pravrudhi/releases/latest';
const DOWNLOAD_TIMEOUT_MS = 15 * 60 * 1000;

async function latestRelease(fetchFn = fetch) {
  const response = await fetchFn(RELEASE_API, {
    headers: {accept: 'application/vnd.github+json'},
    redirect: 'follow',
    signal: AbortSignal.timeout(60_000),
  });
  if (!response.ok) throw new Error(`GitHub answered ${response.status}`);
  return response.json();
}

async function download(url, fetchFn = fetch) {
  const response = await fetchFn(url, {redirect: 'follow', signal: AbortSignal.timeout(DOWNLOAD_TIMEOUT_MS)});
  if (!response.ok) throw new Error(`${url} answered ${response.status}`);
  return Buffer.from(await response.arrayBuffer());
}

// `ditto` is macOS's own archiver and the one install-macos.sh already uses, so an archive that installs by
// hand installs the same way here. `unzip` would drop resource forks and symlinks a bundle depends on.
const io = {
  write: (p, bytes) => fs.writeFile(p, bytes),
  chmod: (p, mode) => fs.chmod(p, mode),
  rename: (a, b) => fs.rename(a, b),
  remove: p => fs.rm(p, {recursive: true, force: true}),
  exists: p => fs.access(p).then(() => true, () => false),
  mkdtemp: prefix => fs.mkdtemp(prefix),
  extract: async (zip, dir) => {
    await fs.mkdir(dir, {recursive: true});
    await run('ditto', ['-x', '-k', zip, dir], {timeout: 10 * 60 * 1000});
  },
};

// Where this install actually lives, which is what gets replaced. On Linux an AppImage knows its own path
// through APPIMAGE; without it the app is running from source and there is nothing to replace.
function installedPath({platform = process.platform, env = process.env, appPath} = {}) {
  if (platform === 'linux') return env.APPIMAGE || null;
  if (platform === 'darwin') {
    // .../Pravrudhi.app/Contents/MacOS/Pravrudhi -> .../Pravrudhi.app
    const match = /^(.*\.app)\//.exec(appPath ?? '');
    return match ? match[1] : null;
  }
  return null;
}

module.exports = {latestRelease, download, io, installedPath, RELEASE_API, os, path};
