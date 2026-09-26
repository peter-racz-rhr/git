#!/usr/bin/env bash
# Installs Text Grab for the current user.
#
#   ./install.sh                      install (or update - the old version is removed first)
#   SHORTCUT='<Super><Shift>t' ./install.sh   use a different shortcut (none = off)
set -euo pipefail

SHORTCUT="${SHORTCUT:-}"
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$HOME/.local/share/text-grab/app"
BIN="$HOME/.local/bin/text-grab"
DESKTOP_FILE="$HOME/.local/share/applications/text-grab.desktop"
AUTOSTART_FILE="$HOME/.config/autostart/text-grab.desktop"
NEMO_ACTION="$HOME/.local/share/nemo/actions/text-grab.nemo_action"
PATTERN='^/usr/bin/python3[.0-9]* [^ ]*/text_grab[.]py'

say() { printf '\033[1;32m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m %s\n' "$*"; }

# 1. Text recognition (Tesseract with Hungarian, English, German), QR reader, GTK + cairo
need=()
command -v tesseract >/dev/null || need+=(tesseract-ocr)
for lang in hun eng deu; do
    [ -f "/usr/share/tesseract-ocr/5/tessdata/$lang.traineddata" ] || [ -f "/usr/share/tesseract-ocr/4.00/tessdata/$lang.traineddata" ] \
        || need+=("tesseract-ocr-$lang")
done
command -v zbarimg >/dev/null || need+=(zbar-tools)
/usr/bin/python3 -c 'import cairo, gi; gi.require_version("Gtk", "3.0"); gi.require_foreign("cairo")' 2>/dev/null \
    || need+=(python3-gi python3-gi-cairo gir1.2-gtk-3.0)
if [ ${#need[@]} -gt 0 ]; then
    say "Installing: ${need[*]} (needs your password)"
    sudo apt-get install -y "${need[@]}"
fi

# 2. Remove any earlier version first
if pgrep -f "$PATTERN" >/dev/null; then
    [ -x "$BIN" ] && "$BIN" --quit >/dev/null 2>&1 || true
    sleep 1
    pkill -f "$PATTERN" 2>/dev/null || true
fi
rm -rf "$APP_DIR"
rm -f "$BIN" "$DESKTOP_FILE" "$AUTOSTART_FILE" "$NEMO_ACTION"

# 3. Program files
say "Copying program to $APP_DIR"
mkdir -p "$APP_DIR" "$(dirname "$BIN")"
install -m 755 "$SRC_DIR/text_grab.py" "$APP_DIR/text_grab.py"
cat > "$BIN" <<EOS
#!/bin/sh
exec /usr/bin/python3 "$APP_DIR/text_grab.py" "\$@"
EOS
chmod 755 "$BIN"

# 4. Menu entry (clicking it starts a grab) + autostart in the background
mkdir -p "$(dirname "$DESKTOP_FILE")" "$(dirname "$AUTOSTART_FILE")"
cat > "$DESKTOP_FILE" <<EOS
[Desktop Entry]
Type=Application
Name=Text Grab
Comment=Copy the text (or QR code) from anything on screen
Exec=$BIN --grab
Icon=edit-select-all
Terminal=false
Categories=Utility;
Keywords=ocr;text;screenshot;qr;copy;
StartupNotify=false
EOS
cat > "$AUTOSTART_FILE" <<EOS
[Desktop Entry]
Type=Application
Name=Text Grab
Exec=$BIN
Icon=edit-select-all
Terminal=false
X-GNOME-Autostart-enabled=true
EOS

# 5. Right-click an image in Nemo: "Grab text from image"
if command -v nemo >/dev/null 2>&1; then
    mkdir -p "$(dirname "$NEMO_ACTION")"
    cat > "$NEMO_ACTION" <<EOS
[Nemo Action]
Name=Grab text from image
Comment=Copy the text or QR code in this picture
Exec=$BIN --file %F
Icon-Name=edit-select-all
Selection=s
Mimetypes=image/*;
EOS
fi

# 6. Shortcut setting
CONFIG="$HOME/.config/text-grab/settings.ini"
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
kf.set_string("text-grab", "shortcut", shortcut)
kf.save_to_file(path)
PY
fi

# 7. Start it
nohup "$BIN" >/dev/null 2>&1 &
sleep 2
ACTIVE="$("$BIN" --shortcut)"
case "$ACTIVE" in
    none*) warn "No keyboard shortcut active. Add one in Keyboard > Shortcuts > Custom with: $BIN --grab" ;;
    *)     say "Done! Press $ACTIVE and drag a box around any text or QR code." ;;
esac
say "Also: right-click an image in the file manager > Grab text from image"
