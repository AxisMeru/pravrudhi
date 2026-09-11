// Pravrudhi's permanent front door for one hosted engine (ADR-0051, addendum 3).
//
// The engine runs behind an ephemeral cloudflared quick tunnel whose hostname changes on every restart.
// run_gateway.sh writes the current tunnel URL into KV under ENGINE_KEY; this Worker reads it on each request
// and proxies, so the public *.workers.dev URL the web app is built against never changes. One Worker per
// edition: deploy twice (wrangler.toml `[env.studio]` and `[env.product]`), each with its own KV key and origin.
//
// The Worker adds nothing to the trust model. Identity is the engine's (PRAVRUDHI_AUTH=required verifies the
// Supabase bearer token); CORS is the engine's (PRAVRUDHI_ALLOWED_ORIGINS). The Worker forwards headers and
// body untouched in both directions, and answers 503 when no tunnel is registered.

export default {
  async fetch(request, env) {
    const backend = await env.KV.get(env.ENGINE_KEY);
    if (!backend) {
      return new Response(JSON.stringify({ error: "engine not registered", edition: env.EDITION }), {
        status: 503, headers: { "content-type": "application/json" },
      });
    }
    const url = new URL(request.url);
    const target = backend.replace(/\/$/, "") + url.pathname + url.search;
    const headers = new Headers(request.headers);
    headers.delete("host");
    try {
      const resp = await fetch(target, {
        method: request.method,
        headers,
        body: request.method === "GET" || request.method === "HEAD" ? undefined : request.body,
        redirect: "manual",
      });
      return new Response(resp.body, { status: resp.status, headers: resp.headers });
    } catch (e) {
      return new Response(JSON.stringify({ error: "engine unreachable", detail: String(e) }), {
        status: 502, headers: { "content-type": "application/json" },
      });
    }
  },
};
