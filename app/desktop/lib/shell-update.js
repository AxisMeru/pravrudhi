'use strict';
// Replacing the running application, which is the one thing the engine's own updater cannot do for it.
//
// The engine keeps itself current — checks its channel, verifies a download by checksum, installs into a
// versioned directory, switches, and rolls back if the new install cannot answer. The shell had none of that:
// a new Electron bundle meant a person downloading a file and running an installer, so a shell could sit
// behind a current engine indefinitely with nothing saying so.
//
// This module is the part that decides *what* to install, kept pure so it can be tested without touching a
// disk or a network: which published asset belongs to this install, and whether the bytes that arrived are the
// bytes the release published. Applying them is `applyUpdate` in shell-apply.js, which has the side effects.
//
// The checksum is the safety property, not a nicety. This downloads an executable and then runs it. An asset
// the release's own SHA256SUMS does not vouch for is refused — including one it simply does not mention, since
// "no digest recorded, assume fine" would accept anything a network could serve.
const crypto = require('node:crypto');
const path = require('node:path');

// How each platform names the build this updater can actually apply. macOS takes the zip rather than the dmg:
// a dmg needs hdiutil to attach and detach, while a zip can be verified in memory and unpacked in process.
// Both are published, because a person installing by hand wants the dmg.
const EXTENSION = {linux: '.AppImage', darwin: '.zip', win32: '.exe'};
const OS_TOKEN = {linux: 'linux', darwin: 'mac', win32: 'win'};

// electron-builder's ${arch} macro and Node's process.arch do not agree: a Linux build is named x86_64 where
// process.arch reports x64. Matched literally, every Linux install would ask for a build that is never
// published and conclude there was none — a safe failure and a dead feature. Aliases never cross between
// architectures; x64 and arm64 remain distinct however they are spelled.
const ARCH_ALIASES = {x64: ['x64', 'x86_64', 'amd64'], arm64: ['arm64', 'aarch64']};

// Release assets are named pravrudhi-<edition>-<version>-<os>-<arch>.<ext> by the build configurations, so the
// edition is a field rather than something inferred by matching "Pravrudhi" against "Pravrudhi Studio" — a
// prefix test that quietly picks the wrong edition's build.
function pickAsset(assets, {edition, platform, arch}) {
  const ext = EXTENSION[platform], os = OS_TOKEN[platform];
  if (!ext || !os) return null;
  const wanted = `pravrudhi-${edition}-`;
  const suffixes = (ARCH_ALIASES[arch] ?? [arch]).map(a => `-${os}-${a}${ext}`);
  return (assets ?? []).find(a => {
    const name = String(a?.name ?? '');
    return name.startsWith(wanted) && suffixes.some(suffix => name.endsWith(suffix));
  }) ?? null;
}

// `sha256sum` output: one "<digest>  <name>" line per file. The name is compared by basename because a release
// assembled elsewhere may carry a path where a local run writes a bare name.
function verifyDigest(bytes, assetName, sumsText) {
  const wanted = path.basename(String(assetName ?? ''));
  if (!wanted) return false;
  const actual = crypto.createHash('sha256').update(bytes).digest('hex');
  for (const line of String(sumsText ?? '').split('\n')) {
    const [digest, ...rest] = line.trim().split(/\s+/);
    if (!digest || rest.length === 0) continue;
    if (path.basename(rest.join(' ')) !== wanted) continue;
    return digest.toLowerCase() === actual;
  }
  return false;  // not mentioned is not vouched for
}

// Where the new build is written and where the old one is kept. Both sit beside the target so the swap is a
// rename on one filesystem rather than a copy across two, and neither may equal the target: writing the
// download onto the live path would replace the running application before anything had verified it.
function stagedNames(target) {
  const dir = path.dirname(target), base = path.basename(target);
  return {
    staged: path.join(dir, `.${base}.incoming`),
    backup: path.join(dir, `.${base}.previous`),
  };
}

module.exports = {pickAsset, verifyDigest, stagedNames, EXTENSION, OS_TOKEN, ARCH_ALIASES};
