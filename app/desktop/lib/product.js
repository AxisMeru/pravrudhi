'use strict';
const slug = value => typeof value === 'string' && /^[a-z0-9][a-z0-9-]{1,62}$/.test(value);
function createProduct({api, auth, selectWorkspace}) {
  let selected = null, generation = 0;
  async function identity() {
    if (!auth.status().user) throw new Error('Sign in to open your workspaces.');
    const me = await api.me();
    if (!me.authenticated || me.id !== auth.status().user?.id) throw new Error('This engine must enable Supabase identity before you can open personal workspaces.');
  }
  return {
    reset() { selected = null; generation++; },
    async workspaces() {
      await identity();
      const result = await api.workspaces();
      return result.workspaces.map(w=>({slug:w.slug}));
    },
    async createWorkspace(value) {
      await identity();
      if (!slug(value)) throw new Error('Use 2–63 lowercase letters, digits or hyphens.');
      const result = await api.createWorkspace({slug:value});
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
      await api.createObjective({id:input.id, intent:input.intent.trim(), track:input.id,
        notes:input.location.trim(), benchmarks:[{id:input.id,tool:'lm-eval',metric:input.metric.trim(),direction:input.direction}]});
      return {id:input.id};
    }
  };
}
module.exports = {createProduct};
