'use strict';
const slug = value => typeof value === 'string' && /^[a-z0-9][a-z0-9-]{1,62}$/.test(value);
// The band's own terms, from docs/BAND.md's table over src/pravrudhi/assets/configs/band.yaml: what
// the engine may do without asking, what it must ask about first, the lifetime spend ceiling, and how
// often it may run. Rendered in the user's terms, not the policy's field names.
const BAND_LEVELS = Object.freeze([
  {id:'one_time', name:'One-time build', mayActWithoutAsking:'Produce the initial artifact.',
    mustAskFirst:'None; further work requires changing the choice.', spendCeilingUsd:10, runFrequency:'One run, then stop.'},
  {id:'critical', name:'Critical updates only', mayActWithoutAsking:'Build; repair reported breakage; patch reported unsafe dependencies.',
    mustAskFirst:'None; other work is outside this level.', spendCeilingUsd:50, runFrequency:'At most once per day.'},
  {id:'self_healing', name:'Self-healing', mayActWithoutAsking:'Critical actions; check against a recorded baseline and repair a reported regression.',
    mustAskFirst:'None; other work is outside this level.', spendCeilingUsd:150, runFrequency:'At most once per hour.'},
  {id:'continuous', name:'Continuous improvement', mayActWithoutAsking:'Self-healing actions; propose and test unsolicited improvements.',
    mustAskFirst:'Deploy an improvement.', spendCeilingUsd:500, runFrequency:'At most once per 15 minutes.'},
]);
function createProduct({api, auth, selectWorkspace}) {
  let selected = null, generation = 0;
  // Per-artifact band choice. Nothing in the desktop's closed route table can carry this to the engine yet, so
  // it lives only for this signed-in session: real for the user driving it now, honest about not surviving restart.
  const bands = new Map();
  async function identity() {
    if (!auth.status().user) throw new Error('Sign in to open your workspaces.');
    const me = await api.me();
    if (!me.authenticated || me.id !== auth.status().user?.id) throw new Error('This engine must enable Supabase identity before you can open personal workspaces.');
  }
  return {
    reset() { selected = null; generation++; bands.clear(); },
    async workspaces() {
      await identity();
      const result = await api.workspaces();
      return result.workspaces.map(w=>({slug:w.slug}));
    },
    async createWorkspace(value) {
      await identity();
      if (!slug(value)) throw new Error('Use 2–63 lowercase letters, digits or hyphens.');
      const result = await api.createWorkspace({body:{slug:value}});
      return {slug:result.slug};
    },
    async choose(value) {
      await identity();
      const ticket = ++generation;
      selected = null;
      const result = await api.workspaces();
      const workspace = result.workspaces.find(w=>w.slug === value);
      if (!workspace || !slug(value)) throw new Error('Choose one of your workspaces.');
      await selectWorkspace(workspace.path);
      await identity();
      if (ticket !== generation) throw new Error('Workspace selection changed.');
      selected = value;
      return {slug:selected};
    },
    async artifacts() {
      await identity();
      if (!selected) throw new Error('Choose a workspace first.');
      const ticket = generation;
      const result = await api.objectives();
      if (ticket !== generation) throw new Error('Workspace selection changed.');
      // Explicit projection keeps internal provenance and untrusted HTML out of the product.
      return {workspace:selected, artifacts:result.objectives.map(o=>({id:o.id, intent:o.intent,
        location:o.notes || '', progress:(o.progress || []).map(p=>({benchmark:p.benchmark,state:p.state,
          baseline:p.baseline?.value ?? null, latest:p.latest?.value ?? null, delta:p.delta ?? null}))})),
        problems:(result.problems || []).length};
    },
    async create(input) {
      await identity();
      if (!selected) throw new Error('Choose a workspace first.');
      if (!slug(input?.id) || !['intent','location','metric'].every(k=>typeof input[k] === 'string' && input[k].trim() && input[k].length <= 4000)) throw new Error('Supply a name, goal, external location and success metric.');
      if (!['up','down'].includes(input.direction)) throw new Error('Choose a metric direction.');
      const existing = await api.objectives();
      if (existing.objectives.some(o=>o.id === input.id)) throw new Error('That name is already in use.');
      await api.createObjective({body:{id:input.id, intent:input.intent.trim(), track:input.id,
        notes:input.location.trim(), benchmarks:[{id:input.id,tool:'lm-eval',metric:input.metric.trim(),direction:input.direction}]}});
      return {id:input.id};
    },
    // Opening one objective, starting work on it, watching it and stopping it. The engine reads `workspace` to
    // decide whose project a request is about, so it travels with every one of these: an operator with none
    // named gets the engine's own project, which is where prabhasa-nyaya lives.
    async objective(id) {
      await identity();
      return api.objective({id, workspace:selected ?? undefined});
    },
    async runs() {
      await identity();
      const rows = await api.runs({workspace:selected ?? undefined});
      return Array.isArray(rows) ? rows : [];
    },
    async startWork({target = 'model', ...rest} = {}) {
      // Taken before the first await, not after. Capturing it later left a window: the call yields at
      // `identity()`, a workspace switch runs in that gap, and the ticket is then read as the *new* value, so
      // the guard compares a number against itself and never fires.
      const ticket = generation;
      await identity();
      const run = await api.startRun({workspace:selected ?? undefined, body:{target, ...rest}});
      // The workspace can be switched while a request is in flight, and a run started against the previous one
      // must not be reported as belonging to the current.
      if (ticket !== generation) throw new Error('Workspace changed while the run was starting.');
      return run;
    },
    async stopWork(runId) {
      await identity();
      return api.stopRun({id:runId, workspace:selected ?? undefined});
    },
    bandLevels() { return BAND_LEVELS.map(level => ({...level})); },
    async chooseBand(artifactId, levelId) {
      await identity();
      if (!selected) throw new Error('Choose a workspace first.');
      if (!slug(artifactId)) throw new Error('Choose an artifact.');
      if (!BAND_LEVELS.some(level => level.id === levelId)) throw new Error('Choose one of the offered levels.');
      bands.set(`${selected}/${artifactId}`, levelId);
      return {id:artifactId, level:levelId};
    },
    bandFor(artifactId) { return selected ? bands.get(`${selected}/${artifactId}`) || null : null; }
  };
}
module.exports = {createProduct};
