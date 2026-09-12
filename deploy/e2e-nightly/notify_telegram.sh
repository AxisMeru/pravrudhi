# Sourced, not executed: defines notify_telegram() for run_e2e_nightly.sh and run_desktop_nightly.sh to call on
# a non-zero status. Uses the operator's own bot credential (~/.config/pravrudhi/telegram.env,
# TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID — the same one pravrudhi-heartbeat.service already loads via
# EnvironmentFile, never a new bot) over the same wire call application/reach.py's Sink makes: a plain
# POST .../sendMessage. Deliberately without reach.py's MarkdownV2 escaping, retries or delivery-id dedup —
# those exist for the engine's own noisier internal event stream (a night finishing, a job accepted), not a
# single rare "a nightly failed" alert, so plain text keeps this the smaller of the two things it could have
# been (see W12's option C, not built for exactly this reason).
#
# A send failure (missing credential, network, a bad chat id) is swallowed and merely logged to stderr: a
# nightly's own PASS/FAIL result and exit code must never be masked by this notification side-channel failing.
notify_telegram() {
  local text="$1"
  local token="${TELEGRAM_BOT_TOKEN:-}"
  local chat_id="${TELEGRAM_CHAT_ID:-}"
  if [ -z "$token" ] || [ -z "$chat_id" ]; then
    echo "notify_telegram: TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID not set, not sending" >&2
    return 0
  fi
  if ! curl -s -o /dev/null -w '%{http_code}' -X POST "https://api.telegram.org/bot${token}/sendMessage" \
    --data-urlencode "chat_id=${chat_id}" \
    --data-urlencode "text=${text}" | grep -q '^200$'; then
    echo "notify_telegram: send failed or was refused" >&2
  fi
  return 0
}
