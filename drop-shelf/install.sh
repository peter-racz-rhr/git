#!/usr/bin/env bash
# Installs Drop Shelf for the current user (no sudo needed unless deps are missing).
#
#   ./install.sh                      default shortcut: Ctrl+Alt+D
#   SHORTCUT='<Super>z' ./install.sh  pick your own shortcut
set -euo pipefail

SHORTCUT="${SHORTCUT:-<Primary><Alt>d}"
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

# 6. Global keyboard shortcut
CMD="$BIN --toggle"
shortcut_done=0

if command -v gsettings >/dev/null 2>&1 && gsettings list-schemas | grep -qx org.cinnamon.desktop.keybindings; then
    say "Registering Cinnamon shortcut $SHORTCUT"
    /usr/bin/python3 - "$CMD" "$SHORTCUT" <<'PY'
import ast, subprocess, sys
cmd, shortcut = sys.argv[1], sys.argv[2]
schema = "org.cinnamon.desktop.keybindings"
base = "/org/cinnamon/desktop/keybindings/custom-keybindings/"
custom = "org.cinnamon.desktop.keybindings.custom-keybinding"

def get(schema_path, key):
    return subprocess.run(["gsettings", "get", schema_path, key],
                          capture_output=True, text=True).stdout.strip()

def gset(schema_path, key, value):
    subprocess.run(["gsettings", "set", schema_path, key, value], check=True)

raw = get(schema, "custom-list")
names = ast.literal_eval(raw.replace("@as ", "")) if raw else []
# Reuse our own slot if we installed before, otherwise take a free customN.
slot = None
for n in names:
    if get(f"{custom}:{base}{n}/", "command").strip("'").endswith("drop-shelf --toggle"):
        slot = n
        break
if slot is None:
    i = 0
    while f"custom{i}" in names:
        i += 1
    slot = f"custom{i}"
path = f"{custom}:{base}{slot}/"
gset(path, "name", "Drop Shelf")
gset(path, "command", cmd)
try:
    gset(path, "binding", repr([shortcut]))       # Cinnamon 4+ (list of strings)
except subprocess.CalledProcessError:
    gset(path, "binding", repr(shortcut))          # very old Cinnamon (single string)
if slot not in names:
    names.append(slot)
    gset(schema, "custom-list", repr(names))
PY
    shortcut_done=1
elif command -v gsettings >/dev/null 2>&1 && gsettings list-schemas | grep -qx org.mate.control-center.keybinding; then
    say "Registering MATE shortcut $SHORTCUT"
    path="/org/mate/desktop/keybindings/drop-shelf/"
    gsettings set "org.mate.control-center.keybinding:$path" name "Drop Shelf"
    gsettings set "org.mate.control-center.keybinding:$path" action "$CMD"
    gsettings set "org.mate.control-center.keybinding:$path" binding "$SHORTCUT"
    shortcut_done=1
elif command -v xfconf-query >/dev/null 2>&1; then
    say "Registering Xfce shortcut $SHORTCUT"
    xfconf-query -c xfce4-keyboard-shortcuts -p "/commands/custom/$SHORTCUT" -n -t string -s "$CMD"
    shortcut_done=1
fi

if [ "$shortcut_done" = 0 ]; then
    warn "Could not set the shortcut automatically."
    warn "Add it yourself: System Settings > Keyboard > Shortcuts > Custom, command: $CMD"
fi

# 7. Start it now
"$BIN" --hidden >/dev/null 2>&1 &
disown || true

say "Done! Press ${SHORTCUT} to show or hide the shelf."
say "You can also open it from the menu: Drop Shelf"
