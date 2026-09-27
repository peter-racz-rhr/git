#!/usr/bin/env bash
# Installs Tidy Downloads for the current user.
#
#   ./install.sh                  install (or update - the old version is removed first)
#   TIDY_EXISTING=no ./install.sh leave the files that are already in Downloads alone
set -euo pipefail

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$HOME/.local/share/tidy-downloads/app"
BIN="$HOME/.local/bin/tidy-downloads"
DESKTOP_FILE="$HOME/.local/share/applications/tidy-downloads.desktop"
AUTOSTART_FILE="$HOME/.config/autostart/tidy-downloads.desktop"
NEMO_ACTION="$HOME/.local/share/nemo/actions/tidy-downloads.nemo_action"
CONFIG="$HOME/.config/tidy-downloads/settings.ini"
PATTERN='^/usr/bin/python3[.0-9]* [^ ]*/tidy_downloads[.]py'

say() { printf '\033[1;32m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m %s\n' "$*"; }

# 1. GTK (preinstalled on Linux Mint)
if ! /usr/bin/python3 -c 'import gi; gi.require_version("Gtk", "3.0")' 2>/dev/null; then
    say "Installing python3-gi and GTK (needs your password)"
    sudo apt-get install -y python3-gi gir1.2-gtk-3.0
fi

# 2. Remove any earlier version first (settings and undo history are kept)
FIRST_INSTALL=yes
[ -f "$CONFIG" ] && FIRST_INSTALL=no
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
install -m 755 "$SRC_DIR/tidy_downloads.py" "$APP_DIR/tidy_downloads.py"
cat > "$BIN" <<EOS
#!/bin/sh
exec /usr/bin/python3 "$APP_DIR/tidy_downloads.py" "\$@"
EOS
chmod 755 "$BIN"

# 4. Menu entry (opens the window) + autostart in the background
mkdir -p "$(dirname "$DESKTOP_FILE")" "$(dirname "$AUTOSTART_FILE")"
cat > "$DESKTOP_FILE" <<EOS
[Desktop Entry]
Type=Application
Name=Tidy Downloads
Comment=Sorts your Downloads folder by itself
Exec=$BIN --window
Icon=folder-download
Terminal=false
Categories=Utility;
Keywords=downloads;tidy;sort;clean;organize;
StartupNotify=false
EOS
cat > "$AUTOSTART_FILE" <<EOS
[Desktop Entry]
Type=Application
Name=Tidy Downloads
Exec=$BIN
Icon=folder-download
Terminal=false
X-GNOME-Autostart-enabled=true
X-GNOME-Autostart-Delay=20
EOS

# 5. Right-click in the file manager: "Tidy Downloads now"
if command -v nemo >/dev/null 2>&1; then
    mkdir -p "$(dirname "$NEMO_ACTION")"
    cat > "$NEMO_ACTION" <<EOS
[Nemo Action]
Name=Tidy Downloads now
Comment=Sort everything in Downloads into folders right away
Exec=$BIN --now
Icon-Name=folder-download
Selection=None
Extensions=any;
EOS
fi

# 6. First time: tidy what's already in Downloads, or only new downloads?
if [ "$FIRST_INSTALL" = yes ]; then
    answer="${TIDY_EXISTING:-}"
    if [ -z "$answer" ] && [ -t 0 ]; then
        read -r -p "Also tidy the files that are already in your Downloads folder? (you can undo it) [Y/n] " answer
    fi
    mkdir -p "$(dirname "$CONFIG")"
    case "${answer,,}" in
        n|no) printf '[tidy]\nsince=%s\n' "$(date +%s)" > "$CONFIG"
              say "OK: only new downloads will be sorted." ;;
        *)    printf '[tidy]\nsince=0\n' > "$CONFIG"
              say "Your current downloads will be sorted in about a minute." ;;
    esac
fi

# 7. Start it
nohup "$BIN" >/dev/null 2>&1 &
say "Done! New downloads are sorted into folders 15 minutes after they arrive."
say "Open Tidy Downloads from the menu to see it, tidy now or undo."
