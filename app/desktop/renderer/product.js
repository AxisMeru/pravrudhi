'use strict';
// The bring-your-own-key surface: a user names a provider from the engine's registry, supplies a key, and
// this calls the three routes the engine classifies as the product's rather than the operator's
// (src/pravrudhi/api/roles.py: `/api/providers`, `/api/providers/{provider_id}/key`).
//
// The operator's instruction this follows: "the desktop app will not have access to any api/routing for any
// model provider, they have to bring theirs, configure it etc... but for admin it uses the one as the core
// pravrudhi". That boundary is the engine's own to keep (tests/test_byok_boundary.py resolves it by caller
// identity against the workspace store), so this surface carries no key of its own and no notion of which
// provider is the operator's core one — it only ever asks the engine, and only ever about the caller who asked.
//
// `add` both stores and validates in the one call the engine offers (`POST .../key` returns `validated` and a
// redacted `reason`); `validate` re-reads the registry to report whether a provider is still configured.
function createProviderSurface(api) {
  async function list() {
    return api.providers();
  }
  async function validate(id) {
    const provider = (await list()).find(p => p.id === id);
    if (!provider) throw new Error(`${id}: unknown provider.`);
    return provider;
  }
  async function add(id, key, baseUrl) {
    if (!key) throw new Error('A key is required.');
    return api.setProviderKey({id, body: baseUrl ? {key, base_url: baseUrl} : {key}});
  }
  async function remove(id) {
    return api.deleteProviderKey({id});
  }
  return Object.freeze({list, validate, add, remove});
}
module.exports = {createProviderSurface};
