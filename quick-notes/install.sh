#!/usr/bin/env bash
# Installs Quick Notes for the current user (no sudo needed unless deps are missing).
#
#   ./install.sh                      shortcut Ctrl+Alt+N (or the next free one)
#   SHORTCUT='<Super>n' ./install.sh  pick your own shortcut
set -euo pipefail

# Tried in order; the first one not already used by the desktop wins.
CANDIDATES="<Primary><Alt>n <Super>n <Primary><Alt>j <Primary><Super>n"
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

# 5. Global keyboard shortcut
CMD="$BIN --new"
shortcut_done=0

if command -v gsettings >/dev/null 2>&1 && gsettings list-schemas | grep -qx org.cinnamon.desktop.keybindings; then
    SHORTCUT="$(/usr/bin/python3 - "$CMD" "$SHORTCUT" "$CANDIDATES" <<'PY'
import ast, re, subprocess, sys
cmd, wanted, candidates = sys.argv[1], sys.argv[2], sys.argv[3].split()
schema = "org.cinnamon.desktop.keybindings"
base = "/org/cinnamon/desktop/keybindings/custom-keybindings/"
custom = "org.cinnamon.desktop.keybindings.custom-keybinding"

def run(*args):
    return subprocess.run(["gsettings", *args], capture_output=True, text=True).stdout.strip()

def gset(schema_path, key, value):
    subprocess.run(["gsettings", "set", schema_path, key, value], check=True)

def parse_list(raw):
    raw = raw.replace("@as ", "")
    try:
        value = ast.literal_eval(raw) if raw else []
    except (ValueError, SyntaxError):
        return []
    return [value] if isinstance(value, str) else list(value)

ALIASES = {"control": "ctrl", "ctrl": "ctrl", "primary": "ctrl", "alt": "alt", "mod1": "alt",
           "super": "super", "mod4": "super", "shift": "shift", "meta": "meta", "hyper": "hyper"}

def norm(accel):
    mods = frozenset(ALIASES.get(m.lower(), m.lower()) for m in re.findall(r"<([^>]+)>", accel))
    key = re.sub(r"<[^>]+>", "", accel).lower()
    return (mods, key)

names = parse_list(run("get", schema, "custom-list"))
slot = None
taken = set()
for n in names:
    path = f"{custom}:{base}{n}/"
    if run("get", path, "command").strip("'").endswith("quick-notes --new"):
        slot = n          # our own entry from an earlier install - reuse it
        continue
    taken.update(norm(a) for a in parse_list(run("get", path, "binding")))

# Every built-in Cinnamon / window-manager / media-key shortcut.
listing = subprocess.run(["gsettings", "list-schemas"], capture_output=True, text=True).stdout.split()
for s in listing:
    if s.startswith(("org.cinnamon.desktop.keybindings", "org.cinnamon.muffin.keybindings",
                     "org.gnome.desktop.wm.keybindings")) and s != custom:
        for line in run("list-recursively", s).splitlines():
            for accel in re.findall(r"'([^']*<[^']+>[^']*)'", line):
                taken.add(norm(accel))

if wanted:
    shortcut = wanted
    if norm(wanted) in taken:
        print(f"!! {wanted} is already used by another shortcut - it may not work", file=sys.stderr)
else:
    free = [c for c in candidates if norm(c) not in taken]
    if not free:
        print("!! all default shortcuts are taken, set one manually", file=sys.stderr)
        sys.exit(0)
    shortcut = free[0]

if slot is None:
    i = 0
    while f"custom{i}" in names:
        i += 1
    slot = f"custom{i}"
path = f"{custom}:{base}{slot}/"
gset(path, "name", "Quick Notes")
gset(path, "command", cmd)
try:
    gset(path, "binding", repr([shortcut]))       # Cinnamon 4+ (list of strings)
except subprocess.CalledProcessError:
    gset(path, "binding", repr(shortcut))          # very old Cinnamon (single string)
if slot not in names:
    names.append(slot)
    gset(schema, "custom-list", repr(names))
print(shortcut)
PY
)"
    [ -n "$SHORTCUT" ] && shortcut_done=1 && say "Registered Cinnamon shortcut $SHORTCUT"
elif command -v gsettings >/dev/null 2>&1 && gsettings list-schemas | grep -qx org.mate.control-center.keybinding; then
    SHORTCUT="${SHORTCUT:-${CANDIDATES%% *}}"
    say "Registering MATE shortcut $SHORTCUT"
    path="/org/mate/desktop/keybindings/quick-notes/"
    gsettings set "org.mate.control-center.keybinding:$path" name "Quick Notes"
    gsettings set "org.mate.control-center.keybinding:$path" action "$CMD"
    gsettings set "org.mate.control-center.keybinding:$path" binding "$SHORTCUT"
    shortcut_done=1
elif command -v xfconf-query >/dev/null 2>&1; then
    SHORTCUT="${SHORTCUT:-${CANDIDATES%% *}}"
    say "Registering Xfce shortcut $SHORTCUT"
    xfconf-query -c xfce4-keyboard-shortcuts -p "/commands/custom/$SHORTCUT" -n -t string -s "$CMD"
    shortcut_done=1
fi

if [ "$shortcut_done" = 0 ]; then
    warn "Could not set the shortcut automatically."
    warn "Add it yourself: System Settings > Keyboard > Shortcuts > Custom, command: $CMD"
fi

# 6. Start it now (restart it if an older copy is already running)
"$BIN" --quit >/dev/null 2>&1 || true
sleep 1
"$BIN" --restore >/dev/null 2>&1 &
disown || true

if [ "$shortcut_done" = 1 ]; then
    NICE="$(echo "$SHORTCUT" | sed 's/<Primary>/Ctrl+/g; s/<Control>/Ctrl+/g; s/<Alt>/Alt+/g; s/<Super>/Super+/g; s/<Shift>/Shift+/g; s/space$/Space/; s/+\([a-z]\)$/+\U\1/')"
    case "$NICE" in *Super*) NICE="$NICE (Super is the Windows key)";; esac
    say "Done! Press ${NICE} to write a new note."
else
    say "Done!"
fi
say "You can also open it from the menu: Quick Notes"
