'use strict';
const {discoverEngine, freePort} = require('./core');
const {createApiClient} = require('./api');
function loopbackOrigin(value) {
  try {
    const url = new URL(value);
    if (url.protocol !== 'http:' || !['127.0.0.1','localhost','[::1]'].includes(url.hostname) || url.username || url.password) return null;
    return url.origin;
  } catch { return null; }
}
async function selectConnection({candidates = [], discover = discoverEngine, allocate = freePort,
  health = origin => createApiClient(()=>origin,{timeout:1000}).health()} = {}) {
  for (const origin of new Set(candidates.map(loopbackOrigin).filter(Boolean))) {
    try { if ((await health(origin))?.ok === true) return {attached:true,origin,binary:await discover()}; } catch { /* Try the next installed endpoint. */ }
  }
  const binary = await discover();
  if (!binary) throw new Error('Connect an installed engine to get started.');
  return {attached:false,binary,origin:`http://127.0.0.1:${await allocate()}`};
}
function defaultWorkspace({env, saved, binary, home = require('node:os').homedir(), exists = require('node:fs').existsSync} = {}) {
  const path = require('node:path');
  if (env || saved) return path.resolve(env || saved);
  if (binary) {
    const project = path.resolve(path.dirname(binary),'../..');
    if (exists(path.join(project,'app/frontend/out/index.html'))) return project;
  }
  // A release install keeps its workspace beside its releases directory, so drive that rather than guessing.
  const release = path.resolve(home,'pravrudhi-release');
  if (exists(path.join(release,'.pravrudhi'))) return release;
  // Falling back to the bare home directory made the engine write .pravrudhi/ and research/ straight into it.
  // A dedicated directory is created on first use instead; the user can still choose another from the menu.
  return path.resolve(home,'pravrudhi');
}
module.exports = {selectConnection, loopbackOrigin, defaultWorkspace};
