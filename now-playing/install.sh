#!/usr/bin/env bash
# Installs Now Playing (Spotify widget) for the current user.
#
#   ./install.sh               install (or update - the old version is removed first)
#   AUTOSTART=1 ./install.sh   also start the widget automatically when you log in
set -euo pipefail

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$HOME/.local/share/now-playing"
BIN="$HOME/.local/bin/now-playing"
DESKTOP_FILE="$HOME/.local/share/applications/now-playing.desktop"
AUTOSTART_FILE="$HOME/.config/autostart/now-playing.desktop"
PATTERN='^/usr/bin/python3[.0-9]* [^ ]*/now_playing[.]py'

say() { printf '\033[1;32m==>\033[0m %s\n' "$*"; }

# 1. Dependencies (preinstalled on Linux Mint, but just in case)
if ! /usr/bin/python3 -c 'import cairo, gi; gi.require_version("Gtk", "3.0"); gi.require_foreign("cairo"); from gi.repository import Gtk' 2>/dev/null; then
    say "Installing GTK and cairo bindings for Python (needs your password)"
    sudo apt-get install -y python3-gi python3-gi-cairo gir1.2-gtk-3.0
fi

# 2. Remove any earlier version first (your Spotify login and settings are kept)
if pgrep -f "$PATTERN" >/dev/null; then
    say "Stopping the running widget"
    [ -x "$BIN" ] && "$BIN" --quit >/dev/null 2>&1 || true
    sleep 1
    pkill -f "$PATTERN" 2>/dev/null || true
fi
rm -rf "$APP_DIR"
rm -f "$BIN" "$DESKTOP_FILE" "$AUTOSTART_FILE"

# 3. Program files
say "Copying program to $APP_DIR"
mkdir -p "$APP_DIR" "$(dirname "$BIN")"
install -m 755 "$SRC_DIR/now_playing.py" "$APP_DIR/now_playing.py"
cat > "$BIN" <<EOS
#!/bin/sh
exec /usr/bin/python3 "$APP_DIR/now_playing.py" "\$@"
EOS
chmod 755 "$BIN"

# 4. Menu entry
say "Adding menu entry"
mkdir -p "$(dirname "$DESKTOP_FILE")"
cat > "$DESKTOP_FILE" <<EOS
[Desktop Entry]
Type=Application
Name=Now Playing
Comment=Spotify widget with queue and typing lyrics
Exec=$BIN
Icon=multimedia-audio-player
Terminal=false
Categories=AudioVideo;Audio;Player;
Keywords=spotify;music;lyrics;queue;widget;
StartupNotify=false
EOS

# 5. Optional autostart
if [ "${AUTOSTART:-0}" = 1 ]; then
    say "Enabling autostart"
    mkdir -p "$(dirname "$AUTOSTART_FILE")"
    sed 's/^Exec=.*/Exec='"${BIN//\//\\/}"'/' "$DESKTOP_FILE" > "$AUTOSTART_FILE"
fi

# 6. Start it
nohup "$BIN" >/dev/null 2>&1 &

say "Done! Now Playing is open. You also find it in the menu."
say "Click the gear button once to connect your Spotify account (for the queue)."
say "Keyboard shortcut command (Keyboard > Shortcuts > Custom): $BIN --toggle"
