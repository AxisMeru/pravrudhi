'use strict';
// Which of the two products this install is. Studio builds and improves Pravrudhi; Pravrudhi improves whatever
// its user is building. They are one codebase and two installs that must sit side by side on one machine, so
// the answer is decided when the build is made: a packaged app has no role to read and no repository to consult.
//
// It arrives as `edition.json` beside the packaged resources, copied there by whichever electron-builder
// configuration made the build (`extraResources`). Running from source there is no such file, and the answer is
// the product.
//
// Two simpler-looking approaches were tried and are recorded because both fail quietly:
//
//   `extraMetadata` writes the edition into the packaged package.json — and, with the app directory equal to
//   the project directory, writes the transformed file back over the *source*. One build left this repository
//   with a seven-line package.json, no scripts and no devDependencies.
//
//   `app.getName()` looked like it would return the productName. It does not: electron-builder leaves `name` as
//   `pravrudhi-desktop` in the packaged package.json and carries productName elsewhere, so both builds answered
//   identically and Studio would have run as the product with nothing to show for it.
//
// Anything unreadable, missing or unrecognised is the product. Studio is the operator's edition and is not
// released to any other user, so a build that cannot say what it is must be the harmless one; defaulting the
// other way would ship Studio to whoever downloaded an unlabelled build.
const fs = require('node:fs');
const path = require('node:path');

const PRODUCT = 'product';
const STUDIO = 'studio';

function editionOf(declared) {
  return String(declared ?? '').trim().toLowerCase() === STUDIO ? STUDIO : PRODUCT;
}

// `resourcesPath` is Electron's own directory when running from source, where no edition.json exists.
function readEdition(resourcesPath, readFile = p => fs.readFileSync(p, 'utf8')) {
  try {
    return editionOf(JSON.parse(readFile(path.join(resourcesPath, 'edition.json'))).edition);
  } catch {
    return PRODUCT;  // absent, unreadable or malformed: never Studio by accident
  }
}

// Both builds package the same `name` — electron-builder carries productName in the platform metadata, not in
// the packaged package.json — so Electron's app.getName() answers "pravrudhi-desktop" for either one, and the
// per-user data directory it derives from that would be shared. Two installs that overwrite each other's saved
// workspace and window state are not two installs. Studio therefore gets its own directory by name.
function userDataName(edition) {
  return editionOf(edition) === STUDIO ? 'pravrudhi-studio' : 'pravrudhi-desktop';
}

// The engine decides what to call itself from this variable (src/pravrudhi/api/edition.py::EDITION_ENV), so the
// shell it is spawned into carries this build's answer. It is set last and unconditionally: a variable inherited
// from the user's environment must not let a product install present itself as Studio.
function engineEnv(env, edition) {
  return {...env, PRAVRUDHI_EDITION: editionOf(edition)};
}

module.exports = {PRODUCT, STUDIO, editionOf, readEdition, engineEnv, userDataName};
