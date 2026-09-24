#!/usr/bin/env bash
# Removes Drop Shelf for the current user.
set -uo pipefail

BIN="$HOME/.local/bin/drop-shelf"
[ -x "$BIN" ] && "$BIN" --quit >/dev/null 2>&1

rm -rf "$HOME/.local/share/drop-shelf" "$HOME/.cache/drop-shelf" "$HOME/.config/drop-shelf"
rm -f "$BIN" \
      "$HOME/.local/share/applications/drop-shelf.desktop" \
      "$HOME/.config/autostart/drop-shelf.desktop" \
      "$HOME/.local/share/nemo/actions/drop-shelf.nemo_action"

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
    if get(path, "command").strip("'").endswith("drop-shelf --toggle"):
        for key in ("name", "command", "binding"):
            subprocess.run(["gsettings", "reset", path, key])
    else:
        keep.append(n)
subprocess.run(["gsettings", "set", schema, "custom-list", repr(keep)])
PY
fi
# MATE shortcut
if command -v dconf >/dev/null 2>&1; then
    dconf reset -f /org/mate/desktop/keybindings/drop-shelf/ 2>/dev/null
fi
# Xfce shortcut
if command -v xfconf-query >/dev/null 2>&1; then
    for p in $(xfconf-query -c xfce4-keyboard-shortcuts -l 2>/dev/null | grep '^/commands/custom/'); do
        if xfconf-query -c xfce4-keyboard-shortcuts -p "$p" 2>/dev/null | grep -q 'drop-shelf --toggle'; then
            xfconf-query -c xfce4-keyboard-shortcuts -p "$p" -r
        fi
    done
fi

echo "Drop Shelf removed."
