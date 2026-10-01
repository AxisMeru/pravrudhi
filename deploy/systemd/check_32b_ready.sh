#!/usr/bin/env bash
# Durable, host-level watchdog for the always-on 32B second-judge endpoint (nyaya-judge-32b-anydc,
# 7j7ipedmwi8z1w), NOT a session cron -- Lead-2, 2026-09-27 second-judge-availability Phase-1 cutover.
# Runs every 30 minutes via pravrudhi-32b-watchdog.timer. Queries the RunPod REST API directly for ready
# workers; alerts via the existing Telegram notifier (deploy/e2e-nightly/notify_telegram.sh's function,
# sourced not duplicated) if zero are ready. Never prints the API key -- read from
# ~/.config/pravrudhi/runpod-axismeru.env via EnvironmentFile in the unit, referenced here only as a var.
set -euo pipefail

ENDPOINT_ID="7j7ipedmwi8z1w"
: "${RUNPOD_API_KEY_AXISMERU:?RUNPOD_API_KEY_AXISMERU must be set, via the units EnvironmentFile}"

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
# shellcheck disable=SC1091
source "$ROOT/deploy/e2e-nightly/notify_telegram.sh"
# notify_telegram() reads TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID from the environment (also EnvironmentFile'd
# in the unit, from telegram.env) -- swallows its own send failures, never masks this script's own logic.

RESPONSE="$(curl -sS --fail-with-body \
  -H "Authorization: Bearer ${RUNPOD_API_KEY_AXISMERU}" \
  "https://api.runpod.ai/v2/${ENDPOINT_ID}/health" 2>&1)" || {
  echo "check_32b_ready: RunPod health API call failed: ${RESPONSE}" >&2
  notify_telegram "32B second-judge watchdog: RunPod health API call FAILED for endpoint ${ENDPOINT_ID}. Check manually."
  exit 1
}

READY="$(echo "$RESPONSE" | python3 -c "
import json, sys
d = json.load(sys.stdin)
w = d.get('workers', {})
print(w.get('ready', 0) + w.get('running', 0) + w.get('idle', 0))
")"

echo "check_32b_ready: $(date -u +%FT%TZ) ready+running+idle=${READY}"

if [ "${READY}" -eq 0 ]; then
  notify_telegram "32B second-judge watchdog: 0 ready workers on endpoint ${ENDPOINT_ID} (nyaya-judge-32b-anydc). min=1 is set -- this means the worker died, is stuck initializing, or RunPod has no AMPERE_80 capacity right now. Production PROOF verdicts will fall back to REFER/second_judge_unavailable until this recovers."
fi
