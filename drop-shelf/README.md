# Drop Shelf

A small floating "shelf" for drag & drop on Linux Mint (like Dropover/Yoink on macOS).

When you only have room for one window, drop files on the shelf, switch to the other
window (e.g. Google Drive in your browser) and drag them from the shelf into it.

## Install

```bash
cd drop-shelf
./install.sh
```

That's it. It:
- adds **Drop Shelf** to your menu
- starts it quietly in the background on login
- sets up a keyboard shortcut to show or hide the shelf at your mouse pointer:
  **Super+Z** (Super is the Windows key). If another program already uses it, the app picks
  the next free one (Ctrl+Alt+Z, ...). The installer tells you which one you got, and
  `drop-shelf --shortcut` shows it any time.
- adds **Add to Drop Shelf** to the right-click menu in Nemo (the file manager)

Want a different shortcut? `SHORTCUT='<Super>x' ./install.sh`, or put it in
`~/.config/drop-shelf/settings.ini` under `[shelf]` as `shortcut=<Super>x` and restart the app.

Uninstall with `./uninstall.sh`.

## How to use

1. Press your shortcut (e.g. **Super+Z**). The shelf pops up where your mouse is.
2. Drop anything on it:
   - **files & folders**: only a reference is kept, nothing gets copied
   - **images from a website**: downloaded
   - **links**: saved as a `.url` shortcut (links to images or PDFs get downloaded)
   - **selected text**: saved as a `.txt` file
   - or press **Ctrl+V** to paste copied files, images or text
3. Go to the other window and drag items from the shelf into it.
   Once a drag succeeds, the item leaves the shelf. When the shelf is empty it hides itself.

| Control | What it does |
|---|---|
| Pin button | keep items on the shelf after dragging them out |
| Clear button | empty the shelf (never deletes your files) |
| Menu button | paste, select all, "hide when empty" on/off, quit |
| Close button / Esc | hide the shelf (items stay on it) |
| **Drag all** | drag this button to drag every item at once |
| ◢ corner | resize |
| header | drag it to move the window |
| right-click an item | open, show in folder, copy path, remove |
| double-click | open the file |
| Delete | remove the selected items from the shelf |
| Ctrl+A / Ctrl+C | select all / copy paths |

Other behaviour:
- If you move or delete a file on disk, it drops off the shelf automatically.
- The shelf always starts empty after a restart or reboot.

## Tip

The keyboard shortcut doesn't work *while* you're dragging, because the app you're
dragging from holds the keyboard. So open the shelf first, then start dragging.

## Requirements

Python 3 with GTK 3 (`python3-gi`, `gir1.2-gtk-3.0`). Linux Mint ships both. Works on
Cinnamon, MATE and Xfce.
