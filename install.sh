#!/usr/bin/env bash
# FrameGrid — Copyright (C) 2026 Javier
# SPDX-License-Identifier: GPL-3.0-or-later
# Install FrameGrid to ~/.local/share/framegrid and create the desktop launcher.
#   ./install.sh            -> install + KDE application menu entry
#   ./install.sh --steam    -> also add it to the Steam library (live mode, without closing Steam)
#   ./install.sh --uninstall
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"
DEST="$HOME/.local/share/framegrid"
APPS="$HOME/.local/share/applications"
DESKTOP_FILE="$APPS/framegrid.desktop"

if [[ "${1:-}" == "--uninstall" ]]; then
  rm -rf "$DEST" "$DESKTOP_FILE" "$HOME/Desktop/framegrid.desktop"
  echo "FrameGrid uninstalled (your API key remains in ~/.config/framegrid; delete it if you wish)."
  exit 0
fi

command -v python3 >/dev/null || { echo "python3 is missing"; exit 1; }
python3 - <<'EOF' || { echo "Python 3.9 or later is required"; exit 1; }
import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)
EOF

mkdir -p "$DEST" "$APPS"
cp -r "$SRC/framegrid.py" "$SRC/fg" "$SRC/web" "$SRC/framegrid.svg" "$SRC/LICENSE" "$DEST/"
chmod +x "$DEST/framegrid.py"

cat > "$DESKTOP_FILE" <<EOF
[Desktop Entry]
Type=Application
Name=FrameGrid
Comment=SteamGridDB artwork and collections for Steam
Exec=python3 $DEST/framegrid.py
Icon=$DEST/framegrid.svg
Terminal=false
Categories=Game;Utility;
EOF
chmod +x "$DESKTOP_FILE"
if [[ -d "$HOME/Desktop" ]]; then
  cp "$DESKTOP_FILE" "$HOME/Desktop/framegrid.desktop"
  chmod +x "$HOME/Desktop/framegrid.desktop"
fi
command -v update-desktop-database >/dev/null && update-desktop-database "$APPS" >/dev/null 2>&1 || true
echo "Installed to $DEST — look for “FrameGrid” in the application menu."

if [[ "${1:-}" == "--steam" ]]; then
  # With Steam running, use live mode (CEF debugger); no need to close Steam
  python3 "$DEST/framegrid.py" --add-to-steam
fi
echo
python3 "$DEST/framegrid.py" --status || true
