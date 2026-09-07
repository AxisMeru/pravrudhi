'use strict';
// The six things this application is for: sign in; choose or create a workspace; see its artifacts and
// their real state; start something new; watch what is running and stop it; see what changed and keep or
// discard it; choose, per artifact, how hard the engine should keep working. app.js owns engine connection
// and diagnostics; this file owns none of that and never reaches an operator route (see lib/api.js).
(function () {
  const el = id => document.getElementById(id);
  const bridge = window.desktop && window.desktop.product;
  const productError = el('product-error');
  if (!bridge) {
    // Signing in and workspaces exist in lib/auth.js and lib/product.js, but nothing yet carries them
    // through preload.js into this window. Saying so beats a dead form or a silent crash on first click.
    productError.textContent = 'Your workspace is not connected in this build yet.';
    return;
  }
  let levelsCache = null;
  async function levels() { if (!levelsCache) levelsCache = await bridge.bandLevels(); return levelsCache; }
  async function run(action) {
    productError.textContent = '';
    try { await action(); } catch (e) { productError.textContent = e.message; }
  }
  async function refreshAuth() {
    const status = await bridge.authStatus();
    el('signout').hidden = !status.user;
    el('signin-status').textContent = status.user ? `Signed in as ${status.user.email || status.user.id}` : 'Not signed in.';
    el('workspace-section').hidden = !status.user;
    if (!status.user) el('artifact-section').hidden = true;
    return status;
  }
  async function loadWorkspaces() {
    const list = await bridge.workspaces();
    const container = el('workspace-list');
    container.replaceChildren();
    for (const w of list) {
      const button = document.createElement('button');
      button.className = 'secondary'; button.textContent = w.slug;
      button.addEventListener('click', () => run(async () => { await bridge.chooseWorkspace(w.slug); await loadArtifacts(); }));
      container.append(button);
    }
  }
  async function loadArtifacts() {
    const result = await bridge.artifacts();
    el('current-workspace').textContent = result.workspace;
    el('artifact-section').hidden = false;
    await renderArtifacts(result.artifacts);
  }
  function describeLevel(level) {
    return `May act without asking: ${level.mayActWithoutAsking} Must ask first: ${level.mustAskFirst} Spend ceiling: $${level.spendCeilingUsd}. Runs: ${level.runFrequency}`;
  }
  async function renderArtifacts(artifacts) {
    const bandLevels = await levels();
    const container = el('artifact-list');
    container.replaceChildren();
    for (const artifact of artifacts) {
      const card = document.createElement('article'); card.className = 'artifact';
      const heading = document.createElement('strong'); heading.textContent = `${artifact.id} — ${artifact.intent}`;
      const location = document.createElement('p'); location.className = 'caption'; location.textContent = artifact.location;
      card.append(heading, location);

      const progress = document.createElement('div'); progress.className = 'progress';
      for (const p of artifact.progress) {
        const row = document.createElement('div'); row.className = 'progress-row';
        const label = document.createElement('span'); label.textContent = `${p.benchmark}: ${p.state}`;
        row.append(label);
        if (p.baseline !== null || p.latest !== null) {
          const values = document.createElement('span'); values.className = 'caption';
          values.textContent = `${p.baseline ?? '—'} → ${p.latest ?? '—'}${p.delta !== null ? ` (Δ ${p.delta})` : ''}`;
          row.append(values);
        }
        if (p.delta !== null) {
          const keep = document.createElement('button'); keep.className = 'quiet'; keep.textContent = 'Keep'; keep.disabled = true;
          const discard = document.createElement('button'); discard.className = 'quiet'; discard.textContent = 'Discard'; discard.disabled = true;
          const note = document.createElement('small'); note.textContent = 'Reviewing a change needs a route the engine does not offer this application yet.';
          row.append(keep, discard, note);
        }
        if (String(p.state).toLowerCase() === 'running') {
          const stop = document.createElement('button'); stop.className = 'quiet'; stop.textContent = 'Stop'; stop.disabled = true;
          const note = document.createElement('small'); note.textContent = 'Stopping a run needs a route the engine does not offer this application yet.';
          row.append(stop, note);
        }
        progress.append(row);
      }
      card.append(progress);

      const band = document.createElement('div'); band.className = 'band';
      const select = document.createElement('select');
      for (const level of bandLevels) {
        const option = document.createElement('option'); option.value = level.id; option.textContent = level.name;
        select.append(option);
      }
      const description = document.createElement('p'); description.className = 'caption';
      const describe = id => { const level = bandLevels.find(l => l.id === id); description.textContent = level ? describeLevel(level) : ''; };
      const current = await bridge.bandFor(artifact.id);
      select.value = current || bandLevels[0].id;
      describe(select.value);
      select.addEventListener('change', () => run(async () => { await bridge.chooseBand(artifact.id, select.value); describe(select.value); }));
      band.append(select, description);
      card.append(band);

      container.append(card);
    }
  }
  el('signin-submit').addEventListener('click', () => run(async () => {
    await bridge.signIn({email: el('signin-email').value, password: el('signin-password').value});
    el('signin-password').value = '';
    await refreshAuth();
    await loadWorkspaces();
  }));
  el('signout').addEventListener('click', () => run(async () => {
    await bridge.signOut();
    el('workspace-list').replaceChildren(); el('artifact-list').replaceChildren();
    await refreshAuth();
  }));
  el('workspace-create').addEventListener('click', () => run(async () => {
    await bridge.createWorkspace(el('workspace-slug').value);
    el('workspace-slug').value = '';
    await loadWorkspaces();
  }));
  el('artifact-create').addEventListener('click', () => run(async () => {
    await bridge.createArtifact({
      id: el('artifact-id').value, intent: el('artifact-intent').value,
      location: el('artifact-location').value, metric: el('artifact-metric').value,
      direction: el('artifact-direction').value,
    });
    for (const id of ['artifact-id', 'artifact-intent', 'artifact-location', 'artifact-metric']) el(id).value = '';
    await loadArtifacts();
  }));
  run(async () => { const status = await refreshAuth(); if (status.user) await loadWorkspaces(); });
})();
