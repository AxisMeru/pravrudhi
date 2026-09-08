'use strict';
// Putting a verified build in place of the installed one.
//
// The ordering is the safety property. The download is staged beside the target, the live application is moved
// aside as a backup, and only then does the new one take its place — so a failure at any point leaves either
// the old application or a restorable backup, never nothing. The worst outcome this guards against is not a
// failed update; it is an application that is no longer there.
//
// Staging beside the target rather than in a temporary directory keeps every move a rename on one filesystem.
// A rename is atomic and cannot half-copy; moving across filesystems is a copy that can, and a half-copied
// application is exactly the state this must never produce.
//
// The bytes arriving here have already been matched against the release's SHA256SUMS
// (shell-update.js::verifyDigest). Nothing unverified reaches this function, and nothing here re-decides that.
const {stagedNames} = require('./shell-update');

async function applyBinary({target, bytes, mode = 0o755}, io) {
  if (!bytes || bytes.length === 0) {
    return {applied: false, reason: 'refusing to install an empty download'};
  }
  const {staged, backup} = stagedNames(target);

  try {
    await io.write(staged, bytes);
    await io.chmod(staged, mode);
  } catch (error) {
    // Nothing has moved yet, so the installed application is untouched. Clear the partial file and stop.
    await io.remove(staged).catch(() => {});
    return {applied: false, reason: `could not stage the new build: ${error.message}`};
  }

  const hadPrevious = await io.exists(backup);
  if (hadPrevious) await io.remove(backup).catch(() => {});

  let movedAside = false;
  try {
    if (await io.exists(target)) { await io.rename(target, backup); movedAside = true; }
    await io.rename(staged, target);
    return {applied: true, reason: `installed, previous build kept at ${backup}`, backup};
  } catch (error) {
    // Put it back. A machine with no application is a worse outcome than one that did not update.
    if (movedAside) await io.rename(backup, target).catch(() => {});
    await io.remove(staged).catch(() => {});
    return {applied: false, reason: `could not install the new build: ${error.message}`};
  }
}


// macOS ships an application as a directory, and the update arrives as a zip of it. Same ordering as above —
// unpack somewhere else entirely, confirm the archive actually held the bundle this install is, and only then
// move the live one aside. An archive is the least trustworthy step here: it is the one thing that can look
// like a download and contain something else.
async function applyBundle({target, bytes, bundleName}, io) {
  if (!bytes || bytes.length === 0) {
    return {applied: false, reason: 'refusing to install an empty download'};
  }
  const {staged, backup} = stagedNames(target);
  const workdir = await io.mkdtemp(`${staged}.`);
  const unpacked = `${workdir}/${bundleName}`;

  try {
    await io.write(`${workdir}.zip`, bytes);
    await io.extract(`${workdir}.zip`, workdir);
  } catch (error) {
    await io.remove(workdir).catch(() => {});
    await io.remove(`${workdir}.zip`).catch(() => {});
    return {applied: false, reason: `could not unpack the new build: ${error.message}`};
  }

  if (!(await io.exists(unpacked))) {
    // The archive unpacked but holds something other than this edition's application. Installing whatever it
    // did contain would be worse than not updating.
    await io.remove(workdir).catch(() => {});
    await io.remove(`${workdir}.zip`).catch(() => {});
    return {applied: false, reason: `the archive did not contain ${bundleName}`};
  }

  if (await io.exists(backup)) await io.remove(backup).catch(() => {});

  let movedAside = false;
  try {
    if (await io.exists(target)) { await io.rename(target, backup); movedAside = true; }
    await io.rename(unpacked, target);
    await io.remove(`${workdir}.zip`).catch(() => {});
    return {applied: true, reason: `installed, previous build kept at ${backup}`, backup};
  } catch (error) {
    if (movedAside) await io.rename(backup, target).catch(() => {});
    await io.remove(workdir).catch(() => {});
    await io.remove(`${workdir}.zip`).catch(() => {});
    return {applied: false, reason: `could not install the new build: ${error.message}`};
  }
}

module.exports = {applyBinary, applyBundle};
