#!/usr/bin/env bash
# Removes Mail Brief. Pass --forget to also delete your settings, password, key and summaries.
set -uo pipefail
BIN="$HOME/.local/bin/mail-brief"
PATTERN='^/usr/bin/python3[.0-9]* [^ ]*/mail_brief[.]py'
pgrep -f "$PATTERN" >/dev/null && { [ -x "$BIN" ] && "$BIN" --quit >/dev/null 2>&1; sleep 1; pkill -f "$PATTERN"; }
rm -rf "$HOME/.local/share/mail-brief/app"
rm -f "$BIN" "$HOME/.local/share/applications/mail-brief.desktop" "$HOME/.config/autostart/mail-brief.desktop"
if [ "${1:-}" = "--forget" ]; then
    rm -rf "$HOME/.config/mail-brief" "$HOME/.local/share/mail-brief"
    echo "Settings, password, key and summaries deleted."
fi
echo "Mail Brief removed."
