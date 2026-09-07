#!/bin/bash
set -euo pipefail
cd -- "$(dirname -- "$0")"
# Install one edition's AppImage for the current user and prove it starts before leaving it in place.
#
# Usage: bash install-linux.sh [product|studio]
#
# Per-user throughout: ~/.local/bin and ~/.local/share/applications need no administrator, and the two editions
# carry different names and desktop-entry ids so both can be installed at once. That is the point of having two
# — Studio builds Pravrudhi, and Pravrudhi builds whatever its user is building.
edition=${1:-product}
case "$edition" in
  # The binary is deliberately not called `pravrudhi`: `~/.local/bin/pravrudhi` is one of the paths the shell
  # searches for the *engine* (lib/core.js::discoverEngine), and installing a 117MB desktop AppImage there makes
  # the shell discover itself. It is named for what it is instead.
  product) appname="Pravrudhi"; binary="pravrudhi-desktop"; entry="org.pravrudhi.desktop" ;;
  studio)  appname="Pravrudhi Studio"; binary="pravrudhi-studio-desktop"; entry="org.pravrudhi.studio" ;;
  *) echo "Usage: bash install-linux.sh [product|studio]" >&2; exit 1 ;;
esac

image=$(ls -1 "dist/$edition"/*.AppImage 2>/dev/null | head -1 || true)
if [ -z "$image" ]; then
  echo "No AppImage in dist/$edition/. Run: npm run dist:linux   (or dist:studio:linux)" >&2
  exit 1
fi

# Prove the packaged application actually starts, finds an engine and reports the edition it was built as,
# before it is installed anywhere. An install that only copies a file is not evidence that it works.
npm run smoke:dist -- "$edition"

bindir="$HOME/.local/bin"
appdir="$HOME/.local/share/applications"
icondir="$HOME/.local/share/icons/hicolor/512x512/apps"
mkdir -p "$bindir" "$appdir" "$icondir"

# Never overwrite something that is not a previous install of this edition: the engine's own launcher lives in
# directories like this one, and replacing it with a desktop shell would break the thing the shell needs.
if [ -e "$bindir/$binary" ] && ! head -c 4 "$bindir/$binary" | grep -q ELF; then
  echo "$bindir/$binary exists and is not an AppImage; refusing to replace it." >&2
  exit 1
fi
install -m 0755 "$image" "$bindir/$binary"
install -m 0644 renderer/icon.png "$icondir/$entry.png"

cat > "$appdir/$entry.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=$appname
Comment=Improve your own model, agent or app, on your own hardware, while you watch.
Exec=$bindir/$binary %U
Icon=$entry
Terminal=false
Categories=Development;
StartupWMClass=$appname
DESKTOP

command -v update-desktop-database >/dev/null && update-desktop-database "$appdir" 2>/dev/null || true
echo "Installed $appname:"
echo "  binary       $bindir/$binary"
echo "  menu entry   $appdir/$entry.desktop"
case ":$PATH:" in *":$bindir:"*) ;; *) echo "  note: $bindir is not on PATH; the menu entry uses the full path anyway" ;; esac
