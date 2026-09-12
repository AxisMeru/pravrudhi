'use strict';
// The complete desktop route budget. No arbitrary renderer URL or method is accepted: the renderer names a
// route and may fill a `:id` slot, never an endpoint.
//
// Every entry here is one the engine classifies as the product's, in src/pravrudhi/api/roles.py. The run routes
// joined that list once each project got its own run manager; before that they spent the operator's hardware
// under the operator's keys whoever asked, and had no business in something a user installs.
const ROUTES = Object.freeze({health:['GET','/api/health'], me:['GET','/api/me'],
  workspaces:['GET','/api/workspaces'], createWorkspace:['POST','/api/workspaces'],
  objectives:['GET','/api/objectives'], createObjective:['POST','/api/objectives'],
  objective:['GET','/api/objectives/:id'],
  runs:['GET','/api/runs'], startRun:['POST','/api/runs'],
  run:['GET','/api/runs/:id'], stopRun:['POST','/api/runs/:id/stop'],
  models:['GET','/api/models'],
  providers:['GET','/api/providers'],
  setProviderKey:['POST','/api/providers/:id/key'], deleteProviderKey:['DELETE','/api/providers/:id/key'],
  update:['GET','/api/update'], updateConfig:['GET','/api/update/config'],
  saveUpdateConfig:['PUT','/api/update/config'], appToken:['GET','/api/app-token']});

// What may fill a `:id` slot. The renderer supplies these, so they are constrained rather than escaped: an
// identifier in this product is a candidate id, an objective id or a run id, and none of them contain a slash,
// a dot or a percent. Refusing an unexpected shape outright is narrower than trying to encode one safely.
const ID = /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/;

// A workspace decides whose project a request is about. Sent as a query parameter because that is what the
// engine reads, and omitted entirely when absent, since an operator with none named gets the engine's own.
const SLUG = /^[a-z0-9][a-z0-9-]{1,62}$/;
function createApiClient(getOrigin, {fetchFn = fetch, timeout = 30000, getToken = async()=>null} = {}) {
  async function request(name, {id, workspace, body} = {}) {
    const [method, template] = ROUTES[name];
    const origin = getOrigin();
    if (!origin) throw new Error('Engine is not connected.');
    if (template.includes(':id')) {
      if (!ID.test(String(id ?? ''))) throw new Error(`${template}: that is not a usable identifier.`);
    } else if (id !== undefined) {
      throw new Error(`${template}: takes no identifier.`);
    }
    let endpoint = template.replace(':id', String(id ?? ''));
    if (workspace !== undefined && workspace !== null) {
      if (!SLUG.test(String(workspace))) throw new Error('That is not a usable workspace name.');
      endpoint += `?workspace=${encodeURIComponent(workspace)}`;
    }
    const headers = {};
    const bearer = await getToken();
    if (bearer) headers.Authorization = `Bearer ${bearer}`;
    if (method !== 'GET') {
      const local = await request('appToken', {});
      if (typeof local.token !== 'string' || !local.token) throw new Error('Engine credential unavailable.');
      headers['x-pravrudhi-token'] = local.token;
      headers.Origin = origin;
      headers['Content-Type'] = 'application/json';
    }
    try {
      const response = await fetchFn(`${origin}${endpoint}`, {method, headers, redirect:'error', signal:AbortSignal.timeout(timeout),
        ...(body === undefined ? {} : {body:JSON.stringify(body)})});
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      return await response.json();
    } catch { throw new Error(`${endpoint}: request failed. Check the connection and sign-in.`); }
  }
  return Object.freeze(Object.fromEntries(
    Object.keys(ROUTES).filter(n=>n !== 'appToken').map(n=>[n,(options)=>request(n,options)])));
}
module.exports = {createApiClient, ROUTES};
