'use strict';
const fs = require('node:fs');
const path = require('node:path');
const net = require('node:net');
const os = require('node:os');
async function discoverEngine({env = process.env, home = os.homedir(), saved, executable = async p => {
  try { await fs.promises.access(p, fs.constants.X_OK); return (await fs.promises.stat(p)).isFile(); } catch { return false; }
}} = {}) {
  const candidates = [env.PRAVRUDHI_BIN, ...(env.PATH || '').split(path.delimiter).filter(Boolean).map(p => path.join(p, 'pravrudhi')),
    path.join(home, 'pravrudhi-release/.pravrudhi/releases/current/.venv/bin/pravrudhi'), path.join(home, '.local/bin/pravrudhi'), saved,
    path.join(home, 'pravrudhi/.pravrudhi/releases/current/.venv/bin/pravrudhi')];
  for (const p of [...new Set(candidates.filter(Boolean))]) if (await executable(p)) return path.resolve(p);
  return null;
}
function freePort(createServer = net.createServer) {
  return new Promise((resolve, reject) => {
    const server = createServer();
    server.once('error', reject);
    server.listen(0, '127.0.0.1', () => { const port = server.address().port; server.close(err => err ? reject(err) : resolve(port)); });
  });
}
async function pollHealth(url, {fetchFn = fetch, timeout = 30000, interval = 250, signal, now = Date.now, sleep = ms => new Promise(r => setTimeout(r, ms))} = {}) {
  const deadline = now() + timeout;
  while (now() < deadline) {
    signal?.throwIfAborted();
    const controller = new AbortController();
    const abort = () => controller.abort();
    signal?.addEventListener('abort', abort, {once: true});
    let timer;
    try {
      const result = await Promise.race([
        (async () => { const response = await fetchFn(url, {signal: controller.signal}); return response.ok && await response.json(); })(),
        new Promise((_, reject) => { timer = setTimeout(() => { controller.abort(); reject(new Error('Health request timed out')); }, Math.min(1000, deadline - now())); })
      ]);
      if (result?.ok === true) return result;
    } catch { signal?.throwIfAborted(); }
    finally { clearTimeout(timer); signal?.removeEventListener('abort', abort); }
    await sleep(Math.max(0, Math.min(interval, deadline - now())));
  }
  throw new Error('The engine did not become healthy within 30 seconds.');
}
function parseDoctor(output) {
  const value = JSON.parse(output);
  if (!Array.isArray(value.checks) || value.checks.some(c => typeof c.name !== 'string' || typeof c.ok !== 'boolean' || typeof c.detail !== 'string')) throw new Error('Invalid doctor output: expected named checks with ok and detail.');
  return value.checks;
}
function linkPolicy(url, origin) {
  try { const u = new URL(url); if (!['http:', 'https:'].includes(u.protocol)) return 'deny'; return u.origin === origin ? 'internal' : 'external'; } catch { return 'deny'; }
}
function readState(file) { try { const value = JSON.parse(fs.readFileSync(file, 'utf8')); return value && typeof value === 'object' && !Array.isArray(value) ? value : {}; } catch { return {}; } }
function writeState(file, value) { fs.mkdirSync(path.dirname(file), {recursive:true}); fs.writeFileSync(`${file}.tmp`, JSON.stringify(value)); fs.renameSync(`${file}.tmp`, file); }
function validBounds(b) { return b && ['x','y','width','height'].every(k => Number.isFinite(b[k])) && b.width >= 640 && b.height >= 480; }
module.exports = {discoverEngine, freePort, pollHealth, parseDoctor, linkPolicy, readState, writeState, validBounds};
