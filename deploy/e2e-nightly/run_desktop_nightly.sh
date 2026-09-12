#!/usr/bin/env bash
# Desktop nightly (pravrudhi-e2e-desktop-nightly.service): the same real, signed-in account, this time through
# a released, packaged shell rather than a browser. SUPABASE_URL and E2E_EMAIL/E2E_PASSWORD arrive through the
# unit's EnvironmentFile(s) and are read only by pravrudhi-app/desktop/nightly-dist.js's own process.env access
# — this script never reads, echoes, or logs any of them, so nothing leaves this box. An optional
# PRAVRUDHI_GITHUB_TOKEN_AXISMERU/GITHUB_TOKEN raises the unauthenticated 60/hr GitHub API limit that release
# lookup and the pinned-engine wheel install both call against.
#
# What this proves, and does not, because it is easy to overstate: desktop/lib/connection.js's loopbackOrigin
# refuses any non-loopback engine origin by design, so the packaged shell cannot reach the hosted product
# engine at all — nightly-dist.js starts a local engine instead, at the exact version the release under test
# was pinned to, with PRAVRUDHI_AUTH=required so gateway-probe's sign-in is validated by real Supabase auth.
# Real account, real auth, released shell, fresh local root — never the hosted engine's own data. The web
# nightly (run_e2e_nightly.sh) is what proves the hosted engine.
set -uo pipefail

REPORT_DIR="$HOME/.local/share/pravrudhi-hosted/e2e"
mkdir -p "$REPORT_DIR"
STAMP="$(date -u +%Y-%m-%dT%H-%M-%SZ)"
REPORT="$REPORT_DIR/desktop-$STAMP.log"

PRODUCT_DESKTOP_DIR="$HOME/projects/pravrudhi-app/desktop"

status=0
{
  echo "=== pravrudhi-e2e-desktop-nightly: $STAMP ==="
  echo
  # nightly-dist.js does the rest itself: resolves the latest release via the GitHub API, downloads the Linux
  # AppImage and SHA256SUMS, verifies the checksum, reads that release's pinned engine/ENGINE_VERSION via `git
  # show <tag>:...` and installs it into a scratch venv, starts a fresh `pravrudhi init` root, launches the
  # packaged shell, and writes its own dated JSON report beside this log.
  if ( cd "$PRODUCT_DESKTOP_DIR" && npm ci --no-audit --no-fund && node nightly-dist.js ); then
    echo "pravrudhi-app desktop: PASS"
  else
    echo "pravrudhi-app desktop: FAIL"
    status=1
  fi
} > "$REPORT" 2>&1

ln -sf "$REPORT" "$REPORT_DIR/desktop-latest.log"
if [ "$status" -ne 0 ]; then
  echo "pravrudhi-e2e-desktop-nightly failed; see $REPORT" >&2
fi
exit "$status"
