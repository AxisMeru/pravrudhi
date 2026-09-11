'use strict';
// Credentials are intentionally session-only: no renderer response, disk or child environment.
function createAuth({url = process.env.SUPABASE_URL, key = process.env.SUPABASE_ANON_KEY,
  fetchFn = fetch, now = Date.now} = {}) {
  let session = null, refreshing = null, generation = 0;
  function configured() {
    try { return new URL(url).protocol === 'https:' && Boolean(key); } catch { return false; }
  }
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
      session = next;
      return {user:session.user};
    },
    signOut() { generation++; session = null; },
    async token() {
      if (!session) return null;
      if (session.expires <= now() + 60000) {
        if (!refreshing) {
          const ticket = generation;
          refreshing = exchange('refresh_token', {refresh_token:session.refresh}).then(next=>{
            if (ticket === generation) session = next;
          }).catch(error=>{ if (ticket === generation) session = null; throw error; }).finally(()=>{refreshing = null;});
        }
        await refreshing;
      }
      return session?.access || null;
    }
  });
}
module.exports = {createAuth};
