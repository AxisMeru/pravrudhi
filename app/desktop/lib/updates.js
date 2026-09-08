'use strict';
// The offer, not the mechanism. The engine already checks its channel, verifies a download by checksum,
// installs into a versioned directory and switches a symlink on its own — autonomously, outside this process.
// All this module does is notice, hold what it noticed until the user decides, and never install or restart
// on its own. It never calls /api/update/apply or /api/update/rollback: those are the operator's routes, and
// this is the client for the people the product is for, not the operator.

// Sensible default: frequent enough to catch a release the same day, far under GitHub's 60/hr unauthenticated
// budget that /api/update itself is spending against (see application/updates.py).
const DEFAULT_INTERVAL_MS = 6 * 60 * 60 * 1000;
// How long "downloading" is allowed to sit before the autonomous engine-side apply is presumed stuck.
const DEFAULT_DOWNLOAD_TIMEOUT_MS = 30 * 60 * 1000;

// The engine updates itself; this bundle does not. A shell replaced only by downloading a new AppImage or .app
// could therefore sit months behind a current engine with nothing saying so — its version was rendered in a
// status line and never compared to anything. This notices; it installs nothing, because installing a new shell
// means replacing the running application, which is the user's decision and their download.
//
// Anything unparseable is deliberately "not stale". A tag this cannot read is far more likely to be a release
// naming convention nobody told this function about than a real reason to nag someone to reinstall.
function shellIsStale(shellVersion, latestTag) {
  const parts = value => {
    const cleaned = String(value ?? '').trim().replace(/^v/i, '');
    if (!/^\d+(\.\d+)*$/.test(cleaned)) return null;
    return cleaned.split('.').map(Number);
  };
  const shell = parts(shellVersion), latest = parts(latestTag);
  if (!shell || !latest) return false;
  for (let i = 0; i < Math.max(shell.length, latest.length); i++) {
    const a = shell[i] ?? 0, b = latest[i] ?? 0;
    if (a !== b) return a < b;  // numeric, because "0.3.9" sorts after "0.3.10" as text
  }
  return false;
}

function createUpdateOffer({
  apiClient,
  intervalMs = DEFAULT_INTERVAL_MS,
  downloadTimeoutMs = DEFAULT_DOWNLOAD_TIMEOUT_MS,
  now = Date.now,
  setTimer = (fn, ms) => setInterval(fn, ms),
  clearTimer = id => clearInterval(id),
} = {}) {
  if (!apiClient || typeof apiClient.update !== 'function') throw new Error('createUpdateOffer requires an apiClient with an update() method.');

  let state = {status: 'none'};
  let dismissedVersion = null;
  let downloadDeadline = null;
  let timer = null;
  let inFlight = null;
  const listeners = new Set();

  function setState(next) {
    state = next;
    for (const listener of listeners) listener(state);
  }

  async function poll() {
    const result = await apiClient.update();
    const version = result?.latest?.tag ?? null;

    if (state.status === 'downloading') {
      // The engine having stopped offering the update is how a finished install is recognised — but only when
      // the engine actually managed to look. `update_available: false` also means "could not reach GitHub",
      // and treating that as a finished install told the operator a version was installed because the network
      // had briefly gone. `checked` separates the two (application/updates.py::status).
      if (result?.checked !== false && !result?.update_available) {
        setState({status: 'ready', version: state.version, notes: state.notes}); return;
      }
      if (downloadDeadline !== null && now() >= downloadDeadline) {
        setState({status: 'failed', version: state.version, notes: state.notes, reason: 'The update did not finish installing in time.'});
      }
      return; // still waiting on the autonomous install; keep the accepted offer steady
    }

    // 'ready' and 'failed' are terminal until the user acts (restart, or a fresh dismissal); a background poll
    // must not silently reset a state the user is looking at.
    if (state.status === 'ready' || state.status === 'failed') return;

    if (!result?.update_available || !version) { setState({status: 'none'}); return; }
    if (version === dismissedVersion) { setState({status: 'none'}); return; }
    if (state.status === 'available' && state.version === version) return; // no change worth a re-render
    setState({status: 'available', version, notes: result.latest.url || ''});
  }

  async function check() {
    if (inFlight) return inFlight;
    inFlight = poll()
      .catch(error => {
        // A quiet background check that fails (offline, rate-limited) must not scare the user with an error
        // state; only a failure while an accepted update is actually in flight is worth reporting.
        if (state.status === 'downloading') {
          setState({status: 'failed', version: state.version, notes: state.notes, reason: error?.message || 'Could not verify the update.'});
        }
      })
      .finally(() => { inFlight = null; });
    return inFlight;
  }

  function dismiss() {
    if (state.status !== 'available') return state;
    dismissedVersion = state.version;
    setState({status: 'none'});
    return state;
  }

  function accept() {
    if (state.status !== 'available') return state;
    downloadDeadline = now() + downloadTimeoutMs;
    setState({status: 'downloading', version: state.version, notes: state.notes});
    return state;
  }

  function start() {
    if (timer !== null) return;
    check();
    timer = setTimer(check, intervalMs);
  }

  function stop() {
    if (timer === null) return;
    clearTimer(timer);
    timer = null;
  }

  function subscribe(listener) {
    listeners.add(listener);
    return () => listeners.delete(listener);
  }

  function getState() { return state; }

  return Object.freeze({check, dismiss, accept, start, stop, subscribe, getState});
}

module.exports = {createUpdateOffer, shellIsStale, DEFAULT_INTERVAL_MS, DEFAULT_DOWNLOAD_TIMEOUT_MS};
