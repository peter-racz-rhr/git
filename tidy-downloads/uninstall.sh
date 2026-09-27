#!/usr/bin/env bash
# Removes Tidy Downloads. Your sorted files stay where they are.
set -uo pipefail
BIN="$HOME/.local/bin/tidy-downloads"
[ -x "$BIN" ] && "$BIN" --quit >/dev/null 2>&1
sleep 1
pkill -f '^/usr/bin/python3[.0-9]* [^ ]*/tidy_downloads[.]py' 2>/dev/null
rm -rf "$HOME/.local/share/tidy-downloads" "$HOME/.config/tidy-downloads"
rm -f "$BIN" "$HOME/.local/share/applications/tidy-downloads.desktop" \
      "$HOME/.config/autostart/tidy-downloads.desktop" \
      "$HOME/.local/share/nemo/actions/tidy-downloads.nemo_action"
echo "Tidy Downloads removed. Your files stay in their folders inside Downloads."
