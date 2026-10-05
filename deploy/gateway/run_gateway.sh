#!/usr/bin/env bash
# Both hosted engines behind permanent public URLs, from this box (ADR-0051, addendum 3).
#
#   run_gateway.sh --setup    once: create the KV namespace, deploy both Workers, print their URLs
#   run_gateway.sh            serve: ensure each edition's engine container is up, open a cloudflared quick tunnel
#                             to it, register the tunnel URL in KV, and hold; on exit the tunnels close and the
#                             containers stay (they hold state and cost nothing idle).
#   run_gateway.sh --rebuild  serve, but force a fresh `docker build` even if an image tagged
#                             pravrudhi-engine:$PRAVRUDHI_VERSION already exists -- an ordinary version
#                             bump doesn't need this (the tag changes), but picking up a nyaya score
#                             binary that was added/changed without a version bump does.
#
# Everything secret lives in ~/.config/pravrudhi/*.env, never in this file or in the repository:
#   cloudflare.env  CLOUDFLARE_API_TOKEN (Workers Scripts:Edit, Workers KV Storage:Edit), CLOUDFLARE_ACCOUNT_ID,
#                   CF_KV_ID (written by --setup)
#   gateway.env     PRAVRUDHI_ADMINS (the operator's Supabase account, Studio admits only this),
#                   PRAVRUDHI_VERSION (the release both containers run), STUDIO_ROOT (the real Studio root the
#                   hosted Studio engine serves; default a fresh root), optional STUDIO_ORIGIN / PRODUCT_ORIGIN
#                   NYAYA_HOUSE_JUDGE_MODEL (and NYAYA_SECOND_JUDGE_MODEL when a second judge is configured):
#                   the served judge model ids (nyaya-judge-4b / judge32b). REQUIRED for the engine containers:
#                   a product or studio engine refuses to load its judge config with no model named (#237)
#                   optional NYAYA_HOUSE_JUDGE_BASE_URL + NYAYA_JUDGE_NETWORK: the element-judge server the
#                   /api/v1/analyse-facts agent calls, reached by container name on a shared docker network
#                   (e.g. http://vllm-judge:8000/v1 on network nyaya-judge) -- the host's 127.0.0.1 is not
#                   reachable from an engine container, and docker0 -> host is firewalled on this box
#                   optional STUDIO_HOLD_KV=1: start the Studio tunnel but do NOT write engine_url_studio to KV (the URL is
#                   only logged), so a blank key kept as containment survives a gateway restart. The flag is read from the
#                   gateway's environment on EVERY start: keep it in gateway.env (or an `Environment=STUDIO_HOLD_KV=1` line
#                   in the systemd unit, see pravrudhi-gateway.service), and add it to the restart checklist, or a restart
#                   silently drops the hold and the next start writes engine_url_studio again
#                   optional PRODUCT_DEMO_ANON_PATHS: comma list passed to the PRODUCT engine only as
#                   PRAVRUDHI_DEMO_ANON_PATHS (anonymous demo routes; the engine refuses any outside its fixed set)
#                   optional PRODUCT_UPSTREAM=runpod: the product edition has cut over to RunPod serverless
#                   (deploy/gateway/wrangler.toml [env.product]) and reads its live backend from
#                   `engine_url_product` directly, not from this box's tunnel -- set this so a restart of this
#                   loop records the 5090's own tunnel URL under `engine_url_product_rollback` instead of
#                   clobbering the cutover (2026-09-25 incident, root-caused by Lead-2)
#   supabase.env    SUPABASE_URL (token verification)
#   chat.env        the vendor key the engine routes to (a cost the operator has accepted)
#   github.env      GITHUB_TOKEN, only to fetch release wheels past the anonymous rate limit when building
#
# NYAYA_SCORE_BIN_SRC (plain env, not a *.env file -- not a secret): where ensure_image() looks for the
# compiled prabhasa-nyaya `score` binary to bake into the image, default $HOME/projects/prabhasa-nyaya/
# lean/.lake/build/bin/score (this host's sibling checkout). Absent -> the image builds without it and
# every Lean-checker route answers 503 (deploy/docker/README.md).
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
  # An image tag that already exists is skipped -- correct for an ordinary version bump, wrong for
  # "the same version but the nyaya binary just got added/changed" (2026-09-23 incident: every prior
  # image silently ran without it). REBUILD_IMAGE (set by the --rebuild flag) forces past that skip.
  if [ -z "${REBUILD_IMAGE:-}" ] && docker image inspect "pravrudhi-engine:$PRAVRUDHI_VERSION" >/dev/null 2>&1; then
    return
  fi
  [ -f "$CONF/github-axismeru.env" ] && { set -a; . "$CONF/github-axismeru.env"; set +a; }
  GITHUB_TOKEN="${GITHUB_TOKEN:-${PRAVRUDHI_GITHUB_TOKEN_AXISMERU:-}}"

  # prabhasa-nyaya's compiled Lean score binary: a temp copy of deploy/docker (never the real
  # directory -- this must never leave stray build state behind in the repo), with the binary copied
  # in if the source exists. NYAYA_SCORE_BIN_SRC overrides the default sibling-checkout path.
  local score_src="${NYAYA_SCORE_BIN_SRC:-$HOME/projects/prabhasa-nyaya/lean/.lake/build/bin/score}"
  local build_ctx; build_ctx="$(mktemp -d)"
  cp -r "$HERE/../docker/." "$build_ctx/"
  # The pin is the Dockerfile's NYAYA_SCORE_SHA256 default (or an explicit NYAYA_SCORE_SHA256 env override);
  # never the source file's own sha, which would make the build-time check vacuous.
  local sha_arg=()
  [ -n "${NYAYA_SCORE_SHA256:-}" ] && sha_arg=(--build-arg "NYAYA_SCORE_SHA256=$NYAYA_SCORE_SHA256")
  if [ -f "$score_src" ]; then
    local sha; sha="$(sha256sum "$score_src" | cut -d' ' -f1)"
    cp "$score_src" "$build_ctx/nyaya/score"
    echo "nyaya score binary: $score_src (sha256 $sha) -- including in the image; the build verifies it against the pin"
  else
    echo "nyaya score binary NOT FOUND at $score_src -- building WITHOUT it; every Lean-checker" >&2
    echo "route will answer 503 in this image until a redeploy includes it (see deploy/docker/README.md)" >&2
  fi

  # The token goes in via --secret, never --build-arg (2026-09-26 incident: a --build-arg is printed in
  # cleartext in the build log and in `docker history`; a --secret is read from a file at
  # /run/secrets/<id> inside the one RUN step that needs it, never recorded anywhere else). Written to a
  # 600 tmp file for the duration of this one build call and removed immediately after, success or fail.
  local secret_arg=() token_file=""
  if [ -n "$GITHUB_TOKEN" ]; then
    token_file="$(mktemp)"
    trap 'rm -f "$token_file"' RETURN
    umask 177
    printf '%s' "$GITHUB_TOKEN" > "$token_file"
    umask 022
    secret_arg=(--secret "id=github_token,src=$token_file")
  fi
  docker build --build-arg "PRAVRUDHI_VERSION=$PRAVRUDHI_VERSION" "${secret_arg[@]}" \
    "${sha_arg[@]}" -t "pravrudhi-engine:$PRAVRUDHI_VERSION" "$build_ctx"
  [ -n "$token_file" ] && rm -f "$token_file"
  rm -rf "$build_ctx"
}

# Studio admits only the operator, which is only true when the engine authenticates (PRAVRUDHI_AUTH=required): with
# authentication off every caller is the operator by construction. The image defaults to required, but the gateway's
# own environment (gateway.env) or the container env file (chat.env) could override that, so refuse to START a Studio
# container unless the value that would reach it is `required`. Called BEFORE the existing container is touched, so a
# refusal leaves what is running as it is.
studio_auth_guard() {
  local line v="${PRAVRUDHI_AUTH-required}"
  if [ -f "$CONF/chat.env" ]; then
    line="$(grep -E '^[[:space:]]*(export[[:space:]]+)?PRAVRUDHI_AUTH=' "$CONF/chat.env" | tail -1 || true)"
    if [ -n "$line" ]; then v="${line#*=}"; v="${v%\"}"; v="${v#\"}"; v="${v%\'}"; v="${v#\'}"; fi
  fi
  if [ "$v" != required ]; then
    echo "REFUSING to start the Studio container: PRAVRUDHI_AUTH would be '$v', not 'required' (gateway.env / chat.env)." >&2
    echo "With authentication off every caller is the operator. Fix the setting, then re-run." >&2
    return 1
  fi
}

# Read the running Studio container's PRAVRUDHI_AUTH back and require `required`. The value is asked twice, once
# more after a short wait, before the answer is trusted: a container that has only just started can fail the exec once.
studio_auth_readback() {
  local name=$1 v
  v="$(docker exec "$name" printenv PRAVRUDHI_AUTH 2>/dev/null || true)"
  if [ "$v" != required ]; then
    sleep "${STUDIO_READBACK_WAIT:-2}"
    v="$(docker exec "$name" printenv PRAVRUDHI_AUTH 2>/dev/null || true)"
  fi
  [ "$v" = required ]
}

ensure_engine() {
  local edition=$1 name="pravrudhi-engine-$1" port; port=$(port_of "$1")
  if docker ps --format '{{.Names}} {{.Image}}' | grep -q "^$name pravrudhi-engine:$PRAVRUDHI_VERSION$"; then
    # The right image is already running: a restart of this script must not skip the Studio check just because
    # nothing needed building. An open Studio is taken down and its tunnel is never registered (return 1).
    if [ "$edition" = studio ] && ! studio_auth_readback "$name"; then
      echo "STUDIO REFUSED: the running Studio container does not report PRAVRUDHI_AUTH=required; removing it." >&2
      docker rm -f "$name" >/dev/null 2>&1 || true
      return 1
    fi
    return 0
  fi
  if [ "$edition" = studio ]; then studio_auth_guard || return 1; fi
  docker rm -f "$name" >/dev/null 2>&1 || true
  # Studio's hosted engine serves THE Studio: the operator's real root, with its ledger, requests and the local
  # loop's work, not a fresh root of its own (operator, 2026-09-12: "earlier studio was on rsi...what happened").
  # The product's engine keeps its own root; its users have workspaces there. The Studio container runs as the
  # operator's uid so files it writes into the real root are the operator's, with a HOME the engine can use.
  local data="$STATE/$edition" extra=()
  if [ "$edition" = studio ]; then
    data="${STUDIO_ROOT:-$STATE/studio}"
    # Binds 0.0.0.0 in the container but is published only on 127.0.0.1 (-p below): say so, or the engine cannot see it.
    extra=(-e "PRAVRUDHI_AUTH=required" -e "PRAVRUDHI_STUDIO_LOOPBACK_ONLY=1" -e "PRAVRUDHI_ADMINS=$PRAVRUDHI_ADMINS" --user "$(id -u):$(id -g)" -e HOME=/tmp)   # Studio admits only the operator
  fi
  # The anonymous-demo allowance (identity.DEMO_ANON_CAPABLE) is for the product edition only: Studio stays login-only.
  if [ "$edition" = product ] && [ -n "${PRODUCT_DEMO_ANON_PATHS:-}" ]; then
    extra+=(-e "PRAVRUDHI_DEMO_ANON_PATHS=$PRODUCT_DEMO_ANON_PATHS")
  fi
  mkdir -p "$data"
  docker run -d --name "$name" --restart unless-stopped --memory 3g \
    -p "127.0.0.1:$port:8765" -v "$data:/data" \
    --env-file "$CONF/chat.env" \
    -e PRAVRUDHI_AUTH=required \
    -e PRAVRUDHI_EDITION="$edition" -e SUPABASE_URL="$SUPABASE_URL" \
    -e PRAVRUDHI_ALLOWED_ORIGINS="$(origin_of "$edition")" \
    ${NYAYA_HOUSE_JUDGE_BASE_URL:+-e "NYAYA_HOUSE_JUDGE_BASE_URL=$NYAYA_HOUSE_JUDGE_BASE_URL"} \
    ${NYAYA_HOUSE_JUDGE_MODEL:+-e "NYAYA_HOUSE_JUDGE_MODEL=$NYAYA_HOUSE_JUDGE_MODEL"} \
    ${NYAYA_SECOND_JUDGE_MODEL:+-e "NYAYA_SECOND_JUDGE_MODEL=$NYAYA_SECOND_JUDGE_MODEL"} \
    ${NYAYA_JUDGE_NETWORK:+--network "$NYAYA_JUDGE_NETWORK"} \
    "${extra[@]}" \
    "pravrudhi-engine:$PRAVRUDHI_VERSION" >/dev/null
  if [ "$edition" = studio ] && ! studio_auth_readback "$name"; then
    # Belt and braces: read back what the running container actually has, and take it down rather than serve Studio open.
    echo "STUDIO REFUSED: the Studio container does not report PRAVRUDHI_AUTH=required; removing it." >&2
    docker rm -f "$name" >/dev/null 2>&1 || true
    return 1
  fi
  for _ in $(seq 1 60); do curl -sf "http://127.0.0.1:$port/api/health" >/dev/null 2>&1 && return 0; sleep 1; done
  echo "engine $edition did not answer on $port" >&2; docker logs --tail 20 "$name" >&2; return 1
}

# Studio first, then the product. A Studio refusal or failure is logged loudly, its tunnel is never registered, and the
# product still comes up (its container and tunnel are not touched by Studio's problem); the function returns 1 so the
# caller exits non-zero at the end. A product failure still stops everything, as before.
bring_up_engines() {
  local studio_failed=""
  for edition in studio product; do
    if ensure_engine "$edition"; then
      tunnel "$edition"
    elif [ "$edition" = studio ]; then
      studio_failed=1
      echo "################ STUDIO NOT REGISTERED (see above); continuing with the product edition ################" >&2
    else
      exit 1
    fi
  done
  [ -z "$studio_failed" ]
}

PIDS=()
tunnel() {
  local edition=$1 port log url kv_key; port=$(port_of "$1"); log="$LOGS/tunnel-$edition.log"
  : > "$log"; cloudflared tunnel --url "http://127.0.0.1:$port" >"$log" 2>&1 & PIDS+=($!)
  for _ in $(seq 1 30); do url=$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' "$log" | head -1 || true); [ -n "$url" ] && break; sleep 1; done
  [ -n "$url" ] || { echo "no tunnel url for $edition" >&2; exit 1; }
  grep -q "Registered tunnel connection" "$log" || sleep 5
  # A product edition cut over to RunPod serverless (PRODUCT_UPSTREAM=runpod, deploy/gateway/wrangler.toml
  # [env.product]) reads its live backend from `engine_url_product`, not from this 5090 tunnel -- writing here
  # on every gateway restart silently clobbered that cutover (2026-09-25, root-caused by Lead-2). The 5090's
  # own tunnel URL still gets recorded, under a rollback-only key nothing reads unless the cutover is undone,
  # so restarting the local gateway loop can never again overwrite what production is actually pointed at.
  kv_key="engine_url_$edition"
  if [ "$edition" = "product" ] && [ "${PRODUCT_UPSTREAM:-}" = "runpod" ]; then
    kv_key="engine_url_product_rollback"
  fi
  # An operator who keeps `engine_url_studio` blank as a containment layer (Studio offline behind its Worker) can hold
  # it across a gateway restart: with STUDIO_HOLD_KV=1 the Studio tunnel is still started and its URL is logged, but
  # nothing is written to KV, so the Worker keeps answering 503. Only the exact value 1 holds; the default is unchanged,
  # and the product edition is never held.
  if [ "$edition" = studio ] && [ "${STUDIO_HOLD_KV:-}" = 1 ]; then
    echo "$edition: $url -> KV write HELD (STUDIO_HOLD_KV=1); $kv_key not written, the URL is only in this log"
    return 0
  fi
  curl -sf "${auth[@]}" -X PUT "$API/storage/kv/namespaces/$CF_KV_ID/values/$kv_key" --data "$url" >/dev/null
  echo "$edition: $url -> KV $kv_key"
}

REBUILD_IMAGE=""
case "${1:-}" in
  --setup) setup; exit 0;;
  --rebuild) REBUILD_IMAGE=1 ;;
  "") ;;
  *) echo "usage: $0 [--setup|--rebuild]" >&2; exit 2;;
esac
: "${CF_KV_ID:?run --setup first}"
trap 'kill "${PIDS[@]}" 2>/dev/null || true' EXIT INT TERM
ensure_image
STUDIO_FAILED=""
bring_up_engines || STUDIO_FAILED=1
if [ -n "$STUDIO_FAILED" ]; then
  echo "gateway up for the PRODUCT edition only; Studio was refused and is not registered" >&2
else
  echo "gateway up; both engines registered"
fi
# A quick tunnel can die on its own; its KV entry would then point at a hostname Cloudflare no longer knows (530,
# error 1033) for as long as the other tunnel lived. Exit on the first death so systemd restarts the whole serve
# loop and re-registers both.
wait -n
echo "a tunnel exited; restarting to re-register" >&2
exit 1  # also the exit status when Studio was refused: a non-zero end is what tells systemd and the operator
