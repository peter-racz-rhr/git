# Quick Notes

Post-it notes for Linux Mint. Press **Ctrl+Alt+N**, a small note pops up at your mouse,
write your thought, press **Enter**, and it stays on top of your screen until you delete it.

## Install

```bash
cd quick-notes
./install.sh
```

Running `./install.sh` again always removes the old version first and installs the new one.
Your notes are kept.

The installer:
- adds **Quick Notes** to your menu
- starts it on login, so your notes come back after a restart
- sets up the shortcut **Ctrl+Alt+N**. If another program already uses it, the app picks the
  next free one (Super+N, Ctrl+Alt+J, ...). The installer tells you which one you got, and
  `quick-notes --shortcut` shows it any time.

Want a different shortcut? `SHORTCUT='<Super>n' ./install.sh`, or put it in
`~/.config/quick-notes/settings.ini`:

```ini
[quick-notes]
shortcut=<Primary><Alt>k
```

Prefer to set the shortcut yourself in Mint's Keyboard settings? Install with
`SHORTCUT=none ./install.sh` so the built-in one is off, then add a custom shortcut in
*Keyboard > Shortcuts > Custom Shortcuts* with the command `~/.local/bin/quick-notes --new`
(written out in full, e.g. `/home/you/.local/bin/quick-notes --new`).

Uninstall with `./uninstall.sh`. Your notes are kept unless you run `./uninstall.sh --delete-notes`.

## Using a note

| Control | What it does |
|---|---|
| Ctrl+Alt+N | new note at the mouse pointer |
| Enter | finish the note: it becomes read-only and only the x stays visible |
| Shift+Enter | new line |
| double-click | edit a finished note again |
| **+** | another new note |
| dot button | change color: yellow, pink, green, blue, orange, purple or white. New notes use the color you picked last |
| lock button | show this note on the lock screen (as the lock screen message) |
| **x** | take the note off the screen - it goes to the archive and stays searchable |
| magnifier / **Ctrl+Alt+F** | search all notes, on screen and archived |
| padlock | lock the note with your master password (encrypted) |
| screen button | show this note on the lock screen (not for locked notes) |
| **B** / Ctrl+B | bold |
| *I* / Ctrl+I | italic |
| checkbox / Ctrl+T | turn the line into a checkbox. Click the box to tick it off (works on finished notes too) |
| Shift+Enter in a checkbox list | next line gets a checkbox too. Press it on an empty one to end the list |
| Ctrl+N | new note |
| top bar | drag it to move the note |
| bottom-right corner | resize |

Notes are saved automatically while you type, in `~/.local/share/quick-notes/notes.json`.

## Search and archive

**x** no longer deletes a note: it goes to the archive, where it stays forever. Press
**Ctrl+Alt+F** (or the magnifier on a note) and type anything - a name, "wifi", "+36" - and
matching notes appear as you type, with the match highlighted. Accents don't matter ("cim"
finds "cím"). For each result: **Copy**, **Show** / **Put back on screen**, and for archived
notes **Delete forever** (click twice - this one can't be undone).

## Locked notes (for passwords and other secrets)

Click the **padlock** on a note to lock it. The first time you create a **master password**.

- Locked notes are encrypted (AES-256-GCM, key derived from your password with scrypt).
  The notes file only contains scrambled text for them - finding the file doesn't reveal them.
- On screen a locked note shows only dots and an **Unlock** button. After unlocking it stays
  open for 5 minutes after you last typed in it, then locks itself again.
- The search only looks inside locked notes while they are unlocked.
- **If you forget the master password, locked notes cannot be recovered.** Nobody can open
  them without it - that is the point.
- Needs the `python3-cryptography` package (the installer adds it if it's missing).

A dedicated password manager (Bitwarden, KeePassXC) is still the safest place for important
passwords; locked notes are good for everyday secrets.

## About the lock screen

Linux Mint does not let apps put windows on the lock screen (for security). So a note
with the lock button turned on shows up as the lock screen's message instead, the line of
text under the clock. With several locked notes, they are joined together. Turning the lock
button off (or deleting the note) brings the old message back.

## Requirements

Python 3 with GTK 3 (`python3-gi`, `gir1.2-gtk-3.0`). Linux Mint ships both.
