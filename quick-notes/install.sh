#!/usr/bin/env bash
# Installs Quick Notes for the current user (no sudo needed unless deps are missing).
#
#   ./install.sh                      shortcut Ctrl+Alt+N (or the next free one)
#   SHORTCUT='<Super>n' ./install.sh  pick your own shortcut
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

# 2. Program files
say "Copying program to $APP_DIR"
mkdir -p "$APP_DIR" "$(dirname "$BIN")"
install -m 755 "$SRC_DIR/quick_notes.py" "$APP_DIR/quick_notes.py"
cat > "$BIN" <<EOF
#!/bin/sh
exec /usr/bin/python3 "$APP_DIR/quick_notes.py" "\$@"
EOF
chmod 755 "$BIN"

# 3. Menu entry
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

# 4. Autostart in the background so the shortcut reacts instantly
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

# Keyboard shortcut: the app grabs it itself (the desktop's own shortcut settings
# did not pick up shortcuts added by a script). Remove entries older versions added.
if command -v gsettings >/dev/null 2>&1 && gsettings list-schemas | grep -qx org.cinnamon.desktop.keybindings; then
    /usr/bin/python3 - <<'PY'
import ast, subprocess
schema = "org.cinnamon.desktop.keybindings"
custom = "org.cinnamon.desktop.keybindings.custom-keybinding"
base = "/org/cinnamon/desktop/keybindings/custom-keybindings/"
get = lambda s, k: subprocess.run(["gsettings", "get", s, k], capture_output=True, text=True).stdout.strip()
try:
    names = ast.literal_eval(get(schema, "custom-list").replace("@as ", "") or "[]")
except (ValueError, SyntaxError):
    names = []
keep = []
for n in names:
    path = f"{custom}:{base}{n}/"
    if "quick-notes" in get(path, "command"):
        for key in ("name", "command", "binding"):
            subprocess.run(["gsettings", "reset", path, key])
    else:
        keep.append(n)
if keep != names:
    subprocess.run(["gsettings", "set", schema, "custom-list", repr(keep)])
PY
fi
if command -v dconf >/dev/null 2>&1; then
    dconf reset -f /org/mate/desktop/keybindings/quick-notes/ 2>/dev/null || true
fi

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
if pgrep -f quick_notes.py >/dev/null; then
    "$BIN" --quit >/dev/null 2>&1 || true
    sleep 1
fi
nohup "$BIN" --restore >/dev/null 2>&1 &
sleep 2

ACTIVE="$("$BIN" --shortcut)"
case "$ACTIVE" in
    none*) warn "Could not grab a keyboard shortcut. You can still open it from the menu."
           say "Done!" ;;
    *)     case "$ACTIVE" in *Super*) ACTIVE="$ACTIVE (Super is the Windows key)";; esac
           say "Done! Press $ACTIVE to write a new note." ;;
esac
say "You can also open it from the menu: Quick Notes"
