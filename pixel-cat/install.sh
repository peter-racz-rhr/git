#!/usr/bin/env bash
# Installs Pixel Cat for the current user.
#
#   ./install.sh                         install (or update - the old version is removed first)
#   SHORTCUT='<Super><Shift>c' ./install.sh   use a different shortcut to call her (none = off)
#   SIZE=3 ./install.sh                  a bigger cat (1 = small, 2 = normal, 3 = big)
set -euo pipefail

SHORTCUT="${SHORTCUT:-}"
SIZE="${SIZE:-}"
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$HOME/.local/share/pixel-cat/app"
BIN="$HOME/.local/bin/pixel-cat"
DESKTOP_FILE="$HOME/.local/share/applications/pixel-cat.desktop"
AUTOSTART_FILE="$HOME/.config/autostart/pixel-cat.desktop"
PATTERN='^/usr/bin/python3[.0-9]* [^ ]*/pixel_cat[.]py'

say() { printf '\033[1;32m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m %s\n' "$*"; }

# 1. GTK, cairo and Wnck (to know where the windows are)
need=()
/usr/bin/python3 -c 'import cairo, gi; gi.require_version("Gtk", "3.0"); gi.require_foreign("cairo")' 2>/dev/null \
    || need+=(python3-gi python3-gi-cairo gir1.2-gtk-3.0)
/usr/bin/python3 -c 'import gi; gi.require_version("Wnck", "3.0")' 2>/dev/null || need+=(gir1.2-wnck-3.0)
command -v paplay >/dev/null || command -v pw-play >/dev/null || command -v aplay >/dev/null \
    || need+=(pulseaudio-utils)
if [ ${#need[@]} -gt 0 ]; then
    say "Installing: ${need[*]} (needs your password)"
    sudo apt-get install -y "${need[@]}" || warn "Could not install everything - she may not see your windows"
fi

# 2. Remove any earlier version first (her name and mood are kept)
if pgrep -f "$PATTERN" >/dev/null; then
    [ -x "$BIN" ] && "$BIN" --quit >/dev/null 2>&1 || true
    sleep 1
    pkill -f "$PATTERN" 2>/dev/null || true
fi
rm -rf "$APP_DIR"
rm -f "$BIN" "$DESKTOP_FILE" "$AUTOSTART_FILE"

# 3. Program files
say "Copying program to $APP_DIR"
mkdir -p "$APP_DIR" "$(dirname "$BIN")"
install -m 755 "$SRC_DIR/pixel_cat.py" "$APP_DIR/pixel_cat.py"
install -m 644 "$SRC_DIR/sprites.py" "$APP_DIR/sprites.py"
cat > "$BIN" <<EOS
#!/bin/sh
exec /usr/bin/python3 "$APP_DIR/pixel_cat.py" "\$@"
EOS
chmod 755 "$BIN"

# 4. Menu entry (opening it again calls her to the mouse) + autostart
mkdir -p "$(dirname "$DESKTOP_FILE")" "$(dirname "$AUTOSTART_FILE")"
cat > "$DESKTOP_FILE" <<EOS
[Desktop Entry]
Type=Application
Name=Pixel Cat
Comment=A little pixel-art cat that lives on your desktop
Exec=$BIN
Icon=face-smile
Terminal=false
Categories=Amusement;
Keywords=cat;pet;desktop pet;neko;
StartupNotify=false
EOS
cat > "$AUTOSTART_FILE" <<EOS
[Desktop Entry]
Type=Application
Name=Pixel Cat
Exec=$BIN
Icon=face-smile
Terminal=false
X-GNOME-Autostart-enabled=true
X-GNOME-Autostart-Delay=5
EOS

# 5. Settings
CONFIG="$HOME/.config/pixel-cat/settings.ini"
if [ -n "$SHORTCUT$SIZE" ]; then
    mkdir -p "$(dirname "$CONFIG")"
    /usr/bin/python3 - "$CONFIG" "$SHORTCUT" "$SIZE" <<'PY'
import sys
from gi.repository import GLib
path, shortcut, size = sys.argv[1:4]
kf = GLib.KeyFile()
try:
    kf.load_from_file(path, GLib.KeyFileFlags.KEEP_COMMENTS)
except GLib.Error:
    pass
if shortcut:
    kf.set_string("pixel-cat", "shortcut", shortcut)
if size:
    kf.set_string("pixel-cat", "size", size)
kf.save_to_file(path)
PY
fi

# 6. Start her
nohup "$BIN" >/dev/null 2>&1 &
sleep 2
ACTIVE="$("$BIN" --shortcut)"
if [ -f "$HOME/.local/share/pixel-cat/cat.json" ]; then
    say "Done! She's back on your desktop."
else
    say "Done! Pick her coat and name in the window that just opened."
fi
case "$ACTIVE" in
    none*) warn "No keyboard shortcut active; use the menu entry Pixel Cat to call her." ;;
    *)     say "Press $ACTIVE to call her to your mouse. Right-click her for treats and play." ;;
esac
