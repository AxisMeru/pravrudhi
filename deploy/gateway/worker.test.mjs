// Run: node deploy/gateway/worker.test.mjs  (no dependencies; stubs fetch and KV)
import w from "./worker.js";
let proxied = [];
globalThis.fetch = async (u, o) => { proxied.push(o.method + " " + u); return new Response("ok", {status: 200}); };
const env = { KV: { get: async () => "https://engine.example" }, ENGINE_KEY: "k", EDITION: "product" };
const cases = [
 ["POST","/api/runs",403],["PUT","/api/update/config",403],["PATCH","/api/runs/1",403],["DELETE","/api/runs/1",403],
 ["POST","/api/%72uns",403],["PUT","/api/UPDATE/config",403],["POST","/api/runs/abc/stop",403],["POST","/api/update/apply",403],
 ["POST","/api/%zz/runs",403],["GET","/api/runs",200],["GET","/api/update/config",200],["OPTIONS","/api/runs",200],
 ["GET","/api/health",200],["POST","/api/v1/analyse-facts",200],["POST","/api/chat",200],["GET","/api/app-token",200],["POST","/api/memory/notes",200],
];
let bad = 0;
for (const [m,p,exp] of cases) {
  const r = await w.fetch(new Request("https://x.example"+p, {method:m, body: m==="GET"||m==="OPTIONS"?undefined:"{}"}), env);
  const ok = r.status === exp; if(!ok) bad++;
  console.log(ok?"ok  ":"FAIL", m, p, r.status, exp===403? await r.clone().text():"");
}

// caller-supplied x-pravrudhi-client-ip must not reach the engine (#248)
{
  let seen = null;
  globalThis.fetch = async (u, o) => { seen = o.headers; return new Response("ok", {status: 200}); };
  await w.fetch(new Request("https://x.example/api/health", {headers: {"x-pravrudhi-client-ip": "1.2.3.4"}}), env);
  const ok = seen && !seen.has("x-pravrudhi-client-ip");
  console.log(ok ? "ok  " : "FAIL", "client-ip header stripped");
  if (!ok) bad++;
}
console.log("proxied:", proxied.length, "bad:", bad); process.exit(bad?1:0);
