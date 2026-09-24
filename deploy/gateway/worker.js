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
//
// RunPod mode (UPSTREAM_KIND="runpod"): the engine is behind RunPod's LB gateway which requires
// Authorization: Bearer <RUNPOD_API_KEY>. This creates an auth collision: the engine also needs the user's
// Supabase token. The Worker moves the caller's Authorization to X-Pravrudhi-Authorization (the engine's
// custom identity header) and sets Authorization to Bearer <RUNPOD_API_KEY> for the gateway.

// KV is read at most once per BACKEND_TTL_MS per isolate, not once per request: the free tier counts every
// KV read against a daily allowance (the account reached half of it on 2026-09-12 from this lookup alone), and
// the tunnel URL changes only when run_gateway.sh restarts a tunnel. A failed proxy attempt drops the cached
// URL so the next request re-reads KV and finds the replacement tunnel within one request instead of a TTL.
const BACKEND_TTL_MS = 60_000;
let cached = { url: null, at: 0 };

async function backendFor(env) {
  const now = Date.now();
  if (cached.url && now - cached.at < BACKEND_TTL_MS) return cached.url;
  const url = await env.KV.get(env.ENGINE_KEY, { cacheTtl: 60 });
  cached = { url, at: now };
  return url;
}

function isRunPodMode(env) {
  return env.UPSTREAM_KIND === "runpod" && env.RUNPOD_API_KEY;
}

export default {
  async fetch(request, env) {
    const backend = await backendFor(env);
    if (!backend) {
      return new Response(JSON.stringify({ error: "engine not registered", edition: env.EDITION }), {
        status: 503, headers: { "content-type": "application/json" },
      });
    }
    const url = new URL(request.url);
    const target = backend.replace(/\/$/, "") + url.pathname + url.search;
    const headers = new Headers(request.headers);
    headers.delete("host");

    // Ensure User-Agent is set: RunPod's Cloudflare proxy returns 403 (error 1010) to urllib's default
    if (!headers.get("user-agent")) {
      headers.set("user-agent", `pravrudhi/${env.PRAVRUDHI_VERSION || "unknown"}`);
    }

    // RunPod mode: move user's Authorization to custom header, inject RunPod key
    if (isRunPodMode(env)) {
      const userAuth = headers.get("authorization");
      if (userAuth) {
        headers.set("x-pravrudhi-authorization", userAuth);
      }
      headers.set("authorization", `Bearer ${env.RUNPOD_API_KEY}`);
    }

    try {
      const resp = await fetch(target, {
        method: request.method,
        headers,
        body: request.method === "GET" || request.method === "HEAD" ? undefined : request.body,
        redirect: "manual",
      });
      if (resp.status >= 502 && resp.status <= 530) cached = { url: null, at: 0 };  // tunnel gone: re-read next time
      return new Response(resp.body, { status: resp.status, headers: resp.headers });
    } catch (e) {
      cached = { url: null, at: 0 };
      return new Response(JSON.stringify({ error: "engine unreachable", detail: String(e) }), {
        status: 502, headers: { "content-type": "application/json" },
      });
    }
  },
};
