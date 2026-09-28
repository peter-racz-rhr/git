#!/usr/bin/env bash
# Removes Now Playing. Pass --forget to also delete your Spotify login and settings.
set -uo pipefail
BIN="$HOME/.local/bin/now-playing"
PATTERN='^/usr/bin/python3[.0-9]* [^ ]*/now_playing[.]py'
pgrep -f "$PATTERN" >/dev/null && { [ -x "$BIN" ] && "$BIN" --quit >/dev/null 2>&1; sleep 1; pkill -f "$PATTERN"; }
rm -rf "$HOME/.local/share/now-playing" "$HOME/.cache/now-playing"
rm -f "$BIN" "$HOME/.local/share/applications/now-playing.desktop" "$HOME/.config/autostart/now-playing.desktop"
if [ "${1:-}" = "--forget" ]; then
    rm -rf "$HOME/.config/now-playing"
    echo "Spotify login and settings deleted."
fi
echo "Now Playing removed."
