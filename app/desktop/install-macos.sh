#!/bin/bash
set -euo pipefail
cd -- "$(dirname -- "$0")"
if [[ "$(uname -s)" != Darwin || "$(uname -m)" != arm64 ]]; then
  echo 'Run this installer on an Apple Silicon Mac.' >&2
  exit 1
fi
archive=${1:?Usage: bash install-macos.sh /path/to/archive.zip [product|studio]}
[[ "$archive" = /* ]] || archive="$PWD/$archive"
test -f "$archive"
# Which of the two installs this archive holds. They carry different names on purpose, so both can sit in
# Applications at once: Studio builds Pravrudhi, and Pravrudhi builds whatever its user is building.
edition=${2:-product}
case "$edition" in
  product) appname="Pravrudhi" ;;
  studio)  appname="Pravrudhi Studio" ;;
  *) echo "Usage: bash install-macos.sh /path/to/archive.zip [product|studio]" >&2; exit 1 ;;
esac
# Per-user Applications needs no administrator password; keep evidence beside it.
mkdir -p "$HOME/Applications"
# Anchored on the full app path, so quitting one edition is not confused with the other still running.
if pgrep -f "/$appname.app/Contents/MacOS/" >/dev/null; then
  echo "Quit $appname before replacing the installed application." >&2
  exit 1
fi
stage=$(mktemp -d "$HOME/Applications/.pravrudhi-install.XXXXXX")
trap 'rm -rf "$stage"' EXIT
ditto -x -k "$archive" "$stage"
binary="$stage/$appname.app/Contents/MacOS/$appname"
test -x "$binary"
file "$binary" | tee "$stage/architecture.txt"
grep -q arm64 "$stage/architecture.txt"
# Only remove quarantine from this operator-built application, not other apps.
xattr -dr com.apple.quarantine "$stage/$appname.app"
ditto "$stage/$appname.app" "$HOME/Applications/$appname.app"
# The engine needs a workspace that is actually one. `$HOME/pravrudhi` is a llama.cpp directory on this
# operator's Mac, and starting the engine there gives a running process with nothing behind it, which reads as a
# working install until someone opens a page. Prefer the release install, which is what the update channel
# maintains, and fall back to the home directory only when there is no release.
workspace="$HOME/pravrudhi-release"
[ -d "$workspace/.pravrudhi/releases/current" ] || workspace="$HOME/pravrudhi"
echo "engine workspace: $workspace"

evidence="$PWD/macos-evidence-$edition"
mkdir -p "$evidence"
rm -f "$evidence/.smoke/report.json" "$evidence/interface.png" "$evidence/interface.png.json"
PRAVRUDHI_DESKTOP_SMOKE=1 PRAVRUDHI_DESKTOP_SMOKE_DIR="$evidence" \
PRAVRUDHI_DESKTOP_SHOT="$evidence/interface.png" \
PRAVRUDHI_WORKSPACE="$workspace" \
  "$HOME/Applications/$appname.app/Contents/MacOS/$appname" \
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
/usr/bin/python3 - "$evidence" "$edition" <<'PY'
import json, pathlib, sys
root = pathlib.Path(sys.argv[1])
report = json.loads((root / '.smoke/report.json').read_text())
print(json.dumps(report, indent=2))
assert all(report.get(k) is True for k in ('launched', 'engine_found', 'health_ok'))
assert report.get('page_title') and report.get('engine_url') and report.get('errors') == []
expected = sys.argv[2]
assert report.get('edition') == expected, f"installed the {expected} edition but it ran as {report.get('edition')!r}"
observed = json.loads((root / 'interface.png.json').read_text())
print(json.dumps(observed, indent=2))
assert len(observed['body']) > 40
assert (root / 'interface.png').stat().st_size > 0
PY
open "$HOME/Applications/$appname.app"
echo "Smoke passed; launched $appname. Inspect $evidence/interface.png and the desktop window."
