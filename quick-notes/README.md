# Quick Notes

Post-it notes for Linux Mint. Press **Ctrl+Alt+N**, a small note pops up at your mouse,
write your thought, and it stays on top of your screen until you delete it.

## Install

```bash
cd quick-notes
./install.sh
```

The installer:
- adds **Quick Notes** to your menu
- starts it on login, so your notes come back after a restart
- sets the shortcut **Ctrl+Alt+N**. If that one is already taken on your computer, it picks
  a free one and tells you which.

Uninstall with `./uninstall.sh`. Your notes are kept unless you run `./uninstall.sh --delete-notes`.

## Using a note

| Control | What it does |
|---|---|
| Ctrl+Alt+N | new note at the mouse pointer |
| **+** | another new note |
| dot button | change color: yellow, pink, green, blue, orange, purple or white. New notes use the color you picked last |
| lock button | show this note on the lock screen (as the lock screen message) |
| **x** | delete the note right away |
| **B** / Ctrl+B | bold |
| *I* / Ctrl+I | italic |
| checkbox / Ctrl+T | turn the line into a checkbox. Click the box to tick it off |
| Enter in a checkbox list | next line gets a checkbox too. Press Enter on an empty one to end the list |
| Ctrl+N | new note |
| top bar | drag it to move the note |
| bottom-right corner | resize |

Notes are saved automatically while you type, in `~/.local/share/quick-notes/notes.json`.

## About the lock screen

Linux Mint does not let apps put windows on the lock screen (for security). So a note
with the lock button turned on shows up as the lock screen's message instead, the line of
text under the clock. With several locked notes, they are joined together. Turning the lock
button off (or deleting the note) brings the old message back.

## Requirements

Python 3 with GTK 3 (`python3-gi`, `gir1.2-gtk-3.0`). Linux Mint ships both.
