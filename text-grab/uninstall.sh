#!/usr/bin/env bash
# Removes Text Grab (the Tesseract / zbar packages stay installed).
set -uo pipefail
BIN="$HOME/.local/bin/text-grab"
PATTERN='^/usr/bin/python3[.0-9]* [^ ]*/text_grab[.]py'
pgrep -f "$PATTERN" >/dev/null && { [ -x "$BIN" ] && "$BIN" --quit >/dev/null 2>&1; sleep 1; pkill -f "$PATTERN"; }
rm -rf "$HOME/.local/share/text-grab" "$HOME/.cache/text-grab" "$HOME/.config/text-grab"
rm -f "$BIN" "$HOME/.local/share/applications/text-grab.desktop" "$HOME/.config/autostart/text-grab.desktop" \
      "$HOME/.local/share/nemo/actions/text-grab.nemo_action"
echo "Text Grab removed."
