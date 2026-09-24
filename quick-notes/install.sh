#!/usr/bin/env bash
# Installs Quick Notes for the current user (no sudo needed unless deps are missing).
#
#   ./install.sh                      shortcut Ctrl+Alt+N (or the next free one)
#   SHORTCUT='<Super>n' ./install.sh  pick your own shortcut
#   SHORTCUT=none ./install.sh        no built-in shortcut (set one in the Keyboard settings)
set -euo pipefail

SHORTCUT="${SHORTCUT:-}"
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$HOME/.local/share/quick-notes/app"
BIN="$HOME/.local/bin/quick-notes"
DESKTOP_FILE="$HOME/.local/share/applications/quick-notes.desktop"
AUTOSTART_FILE="$HOME/.config/autostart/quick-notes.desktop"

say() { printf '\033[1;32m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m %s\n' "$*"; }

# 1. Dependencies (preinstalled on Linux Mint, but just in case)
if ! /usr/bin/python3 -c 'import gi; gi.require_version("Gtk", "3.0"); from gi.repository import Gtk' 2>/dev/null; then
    say "Installing python3-gi and GTK bindings (needs your password)"
    sudo apt-get install -y python3-gi gir1.2-gtk-3.0
fi

# 2. Remove any earlier version first (your notes are kept)
if pgrep -f '^/usr/bin/python3[.0-9]* [^ ]*/quick_notes[.]py' >/dev/null; then
    say "Stopping the running Quick Notes"
    if [ -x "$BIN" ]; then "$BIN" --quit >/dev/null 2>&1 || true; fi
    sleep 1
    pkill -f '^/usr/bin/python3[.0-9]* [^ ]*/quick_notes[.]py' 2>/dev/null || true
fi
if [ -e "$APP_DIR" ] || [ -e "$BIN" ]; then
    say "Removing the old version"
fi
rm -rf "$APP_DIR"
rm -f "$BIN" "$DESKTOP_FILE" "$AUTOSTART_FILE" "$HOME/.config/quick-notes/active-shortcut"

# 3. Program files
say "Copying program to $APP_DIR"
mkdir -p "$APP_DIR" "$(dirname "$BIN")"
install -m 755 "$SRC_DIR/quick_notes.py" "$APP_DIR/quick_notes.py"
cat > "$BIN" <<EOF
#!/bin/sh
exec /usr/bin/python3 "$APP_DIR/quick_notes.py" "\$@"
EOF
chmod 755 "$BIN"

# 4. Menu entry
say "Adding menu entry"
mkdir -p "$(dirname "$DESKTOP_FILE")"
cat > "$DESKTOP_FILE" <<EOF
[Desktop Entry]
Type=Application
Name=Quick Notes
Comment=Post-it notes that stay on top of your screen
Exec=$BIN --new
Icon=accessories-text-editor
Terminal=false
Categories=Utility;
Keywords=note;notes;post-it;sticky;memo;todo;
StartupNotify=false
EOF

# 5. Autostart in the background so the shortcut reacts instantly
say "Enabling autostart (your notes come back after a restart)"
mkdir -p "$(dirname "$AUTOSTART_FILE")"
cat > "$AUTOSTART_FILE" <<EOF
[Desktop Entry]
Type=Application
Name=Quick Notes
Comment=Post-it notes that stay on top of your screen
Exec=$BIN --restore
Icon=accessories-text-editor
Terminal=false
X-GNOME-Autostart-enabled=true
EOF

# Keyboard shortcut: the app grabs it itself. A shortcut you set up in the
# Keyboard settings is left alone.
CONFIG="$HOME/.config/quick-notes/settings.ini"
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
kf.set_string("quick-notes", "shortcut", shortcut)
kf.save_to_file(path)
PY
fi

# Start it now (restart it if an older copy is already running)
if pgrep -f '^/usr/bin/python3[.0-9]* [^ ]*/quick_notes[.]py' >/dev/null; then
    "$BIN" --quit >/dev/null 2>&1 || true
    sleep 1
fi
nohup "$BIN" --restore >/dev/null 2>&1 &
sleep 2

ACTIVE="$("$BIN" --shortcut)"
case "$ACTIVE" in
    none*) if [ "$SHORTCUT" = none ]; then
               say "Done! Built-in shortcut is off. Set one in Keyboard > Shortcuts > Custom Shortcuts"
               say "with the command: $BIN --new"
           else
               warn "Could not grab a keyboard shortcut. You can still open it from the menu."
               say "Done!"
           fi ;;
    *)     case "$ACTIVE" in *Super*) ACTIVE="$ACTIVE (Super is the Windows key)";; esac
           say "Done! Press $ACTIVE to write a new note." ;;
esac
say "You can also open it from the menu: Quick Notes"
