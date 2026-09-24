#!/usr/bin/env bash
# Installs Drop Shelf for the current user (no sudo needed unless deps are missing).
#
#   ./install.sh                      shortcut Super+Z (or the next free one)
#   SHORTCUT='<Super>x' ./install.sh  pick your own shortcut
set -euo pipefail

SHORTCUT="${SHORTCUT:-}"
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$HOME/.local/share/drop-shelf"
BIN="$HOME/.local/bin/drop-shelf"
DESKTOP_FILE="$HOME/.local/share/applications/drop-shelf.desktop"
AUTOSTART_FILE="$HOME/.config/autostart/drop-shelf.desktop"
NEMO_ACTION="$HOME/.local/share/nemo/actions/drop-shelf.nemo_action"

say() { printf '\033[1;32m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m %s\n' "$*"; }

# 1. Dependencies (preinstalled on Linux Mint, but just in case)
if ! /usr/bin/python3 -c 'import gi; gi.require_version("Gtk", "3.0"); from gi.repository import Gtk' 2>/dev/null; then
    say "Installing python3-gi and GTK bindings (needs your password)"
    sudo apt-get install -y python3-gi gir1.2-gtk-3.0
fi

# 2. Program files
say "Copying program to $APP_DIR"
mkdir -p "$APP_DIR" "$(dirname "$BIN")"
install -m 755 "$SRC_DIR/drop_shelf.py" "$APP_DIR/drop_shelf.py"
cat > "$BIN" <<EOF
#!/bin/sh
exec /usr/bin/python3 "$APP_DIR/drop_shelf.py" "\$@"
EOF
chmod 755 "$BIN"

# 3. Menu entry
say "Adding menu entry"
mkdir -p "$(dirname "$DESKTOP_FILE")"
cat > "$DESKTOP_FILE" <<EOF
[Desktop Entry]
Type=Application
Name=Drop Shelf
Comment=Temporary shelf for drag and drop
Exec=$BIN %F
Icon=edit-paste
Terminal=false
Categories=Utility;
Keywords=drag;drop;shelf;clipboard;files;
StartupNotify=false
EOF

# 4. Autostart in the background so the shortcut reacts instantly
say "Enabling autostart (hidden in the background)"
mkdir -p "$(dirname "$AUTOSTART_FILE")"
cat > "$AUTOSTART_FILE" <<EOF
[Desktop Entry]
Type=Application
Name=Drop Shelf
Comment=Temporary shelf for drag and drop
Exec=$BIN --hidden
Icon=edit-paste
Terminal=false
X-GNOME-Autostart-enabled=true
EOF

# 5. Right-click "Add to Drop Shelf" in Nemo
if command -v nemo >/dev/null 2>&1; then
    say "Adding 'Add to Drop Shelf' to Nemo's right-click menu"
    mkdir -p "$(dirname "$NEMO_ACTION")"
    cat > "$NEMO_ACTION" <<EOF
[Nemo Action]
Name=Add to Drop Shelf
Comment=Put the selected items on the Drop Shelf
Exec=$BIN %F
Icon-Name=edit-paste
Selection=notnone
Extensions=any;
EOF
fi

# Keyboard shortcut: the app grabs it itself. A shortcut you set up in the
# Keyboard settings is left alone.
CONFIG="$HOME/.config/drop-shelf/settings.ini"
if [ -n "$SHORTCUT" ]; then
    mkdir -p "$(dirname "$CONFIG")"
    /usr/bin/python3 - "$CONFIG" "$SHORTCUT" <<'PY'
import sys
from gi.repository import GLib
path, shortcut = sys.argv[1], sys.argv[2]
kf = GLib.KeyFile()
try:
    kf.load_from_file(path, GLib.KeyFileFlags.KEEP_COMMENTS)
except GLib.Error:
    pass
kf.set_string("shelf", "shortcut", shortcut)
kf.save_to_file(path)
PY
fi

# Start it now (restart it if an older copy is already running)
if pgrep -f '^/usr/bin/python3[.0-9]* [^ ]*/drop_shelf[.]py' >/dev/null; then
    "$BIN" --quit >/dev/null 2>&1 || true
    sleep 1
fi
nohup "$BIN" --hidden >/dev/null 2>&1 &
sleep 2

ACTIVE="$("$BIN" --shortcut)"
case "$ACTIVE" in
    none*) warn "Could not grab a keyboard shortcut. You can still open it from the menu."
           say "Done!" ;;
    *)     case "$ACTIVE" in *Super*) ACTIVE="$ACTIVE (Super is the Windows key)";; esac
           say "Done! Press $ACTIVE to show or hide the shelf." ;;
esac
say "You can also open it from the menu: Drop Shelf"
