'use strict';
// The access token never leaves this module: not to the renderer, disk or a child process. A caller that wants
// to restore a session after a restart is handed only a refresh token and the signed-in user, through
// `onSession`, and gives it back through `restore` — never the access token itself.
const crypto = require('node:crypto');
const {OAUTH_DEFAULT_PROVIDER, PKCE_GRANT_TYPE, PKCE_CHALLENGE_METHOD, PKCE_VERIFIER_BYTES, OAUTH_STATE_BYTES} = require('./oauth-constants');
function base64url(buffer) { return buffer.toString('base64').replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, ''); }
function createAuth({url = process.env.SUPABASE_URL, key = process.env.SUPABASE_ANON_KEY,
  fetchFn = fetch, now = Date.now, randomBytes = crypto.randomBytes,
  sha256 = data => crypto.createHash('sha256').update(data).digest(),
  redirectUri = null, onSession = () => {}} = {}) {
  let session = null, refreshing = null, generation = 0, pendingBrowserSignIn = null;
  function configured() {
    try { return new URL(url).protocol === 'https:' && Boolean(key); } catch { return false; }
  }
  // Handed to the caller on every session change so it can persist (or drop) enough to restore the session on
  // the next launch; never the access token, which this module keeps to itself.
  function save() { onSession(session ? {refresh: session.refresh, user: session.user} : null); }
  async function exchange(grant, body) {
    if (!configured()) throw new Error('Configure SUPABASE_URL and SUPABASE_ANON_KEY in the desktop launch environment.');
    try {
      const response = await fetchFn(`${url.replace(/\/$/, '')}/auth/v1/token?grant_type=${grant}`, {
        method:'POST', redirect:'error', signal:AbortSignal.timeout(15000),
        headers:{apikey:key, 'Content-Type':'application/json'}, body:JSON.stringify(body)
      });
      if (!response.ok) throw new Error();
      const value = await response.json();
      if (!value.access_token || !value.refresh_token || !value.user?.id) throw new Error();
      return {access:value.access_token, refresh:value.refresh_token, expires:now() + Number(value.expires_in || 3600)*1000,
        user:{id:value.user.id, email:value.user.email || ''}};
    } catch { throw new Error('Sign-in failed or expired. Please sign in again.'); }
  }
  return Object.freeze({
    status:()=>({configured:configured(), user:session?.user || null}),
    async signIn(input) {
      if (typeof input?.email !== 'string' || typeof input?.password !== 'string' || input.email.length > 320 || input.password.length > 4096) throw new Error('Enter your email and password.');
      const ticket = ++generation;
      session = null;
      const next = await exchange('password', {email:input.email, password:input.password});
      if (ticket !== generation) throw new Error('Sign-in cancelled.');
      session = next; save();
      return {user:session.user};
    },
    // Opens the way to a Supabase OAuth authorization-code + PKCE flow: the caller (main.js) opens `url` in the
    // system browser and, when it redirects back to `redirectUri`, hands the whole callback URL to
    // `completeBrowserSignIn`. The verifier that proves this flow's identity is kept here, never in the URL.
    async beginBrowserSignIn(provider = OAUTH_DEFAULT_PROVIDER) {
      if (!configured()) throw new Error('Configure SUPABASE_URL and SUPABASE_ANON_KEY in the desktop launch environment.');
      if (!redirectUri) throw new Error('This build has no OAuth redirect URI configured.');
      const verifier = base64url(randomBytes(PKCE_VERIFIER_BYTES));
      const state = base64url(randomBytes(OAUTH_STATE_BYTES));
      pendingBrowserSignIn = {verifier, state};
      const authorize = new URL(`${url.replace(/\/$/, '')}/auth/v1/authorize`);
      authorize.searchParams.set('provider', provider);
      authorize.searchParams.set('code_challenge', base64url(sha256(verifier)));
      authorize.searchParams.set('code_challenge_method', PKCE_CHALLENGE_METHOD);
      authorize.searchParams.set('redirect_to', redirectUri);
      authorize.searchParams.set('state', state);
      return {url: authorize.href};
    },
    async completeBrowserSignIn(redirectUrl) {
      const pending = pendingBrowserSignIn;
      pendingBrowserSignIn = null;
      let parsed; try { parsed = new URL(redirectUrl); } catch { parsed = null; }
      const code = parsed?.searchParams.get('code');
      const state = parsed?.searchParams.get('state');
      if (!pending || !code || state !== pending.state) throw new Error('Sign-in failed or expired. Please sign in again.');
      const ticket = ++generation;
      session = null;
      const next = await exchange(PKCE_GRANT_TYPE, {auth_code:code, code_verifier:pending.verifier});
      if (ticket !== generation) throw new Error('Sign-in cancelled.');
      session = next; save();
      return {user:session.user};
    },
    // Hydrates a session saved by `onSession` on a previous launch. It is optimistic and unverified: `status()`
    // reports the restored user right away, but the session has no access token, so the first `token()` call
    // must refresh it before anything can actually use it — the same path an expiring session already takes.
    restore(persisted) {
      if (typeof persisted?.refresh !== 'string' || typeof persisted?.user?.id !== 'string') return;
      generation++;
      session = {access:null, refresh:persisted.refresh, expires:0, user:{id:persisted.user.id, email:persisted.user.email || ''}};
    },
    signOut() { generation++; session = null; save(); },
    async token() {
      if (!session) return null;
      if (session.expires <= now() + 60000) {
        if (!refreshing) {
          const ticket = generation;
          refreshing = exchange('refresh_token', {refresh_token:session.refresh}).then(next=>{
            if (ticket === generation) { session = next; save(); }
          }).catch(error=>{ if (ticket === generation) { session = null; save(); } throw error; }).finally(()=>{refreshing = null;});
        }
        await refreshing;
      }
      return session?.access || null;
    }
  });
}
module.exports = {createAuth};
