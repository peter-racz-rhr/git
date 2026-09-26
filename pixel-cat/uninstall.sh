#!/usr/bin/env bash
# Removes Pixel Cat. Her name and mood are kept unless you pass --forget.
set -uo pipefail
BIN="$HOME/.local/bin/pixel-cat"
[ -x "$BIN" ] && "$BIN" --quit >/dev/null 2>&1
sleep 1
pkill -f '^/usr/bin/python3[.0-9]* [^ ]*/pixel_cat[.]py' 2>/dev/null
rm -rf "$HOME/.local/share/pixel-cat/app" "$HOME/.config/pixel-cat" "$HOME/.cache/pixel-cat"
rm -f "$BIN" "$HOME/.local/share/applications/pixel-cat.desktop" "$HOME/.config/autostart/pixel-cat.desktop"
if [ "${1:-}" = "--forget" ]; then
    rm -rf "$HOME/.local/share/pixel-cat"
    echo "Pixel Cat removed (and forgotten)."
else
    echo "Pixel Cat removed. Her name and mood are kept in ~/.local/share/pixel-cat/cat.json"
fi
