#!/bin/bash
set -euo pipefail
cd -- "$(dirname -- "$0")"
# Cross-build a ZIP on Linux or macOS; DMG creation requires macOS.
# Usage: bash build-macos.sh [product|studio]
# Dependencies and Electron are pinned by package-lock.json. No global builder.
command -v node >/dev/null
command -v npm >/dev/null
# Which of the two installs to build. Studio is the operator's edition; the product is what a user installs.
# They carry different appIds and product names so one Mac can hold both.
edition=${1:-product}
case "$edition" in product|studio) ;; *) echo "Usage: bash build-macos.sh [product|studio]" >&2; exit 1 ;; esac
config="electron-builder.$edition.json"
test -f "$config"
npm ci --no-audit --no-fund
CSC_IDENTITY_AUTO_DISCOVERY=false ./node_modules/.bin/electron-builder \
  --mac zip --arm64 -c.mac.identity=null --config "$config"
ls -1 "dist/$edition"/*.zip
