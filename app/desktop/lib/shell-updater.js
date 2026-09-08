'use strict';
// Notice a newer release, fetch the build for this install, prove it is the build the release published, and
// only then replace the application.
//
// Every step that cannot be completed leaves the installed application exactly as it was. That is the bias
// throughout: a shell that did not update is a small problem, and a shell replaced by something unverified —
// or by the other edition's build — is a large one. The engine's own updater takes the same line, and this
// borrows its discipline rather than inventing a looser one (application/update_apply.py).
//
// Dependencies are injected so the sequence can be tested without a network or a filesystem: `latestRelease`
// returns the GitHub release payload, `download` returns bytes for a URL, and `apply` puts a verified build in
// place (shell-apply.js).
const {pickAsset, verifyDigest} = require('./shell-update');
const {shellIsStale} = require('./updates');

async function updateShell({edition, platform, arch, currentVersion, target, bundleName}, {latestRelease, download, apply}) {
  let release;
  try {
    release = await latestRelease();
  } catch (error) {
    return {applied: false, reason: `could not check for a newer application: ${error.message}`};
  }

  const tag = release?.tag_name ?? null;
  if (!shellIsStale(currentVersion, tag)) {
    // Nothing to do, and nothing downloaded: a current shell must not spend bandwidth proving it is current.
    return {applied: false, reason: `already at ${currentVersion}`, version: currentVersion};
  }

  const asset = pickAsset(release?.assets, {edition, platform, arch});
  if (!asset) {
    return {applied: false, reason: `release ${tag} has no build for this install (${edition}, ${platform}, ${arch})`};
  }
  const sums = (release?.assets ?? []).find(a => a?.name === 'SHA256SUMS');
  if (!sums) {
    // Without the release's own checksums there is nothing to check the download against, and an unchecked
    // executable is not something to install because a version number looked newer.
    return {applied: false, reason: `release ${tag} publishes no checksums (SHA256SUMS); refusing to install unverified`};
  }

  let bytes, sumsText;
  try {
    [bytes, sumsText] = await Promise.all([
      download(asset.browser_download_url),
      download(sums.browser_download_url).then(b => b.toString('utf8')),
    ]);
  } catch (error) {
    return {applied: false, reason: `could not download ${asset.name}: ${error.message}`};
  }

  if (!verifyDigest(bytes, asset.name, sumsText)) {
    return {applied: false, reason: `checksum mismatch for ${asset.name}; refusing to install`};
  }

  const result = await apply({target, bytes, bundleName, assetName: asset.name});
  return {...result, version: tag};
}

module.exports = {updateShell};
