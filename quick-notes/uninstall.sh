#!/usr/bin/env bash
# Removes Quick Notes for the current user. Your notes are kept unless you pass --delete-notes.
set -uo pipefail

BIN="$HOME/.local/bin/quick-notes"
NOTES="$HOME/.local/share/quick-notes/notes.json"
[ -x "$BIN" ] && pgrep -f quick_notes.py >/dev/null && "$BIN" --quit >/dev/null 2>&1

# Give the lock screen its original message back.
if [ -f "$NOTES" ] && command -v gsettings >/dev/null 2>&1 \
   && gsettings list-schemas | grep -qx org.cinnamon.desktop.screensaver; then
    /usr/bin/python3 - "$NOTES" <<'PY'
import json, subprocess, sys
try:
    original = json.load(open(sys.argv[1])).get("lock_original")
except Exception:
    original = None
if original is not None:
    subprocess.run(["gsettings", "set", "org.cinnamon.desktop.screensaver", "default-message", original])
PY
fi

rm -rf "$HOME/.local/share/quick-notes/app"
rm -f "$BIN" \
      "$HOME/.local/share/applications/quick-notes.desktop" \
      "$HOME/.config/autostart/quick-notes.desktop"

if [ "${1:-}" = "--delete-notes" ]; then
    rm -rf "$HOME/.local/share/quick-notes"
    echo "Notes deleted."
else
    echo "Your notes are kept in $NOTES"
fi

# Cinnamon shortcut
if command -v gsettings >/dev/null 2>&1 && gsettings list-schemas | grep -qx org.cinnamon.desktop.keybindings; then
    /usr/bin/python3 - <<'PY'
import ast, subprocess
schema = "org.cinnamon.desktop.keybindings"
custom = "org.cinnamon.desktop.keybindings.custom-keybinding"
base = "/org/cinnamon/desktop/keybindings/custom-keybindings/"
get = lambda s, k: subprocess.run(["gsettings", "get", s, k], capture_output=True, text=True).stdout.strip()
names = ast.literal_eval(get(schema, "custom-list").replace("@as ", "") or "[]")
keep = []
for n in names:
    path = f"{custom}:{base}{n}/"
    if get(path, "command").strip("'").endswith("quick-notes --new"):
        for key in ("name", "command", "binding"):
            subprocess.run(["gsettings", "reset", path, key])
    else:
        keep.append(n)
subprocess.run(["gsettings", "set", schema, "custom-list", repr(keep)])
PY
fi
# MATE shortcut
if command -v dconf >/dev/null 2>&1; then
    dconf reset -f /org/mate/desktop/keybindings/quick-notes/ 2>/dev/null
fi
# Xfce shortcut
if command -v xfconf-query >/dev/null 2>&1; then
    for p in $(xfconf-query -c xfce4-keyboard-shortcuts -l 2>/dev/null | grep '^/commands/custom/'); do
        if xfconf-query -c xfce4-keyboard-shortcuts -p "$p" 2>/dev/null | grep -q 'quick-notes --new'; then
            xfconf-query -c xfce4-keyboard-shortcuts -p "$p" -r
        fi
    done
fi

echo "Quick Notes removed."
