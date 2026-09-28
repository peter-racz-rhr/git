#!/usr/bin/env bash
# Installs Mail Brief for the current user.
#
#   ./install.sh               install (or update - the old version is removed first)
#   AUTOSTART=0 ./install.sh   don't start it automatically at login
set -euo pipefail

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$HOME/.local/share/mail-brief/app"
BIN="$HOME/.local/bin/mail-brief"
DESKTOP_FILE="$HOME/.local/share/applications/mail-brief.desktop"
AUTOSTART_FILE="$HOME/.config/autostart/mail-brief.desktop"
PATTERN='^/usr/bin/python3[.0-9]* [^ ]*/mail_brief[.]py'

say() { printf '\033[1;32m==>\033[0m %s\n' "$*"; }

# 1. Dependencies (preinstalled on Linux Mint, but just in case)
if ! /usr/bin/python3 -c 'import gi; gi.require_version("Gtk", "3.0"); from gi.repository import Gtk' 2>/dev/null; then
    say "Installing GTK bindings for Python (needs your password)"
    sudo apt-get install -y python3-gi gir1.2-gtk-3.0
fi

# 2. Remove any earlier version first (settings and summaries are kept)
if pgrep -f "$PATTERN" >/dev/null; then
    say "Stopping the running Mail Brief"
    [ -x "$BIN" ] && "$BIN" --quit >/dev/null 2>&1 || true
    sleep 1
    pkill -f "$PATTERN" 2>/dev/null || true
fi
rm -rf "$APP_DIR"
rm -f "$BIN" "$DESKTOP_FILE" "$AUTOSTART_FILE"

# 3. Program files
say "Copying program to $APP_DIR"
mkdir -p "$APP_DIR" "$(dirname "$BIN")"
install -m 755 "$SRC_DIR/mail_brief.py" "$APP_DIR/mail_brief.py"
cat > "$BIN" <<EOS
#!/bin/sh
exec /usr/bin/python3 "$APP_DIR/mail_brief.py" "\$@"
EOS
chmod 755 "$BIN"

# 4. Menu entry
say "Adding menu entry"
mkdir -p "$(dirname "$DESKTOP_FILE")"
cat > "$DESKTOP_FILE" <<EOS
[Desktop Entry]
Type=Application
Name=Mail Brief
Comment=Short AI summaries of your unread Gmail
Exec=$BIN
Icon=mail-unread
Terminal=false
Categories=Network;Email;
Keywords=mail;gmail;email;summary;inbox;
StartupNotify=false
EOS

# 5. Autostart (on by default: the line is meant to always be there)
if [ "${AUTOSTART:-1}" = 1 ]; then
    say "Enabling autostart"
    mkdir -p "$(dirname "$AUTOSTART_FILE")"
    cp "$DESKTOP_FILE" "$AUTOSTART_FILE"
fi

# 6. Start it
nohup "$BIN" >/dev/null 2>&1 &

say "Done! Mail Brief is open. The first time, the settings window asks for your"
say "Gmail address, a Gmail app password and a free Groq API key (see README.md)."
