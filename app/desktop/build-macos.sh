#!/bin/bash
set -euo pipefail
cd -- "$(dirname -- "$0")"
# Cross-build a ZIP on Linux or macOS; DMG creation requires macOS.
# Dependencies and Electron are pinned by package-lock.json. No global builder.
command -v node >/dev/null
command -v npm >/dev/null
npm ci --no-audit --no-fund
CSC_IDENTITY_AUTO_DISCOVERY=false ./node_modules/.bin/electron-builder \
  --mac zip --arm64 -c.mac.identity=null \
  '-c.artifactName=Pravrudhi-${version}-mac-arm64.${ext}'
