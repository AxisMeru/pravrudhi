# Gateway: permanent public URLs for the hosted engines

Vercel serves the two web apps and cannot run an engine. Until a cloud host is chosen, both editions' engines run
on the operator's box, each behind a Cloudflare quick tunnel whose hostname changes on every restart, and each
web app is built against a Worker URL that never does (ADR-0051, addendum 3; the pattern is the one
`game-llm`'s gateway proved). The Worker reads the current tunnel URL from KV and proxies; nothing else.

```
pravrudhi.vercel.app ──▶ pravrudhi-studio.<acct>.workers.dev ──KV──▶ <random>.trycloudflare.com ──▶ :8771 studio engine
pravrudhi-app.vercel.app ──▶ pravrudhi-app.<acct>.workers.dev ──KV──▶ <random>.trycloudflare.com ──▶ :8772 product engine
```

Trust lives in the engine, not the edge: `PRAVRUDHI_AUTH=required` verifies every request's Supabase token
(`api/identity.py` `RequireIdentity`, every `/api` path but the health check), `PRAVRUDHI_ADMINS` makes Studio
admit only the operator, `PRAVRUDHI_ALLOWED_ORIGINS` names the one web origin. The Worker forwards headers
untouched.

## Files

| file | role |
|---|---|
| `worker.js` | the proxy; one script deployed twice |
| `wrangler.toml` | the two deployments (`--env studio`, `--env product`) and their KV binding |
| `run_gateway.sh` | `--setup` once (KV namespace, Workers); then serve: containers, tunnels, KV registration |
| `pravrudhi-gateway.service` | the systemd user unit that keeps the serve loop up |

## Secrets, and where they live

Never in the repository. `~/.config/pravrudhi/`:

* `cloudflare.env`: `CLOUDFLARE_API_TOKEN` (permissions: Workers Scripts Edit, Workers KV Storage Edit),
  `CLOUDFLARE_ACCOUNT_ID`; `--setup` appends `CF_KV_ID`.
* `gateway.env`: `PRAVRUDHI_ADMINS` (operator's account email), `PRAVRUDHI_VERSION` (the release to run).
* `supabase.env`, `chat.env`, `github.env`: already present for the other units.

## Bring-up

```bash
deploy/gateway/run_gateway.sh --setup          # prints the two Worker URLs
# set NEXT_PUBLIC_API_BASE=<Worker URL> on each Vercel project (vercel env add, from a scratch clone), push to deploy
cp deploy/gateway/pravrudhi-gateway.service ~/.config/systemd/user/ && systemctl --user enable --now pravrudhi-gateway
journalctl --user -u pravrudhi-gateway -f
```

Engine state is under `~/.local/share/pravrudhi-hosted/<edition>`; logs under `.../logs`. A new release is
`PRAVRUDHI_VERSION` in `gateway.env` and a restart: the script rebuilds the image and replaces the containers.

## Known limits

* A quick tunnel is best effort: Cloudflare may drop it, the unit restarts and re-registers. Named tunnels on a
  domain remove that; the operator has no domain assigned yet.
* The local resolver on this box negatively caches a fresh `trycloudflare.com` name for a while; the Worker
  resolves at the edge and is unaffected, but a local `curl` test may need `--resolve` via 1.1.1.1.
* The product's engine on the operator's workstation is the operator's explicit choice for now; the
  container recipe in `deploy/docker` is the same one a cloud host would run.
