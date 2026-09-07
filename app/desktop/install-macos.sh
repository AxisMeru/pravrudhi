#!/bin/bash
set -euo pipefail
cd -- "$(dirname -- "$0")"
if [[ "$(uname -s)" != Darwin || "$(uname -m)" != arm64 ]]; then
  echo 'Run this installer on an Apple Silicon Mac.' >&2
  exit 1
fi
archive=${1:?Usage: bash install-macos.sh /path/to/Pravrudhi-version-mac-arm64.zip}
[[ "$archive" = /* ]] || archive="$PWD/$archive"
test -f "$archive"
# Per-user Applications needs no administrator password; keep evidence beside it.
mkdir -p "$HOME/Applications"
if pgrep -f '/Pravrudhi.app/Contents/MacOS/Pravrudhi' >/dev/null; then
  echo 'Quit Pravrudhi before replacing the installed application.' >&2
  exit 1
fi
stage=$(mktemp -d "$HOME/Applications/.pravrudhi-install.XXXXXX")
trap 'rm -rf "$stage"' EXIT
ditto -x -k "$archive" "$stage"
test -x "$stage/Pravrudhi.app/Contents/MacOS/Pravrudhi"
file "$stage/Pravrudhi.app/Contents/MacOS/Pravrudhi" | tee "$stage/architecture.txt"
grep -q arm64 "$stage/architecture.txt"
# Only remove quarantine from this operator-built application, not other apps.
xattr -dr com.apple.quarantine "$stage/Pravrudhi.app"
ditto "$stage/Pravrudhi.app" "$HOME/Applications/Pravrudhi.app"
# The engine needs a workspace that is actually one. `$HOME/pravrudhi` is a llama.cpp directory on this
# operator's Mac, and starting the engine there gives a running process with nothing behind it, which reads as a
# working install until someone opens a page. Prefer the release install, which is what the update channel
# maintains, and fall back to the home directory only when there is no release.
workspace="$HOME/pravrudhi-release"
[ -d "$workspace/.pravrudhi/releases/current" ] || workspace="$HOME/pravrudhi"
echo "engine workspace: $workspace"

evidence="$PWD/macos-evidence"
mkdir -p "$evidence"
rm -f "$evidence/.smoke/report.json" "$evidence/interface.png" "$evidence/interface.png.json"
PRAVRUDHI_DESKTOP_SMOKE=1 PRAVRUDHI_DESKTOP_SMOKE_DIR="$evidence" \
PRAVRUDHI_DESKTOP_SHOT="$evidence/interface.png" \
PRAVRUDHI_WORKSPACE="$workspace" \
  "$HOME/Applications/Pravrudhi.app/Contents/MacOS/Pravrudhi" \
  >"$evidence/launch.log" 2>&1 &
pid=$!
# Bound the entire smoke process, including early Electron startup failures.
(sleep 120; kill -TERM "$pid" 2>/dev/null || true) &
watchdog=$!
result=0
wait "$pid" || result=$?
kill "$watchdog" 2>/dev/null || true
cat "$evidence/launch.log"
test "$result" -eq 0
/usr/bin/python3 - "$evidence" <<'PY'
import json, pathlib, sys
root = pathlib.Path(sys.argv[1])
report = json.loads((root / '.smoke/report.json').read_text())
print(json.dumps(report, indent=2))
assert all(report.get(k) is True for k in ('launched', 'engine_found', 'health_ok'))
assert report.get('page_title') and report.get('engine_url') and report.get('errors') == []
observed = json.loads((root / 'interface.png.json').read_text())
print(json.dumps(observed, indent=2))
assert len(observed['body']) > 40
assert (root / 'interface.png').stat().st_size > 0
PY
open "$HOME/Applications/Pravrudhi.app"
echo "Smoke passed; launched installed app. Inspect $evidence/interface.png and the desktop window."
