'use strict';
// The complete desktop route budget. No arbitrary renderer URL or method is accepted.
const ROUTES = Object.freeze({health:['GET','/api/health'], me:['GET','/api/me'],
  workspaces:['GET','/api/workspaces'], createWorkspace:['POST','/api/workspaces'],
  objectives:['GET','/api/objectives'], createObjective:['POST','/api/objectives'],
  update:['GET','/api/update'], updateConfig:['GET','/api/update/config'],
  saveUpdateConfig:['PUT','/api/update/config'], appToken:['GET','/api/app-token']});
function createApiClient(getOrigin, {fetchFn = fetch, timeout = 30000, getToken = async()=>null} = {}) {
  async function request(name, body) {
    const [method, endpoint] = ROUTES[name];
    const origin = getOrigin();
    if (!origin) throw new Error('Engine is not connected.');
    const headers = {};
    const bearer = await getToken();
    if (bearer) headers.Authorization = `Bearer ${bearer}`;
    if (method !== 'GET') {
      const local = await request('appToken');
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
  return Object.freeze(Object.fromEntries(Object.keys(ROUTES).filter(n=>n !== 'appToken').map(n=>[n,body=>request(n,body)])));
}
module.exports = {createApiClient, ROUTES};
