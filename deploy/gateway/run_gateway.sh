#!/usr/bin/env bash
# Both hosted engines behind permanent public URLs, from this box (ADR-0051, addendum 3).
#
#   run_gateway.sh --setup    once: create the KV namespace, deploy both Workers, print their URLs
#   run_gateway.sh            serve: ensure each edition's engine container is up, open a cloudflared quick tunnel
#                             to it, register the tunnel URL in KV, and hold; on exit the tunnels close and the
#                             containers stay (they hold state and cost nothing idle).
#
# Everything secret lives in ~/.config/pravrudhi/*.env, never in this file or in the repository:
#   cloudflare.env  CLOUDFLARE_API_TOKEN (Workers Scripts:Edit, Workers KV Storage:Edit), CLOUDFLARE_ACCOUNT_ID,
#                   CF_KV_ID (written by --setup)
#   gateway.env     PRAVRUDHI_ADMINS (the operator's Supabase account, Studio admits only this),
#                   PRAVRUDHI_VERSION (the release both containers run), optional STUDIO_ORIGIN / PRODUCT_ORIGIN
#   supabase.env    SUPABASE_URL (token verification)
#   chat.env        the vendor key the engine routes to (a cost the operator has accepted)
#   github.env      GITHUB_TOKEN, only to fetch release wheels past the anonymous rate limit when building
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONF="${PRAVRUDHI_CONF:-$HOME/.config/pravrudhi}"
STATE="${PRAVRUDHI_HOSTED_STATE:-$HOME/.local/share/pravrudhi-hosted}"
LOGS="${PRAVRUDHI_GATEWAY_LOGS:-$STATE/logs}"
mkdir -p "$STATE" "$LOGS"

load() { [ -f "$CONF/$1" ] && { set -a; . "$CONF/$1"; set +a; } || { echo "missing $CONF/$1" >&2; return 1; }; }
load cloudflare.env; load gateway.env; load supabase.env
: "${CLOUDFLARE_API_TOKEN:?}" "${CLOUDFLARE_ACCOUNT_ID:?}" "${PRAVRUDHI_ADMINS:?}" "${PRAVRUDHI_VERSION:?}" "${SUPABASE_URL:?}"
STUDIO_ORIGIN="${STUDIO_ORIGIN:-https://pravrudhi.vercel.app}"
PRODUCT_ORIGIN="${PRODUCT_ORIGIN:-https://pravrudhi-app.vercel.app}"
API="https://api.cloudflare.com/client/v4/accounts/$CLOUDFLARE_ACCOUNT_ID"
auth=(-H "Authorization: Bearer $CLOUDFLARE_API_TOKEN")

port_of()   { case "$1" in studio) echo 8771;; product) echo 8772;; esac; }
origin_of() { case "$1" in studio) echo "$STUDIO_ORIGIN";; product) echo "$PRODUCT_ORIGIN";; esac; }

verify_token() {
  local status
  status=$(curl -s -H "Authorization: Bearer $CLOUDFLARE_API_TOKEN" "$API/tokens/verify" \
    | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["result"]["status"] if d.get("success") else "invalid: " + "; ".join(e["message"] for e in d.get("errors", [])))')
  [ "$status" = "active" ] || { echo "Cloudflare token in $CONF/cloudflare.env is not usable ($status). It must be an API token (user- or account-owned; not the Global API Key) with Workers Scripts:Edit and Workers KV Storage:Edit on the account." >&2; exit 1; }
}

setup() {
  verify_token
  if [ -z "${CF_KV_ID:-}" ]; then
    CF_KV_ID=$(curl -sf "${auth[@]}" -H 'content-type: application/json' -X POST "$API/storage/kv/namespaces" \
      --data '{"title":"pravrudhi-gateway"}' | python3 -c 'import json,sys; print(json.load(sys.stdin)["result"]["id"])')
    printf '\nCF_KV_ID=%s\n' "$CF_KV_ID" >> "$CONF/cloudflare.env"
    echo "created KV namespace $CF_KV_ID"
  fi
  work=$(mktemp -d); cp "$HERE/worker.js" "$work/"; sed "s/KV_NAMESPACE_ID/$CF_KV_ID/g" "$HERE/wrangler.toml" > "$work/wrangler.toml"
  for edition in studio product; do
    (cd "$work" && CLOUDFLARE_API_TOKEN="$CLOUDFLARE_API_TOKEN" CLOUDFLARE_ACCOUNT_ID="$CLOUDFLARE_ACCOUNT_ID" \
      npx --yes wrangler@4 deploy --env "$edition" 2>&1 | tee "$LOGS/deploy-$edition.log" | grep -E "https://|Error" || true)
  done
  rm -rf "$work"
  echo "set NEXT_PUBLIC_API_BASE on each Vercel project to the Worker URL printed above, then run this script without --setup"
}

ensure_image() {
  docker image inspect "pravrudhi-engine:$PRAVRUDHI_VERSION" >/dev/null 2>&1 && return
  [ -f "$CONF/github-axismeru.env" ] && { set -a; . "$CONF/github-axismeru.env"; set +a; }
  GITHUB_TOKEN="${GITHUB_TOKEN:-${PRAVRUDHI_GITHUB_TOKEN_AXISMERU:-}}"
  docker build --build-arg "PRAVRUDHI_VERSION=$PRAVRUDHI_VERSION" ${GITHUB_TOKEN:+--build-arg GITHUB_TOKEN=$GITHUB_TOKEN} \
    -t "pravrudhi-engine:$PRAVRUDHI_VERSION" "$HERE/../docker"
}

ensure_engine() {
  local edition=$1 name="pravrudhi-engine-$1" port; port=$(port_of "$1")
  if docker ps --format '{{.Names}} {{.Image}}' | grep -q "^$name pravrudhi-engine:$PRAVRUDHI_VERSION$"; then return; fi
  docker rm -f "$name" >/dev/null 2>&1 || true
  mkdir -p "$STATE/$edition"
  docker run -d --name "$name" --restart unless-stopped --memory 3g \
    -p "127.0.0.1:$port:8765" -v "$STATE/$edition:/data" \
    --env-file "$CONF/chat.env" \
    -e PRAVRUDHI_EDITION="$edition" -e SUPABASE_URL="$SUPABASE_URL" \
    -e PRAVRUDHI_ALLOWED_ORIGINS="$(origin_of "$edition")" \
    ${edition/studio/-e PRAVRUDHI_ADMINS=$PRAVRUDHI_ADMINS} ${edition/product/} \
    "pravrudhi-engine:$PRAVRUDHI_VERSION" >/dev/null
  for _ in $(seq 1 60); do curl -sf "http://127.0.0.1:$port/api/health" >/dev/null 2>&1 && return; sleep 1; done
  echo "engine $edition did not answer on $port" >&2; docker logs --tail 20 "$name" >&2; exit 1
}

PIDS=()
tunnel() {
  local edition=$1 port log url; port=$(port_of "$1"); log="$LOGS/tunnel-$edition.log"
  : > "$log"; cloudflared tunnel --url "http://127.0.0.1:$port" >"$log" 2>&1 & PIDS+=($!)
  for _ in $(seq 1 30); do url=$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' "$log" | head -1); [ -n "$url" ] && break; sleep 1; done
  [ -n "$url" ] || { echo "no tunnel url for $edition" >&2; exit 1; }
  grep -q "Registered tunnel connection" "$log" || sleep 5
  curl -sf "${auth[@]}" -X PUT "$API/storage/kv/namespaces/$CF_KV_ID/values/engine_url_$edition" --data "$url" >/dev/null
  echo "$edition: $url -> KV engine_url_$edition"
}

case "${1:-}" in
  --setup) setup; exit 0;;
  "") ;;
  *) echo "usage: $0 [--setup]" >&2; exit 2;;
esac
: "${CF_KV_ID:?run --setup first}"
trap 'kill "${PIDS[@]}" 2>/dev/null || true' EXIT INT TERM
ensure_image
for edition in studio product; do ensure_engine "$edition"; tunnel "$edition"; done
echo "gateway up; both engines registered"
wait
