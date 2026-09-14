#!/usr/bin/env bash
# Install the developer-channel units for the checkout this script lives in, then start the app and the timer.
set -eu
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
UNITS="$HOME/.config/systemd/user"
mkdir -p "$UNITS"
for f in pravrudhi-app.service pravrudhi-update.service pravrudhi-update.timer \
         pravrudhi-heartbeat.service pravrudhi-heartbeat.timer \
         pravrudhi-publish.service pravrudhi-publish.timer \
         pravrudhi-inbox-sweep.service pravrudhi-inbox-sweep.timer; do
  sed "s#@ROOT@#$ROOT#g" "$ROOT/deploy/systemd/$f" > "$UNITS/$f"
done
chmod +x "$ROOT/deploy/systemd/dev-update.sh"
# The service inherits none of the login shell's PATH, so agents installed under nvm or ~/.local (claude, codex)
# read as "not installed" in the app's survey. Record the installing shell's PATH as a drop-in.
mkdir -p "$UNITS/pravrudhi-app.service.d"
printf '[Service]\nEnvironment="PATH=%s"\n' "$PATH" > "$UNITS/pravrudhi-app.service.d/path.conf"
systemctl --user daemon-reload
systemctl --user enable --now pravrudhi-app.service pravrudhi-update.timer pravrudhi-heartbeat.timer \
  pravrudhi-publish.timer pravrudhi-inbox-sweep.timer
systemctl --user list-timers "pravrudhi-*" --no-pager
