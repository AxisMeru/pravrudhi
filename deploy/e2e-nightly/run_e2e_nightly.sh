#!/usr/bin/env bash
# Nightly signed-in check of both hosted doors (pravrudhi-e2e-nightly.service). E2E_EMAIL/E2E_PASSWORD arrive
# through the unit's EnvironmentFile (~/.config/pravrudhi/e2e.env) and are read only by Playwright's own test
# code, via process.env — this script never reads, echoes, or logs either value, so no password leaves this box.
#
# Two projects, one in each repository's frontend: pravrudhi-app's `live-chromium` (e2e/live.spec.ts) is the
# product's own signed-in check — every nav page renders, plus one real note written and removed. pravrudhi's
# `live-chromium` (e2e/live-admin-boundary.spec.ts) proves PRAVRUDHI_ADMINS actually refuses this same,
# deliberately non-admin account on the live Studio engine, not just in roles.py's own unit tests.
set -uo pipefail

# shellcheck source=notify_telegram.sh
source "$(dirname "${BASH_SOURCE[0]}")/notify_telegram.sh"

REPORT_DIR="$HOME/.local/share/pravrudhi-hosted/e2e"
mkdir -p "$REPORT_DIR"
STAMP="$(date -u +%Y-%m-%dT%H-%M-%SZ)"
REPORT="$REPORT_DIR/$STAMP.log"

PRODUCT_DIR="$HOME/projects/pravrudhi-app/frontend"
STUDIO_DIR="$HOME/projects/pravrudhi/app/frontend"

status=0
{
  echo "=== pravrudhi-e2e-nightly: $STAMP ==="
  echo
  echo "--- pravrudhi-app: live-chromium ---"
  if ( cd "$PRODUCT_DIR" && npm ci --no-audit --no-fund && npx playwright install chromium && npx playwright test --project=live-chromium ); then
    echo "pravrudhi-app: PASS"
  else
    echo "pravrudhi-app: FAIL"
    status=1
  fi
  echo
  echo "--- pravrudhi (Studio): live-chromium admin boundary ---"
  if ( cd "$STUDIO_DIR" && npm ci --no-audit --no-fund && npx playwright install chromium && npx playwright test --project=live-chromium ); then
    echo "pravrudhi (Studio): PASS"
  else
    echo "pravrudhi (Studio): FAIL"
    status=1
  fi
} > "$REPORT" 2>&1

ln -sf "$REPORT" "$REPORT_DIR/latest.log"
if [ "$status" -ne 0 ]; then
  echo "pravrudhi-e2e-nightly failed; see $REPORT" >&2
  notify_telegram "pravrudhi-e2e-nightly failed. Report: $REPORT"
fi
exit "$status"
